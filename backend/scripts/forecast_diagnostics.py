"""Autoforecast quality diagnostics for a cohort.

Scores the AUTOFORECAST (not the persisted, possibly hand-edited rows)
against each well's actual production, per stream, to surface systematic
biases like low-qi picks and too-shallow Di.

For every well it re-runs ``forecast_well(persist=False)`` so the metrics
reflect what the auto-fitter produces today, then compares against the
downtime-filtered post-peak actuals:

  * qi_capture   = fitted qi / observed peak rate         (<1 ⇒ qi underfit)
  * Di adequacy  = fitted yr-1 effective decline vs an EMPIRICAL yr-1
                   decline (log-linear slope of the actual post-peak
                   rates over the first ~2 yrs). fit < empirical ⇒ too shallow.
  * cum ratio    = model cum to end-of-history / actual cum to date
  * holdout backtest = fit on the first ~60% of post-peak months, predict
                   the rest, report median signed error (over/under).
  * bound / fallback rates.

Usage:  python -m scripts.forecast_diagnostics <type_curve_name> [stream...]
"""

from __future__ import annotations

import sys

import numpy as np
from sqlalchemy import text

from app.db.session import SessionLocal
from app.forecasting.fit import (
    STREAM_DOWNTIME_FLOOR_FIELD,
    STREAM_RATE_COLUMN,
    STREAM_VOLUME_COLUMN,
    _post_peak_slice,
    fit_with_fallback,
)
from app.forecasting.metrics import effective_decline_first_year
from app.forecasting.orchestrator import (
    _load_monthly,
    detect_stream_peaks,
    forecast_well,
)
from app.forecasting.ramp_arps import evaluate_well_rate
from app.forecasting.types import ForecastConfig

STREAMS = ("oil", "gas", "water")


def _empirical_yr1_decline(rates: list[float], t_years: list[float]) -> float | None:
    """SECANT yr-1 effective decline measured straight off the actuals:
    1 - q(1yr)/q(0), exactly how effective decline is defined (so it's
    comparable to effective_decline_first_year of the fit). q(0) is the
    smoothed peak (median of the first ~3 post-peak months) and q(1yr) the
    smoothed rate ~12 months later (median of months ~10-14). Returns None
    when either window lacks positive points. A log-linear slope over 2 yrs
    understates a front-loaded hyperbolic, which is why it read too low."""
    early = [r for tt, r in zip(t_years, rates, strict=False) if r and r > 0 and tt <= 0.30]
    yr1 = [r for tt, r in zip(t_years, rates, strict=False) if r and r > 0 and 0.85 <= tt <= 1.20]
    if not early or not yr1:
        return None
    q0 = float(np.median(early))
    q12 = float(np.median(yr1))
    if q0 <= 0:
        return None
    return float(max(0.0, min(0.999, 1.0 - q12 / q0)))


def _holdout_bias(monthly, peak, stream, cfg) -> float | None:
    """Fit on the first ~60% of post-peak months, predict the rest, return
    median signed relative error (pred-actual)/actual over the held-out
    tail. Positive ⇒ over-predicts the tail; negative ⇒ under-predicts."""
    rate_col = STREAM_RATE_COLUMN[stream]
    vol_col = STREAM_VOLUME_COLUMN[stream]
    floor = getattr(cfg, STREAM_DOWNTIME_FLOOR_FIELD[stream])
    sl, _ = _post_peak_slice(monthly, peak, rate_col, vol_col, downtime_floor=floor)
    n = len(sl)
    if n < 18:
        return None
    k = max(8, int(round(n * 0.6)))
    if k >= n - 3:
        return None
    # Truncate the FULL monthly frame to peak + k post-peak months, re-fit.
    train_rows = peak.peak_index + k
    train_monthly = monthly.sort_values("prod_date").reset_index(drop=True).iloc[:train_rows]
    try:
        r = fit_with_fallback(train_monthly, peak=peak, stream=stream, config=cfg)
    except Exception:
        return None
    test = sl.iloc[k:]
    t_test = test["t_years"].to_numpy(dtype=float)
    pred = evaluate_well_rate(
        qo=r.qi,
        peak_index_months=None,
        qi=r.qi,
        Di=r.di_initial,
        b=r.b if r.b is not None else 1.0,
        Df=r.df_terminal or 0.08,
        t_years=t_test,
    )
    actual = test["rate"].to_numpy(dtype=float)
    mask = actual > 0
    if mask.sum() < 3:
        return None
    rel = (pred[mask] - actual[mask]) / actual[mask]
    return float(np.median(rel))


def diagnose(tc_name: str, streams: tuple[str, ...], cfg: ForecastConfig | None = None) -> None:
    cfg = cfg or ForecastConfig()
    with SessionLocal() as s:
        tc = s.execute(
            text("SELECT included_api10s FROM type_curves WHERE name = :n"),
            {"n": tc_name},
        ).first()
        if tc is None or not tc.included_api10s:
            print(f"type curve {tc_name!r} not found / empty")
            return
        api10s = list(tc.included_api10s)
        print(f"cohort {tc_name!r}: {len(api10s)} wells | streams={streams}\n")

        recs: dict[str, list[dict]] = {st: [] for st in streams}

        for api10 in api10s:
            monthly = _load_monthly(s, api10)
            if monthly.empty:
                continue
            peaks = detect_stream_peaks(monthly)
            res = forecast_well(s, api10, persist=False, config=cfg)
            for st in streams:
                r = res.get(st)
                peak = peaks.get(st)
                if r is None or peak is None or not peak.peak_rate:
                    continue
                de_fit = effective_decline_first_year(r.di_initial, r.b)
                rate_col = STREAM_RATE_COLUMN[st]
                vol_col = STREAM_VOLUME_COLUMN[st]
                floor = getattr(cfg, STREAM_DOWNTIME_FLOOR_FIELD[st])
                sl, _ = _post_peak_slice(monthly, peak, rate_col, vol_col, downtime_floor=floor)
                de_emp = _empirical_yr1_decline(sl["rate"].tolist(), sl["t_years"].tolist())
                cum_ratio = None
                actual_cum = float(sl["cum_vol"].iloc[-1]) if len(sl) else 0.0
                if actual_cum > 0 and len(sl):
                    pred = evaluate_well_rate(
                        qo=r.qi,
                        peak_index_months=None,
                        qi=r.qi,
                        Di=r.di_initial,
                        b=r.b or 1.0,
                        Df=r.df_terminal or 0.08,
                        t_years=sl["t_years"].to_numpy(dtype=float),
                    )
                    model_cum = float(
                        np.trapezoid(pred, sl["t_years"].to_numpy(dtype=float)) * 365.0
                    )
                    cum_ratio = model_cum / actual_cum
                recs[st].append(
                    {
                        "api10": api10,
                        "qi_cap": r.qi / peak.peak_rate,
                        "de_fit": de_fit,
                        "de_emp": de_emp,
                        "di_gap": (de_fit - de_emp) if de_emp is not None else None,
                        "cum_ratio": cum_ratio,
                        "bias": _holdout_bias(monthly, peak, st, cfg),
                        "fallback": r.fit_method == "rate_time_fallback",
                        "di_lo": r.di_initial is not None and r.di_initial <= 0.51,
                    }
                )

        def pct(xs, q):
            xs = [x for x in xs if x is not None]
            return float(np.percentile(xs, q)) if xs else float("nan")

        def med(xs):
            xs = [x for x in xs if x is not None]
            return float(np.median(xs)) if xs else float("nan")

        for st in streams:
            rs = recs[st]
            n = len(rs)
            if n == 0:
                print(f"== {st.upper()} ==  (no fits)\n")
                continue
            qc = [x["qi_cap"] for x in rs]
            gaps = [x["di_gap"] for x in rs if x["di_gap"] is not None]
            print(f"== {st.upper()} ==  n={n}")
            print(
                f"  qi_capture: p25={pct(qc, 25):.2f} p50={pct(qc, 50):.2f} p75={pct(qc, 75):.2f}"
                f" | share<0.80: {100 * sum(v < 0.8 for v in qc) // n}%"
            )
            print(
                f"  yr1 decline fit p50={pct([x['de_fit'] for x in rs], 50) * 100:.0f}%"
                f" empirical p50={pct([x['de_emp'] for x in rs], 50) * 100:.0f}%"
                f" | shallow>10pp: {100 * sum(g < -0.10 for g in gaps) // max(len(gaps), 1)}%"
            )
            print(
                f"  cum-to-date p50={pct([x['cum_ratio'] for x in rs], 50):.2f}"
                f" | holdout tail bias p50={pct([x['bias'] for x in rs], 50) * 100:+.0f}%"
                f" | rate_time_fallback: {100 * sum(x['fallback'] for x in rs) // n}%"
            )

            # ---- Coupling cut: are low-qi wells the same as shallow-Di /
            # worst-under-predicting wells? (the cum-degeneracy hypothesis) ----
            low = [x for x in rs if x["qi_cap"] < 0.8]
            ok = [x for x in rs if x["qi_cap"] >= 0.8]
            print("  coupling (split on qi_capture):")
            for label, grp in (("qi<0.80", low), ("qi>=0.80", ok)):
                if not grp:
                    continue
                g = [x["di_gap"] for x in grp if x["di_gap"] is not None]
                print(
                    f"    {label} n={len(grp):>2}: di_gap p50={med(g) * 100:+.0f}pp"
                    f" shallow>10pp={100 * sum(v < -0.1 for v in g) // max(len(g), 1)}%"
                    f" | cum p50={med([x['cum_ratio'] for x in grp]):.2f}"
                    f" | tail bias p50={med([x['bias'] for x in grp]) * 100:+.0f}%"
                )
            pairs = [(x["qi_cap"], x["di_gap"]) for x in rs if x["di_gap"] is not None]
            if len(pairs) > 5:
                c = float(np.corrcoef([p[0] for p in pairs], [p[1] for p in pairs])[0, 1])
                print(
                    f"    corr(qi_capture, di_gap) = {c:+.2f}"
                    f"  (>0 ⇒ low qi travels with shallow Di — coupled)"
                )
            print()


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "braveheart_wca"
    sts = tuple(sys.argv[2:]) or STREAMS
    diagnose(name, sts)
