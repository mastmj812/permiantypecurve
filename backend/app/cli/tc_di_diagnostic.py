"""Decompose the Review-tab vs Type-Curve-tab Di gap for one cohort.

The Review tab's "DI (1-yr effective) — median" is the *median across
wells* of each well's own first-year effective decline (median-of-fits).
The Type Curve tab's Di is the first-year effective decline of a *single
Arps curve fit to the P50* of the lateral-normalized forecast rates
(fit-of-the-median). Those are different statistics; on a shape-dispersed
cohort the fit-of-median reads flatter (lower Di) than the median well.

This tool prints, per stream, both numbers side by side for a saved type
curve, plus a Shapley decomposition of the gap into:
  * "Di flattening" — the aggregate trajectory declines less steeply than
    the typical well, so the fitted *nominal* Di is lower.
  * "b creep"       — superposing hyperbolics of varying Di yields a
    heavier-tailed aggregate, so the fit lands on a higher b; at equal
    nominal Di a higher b means a lower first-year effective decline.

Both paths are resolved override → global, exactly as the two tabs do, so
the printed median and fitted numbers match what you see in the UI.

The cohort can be identified two ways:

  * ``--name`` — a SAVED type curve. Uses its stored membership, basis,
    alignment, AND its per-curve forecast_overrides JSONB.
  * ``--api10s`` / ``--api10s-file`` — an AD-HOC cohort that hasn't been
    saved yet (e.g. the live Review→TypeCurve preview). There's no
    per-curve override JSONB in that case, so resolution falls back to the
    ``forecasts`` table globals — which already include the per-well edits
    you saved from the detail modal (those persist as manual_override
    rows). Basis/alignment come from ``--basis`` / ``--alignment``.

Usage:

    # Saved cohort by name:
    python -m app.cli.tc_di_diagnostic --name braveheart_bs2

    # Unsaved cohort — paste the 31 api10s (or point at a file, one/line):
    python -m app.cli.tc_di_diagnostic --api10s 42301...,42389... \
        --basis per_lateral_ft --alignment peak_ramp
    python -m app.cli.tc_di_diagnostic --api10s-file cohort.txt

When several versions share a name, the most recent (by created_at) is
used; pass --version-id to pin an exact row.
"""

from __future__ import annotations

import argparse
import sys
import uuid

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import configure_logging
from app.db.models import Forecast, Stream, TypeCurve
from app.db.session import SessionLocal
from app.forecasting.metrics import effective_decline_first_year
from app.type_curves.aggregate import aggregate
from app.type_curves.fit_p50 import fit_p50_series
from app.type_curves.loader import _resolve_params, load_wells_with_forecast

_STREAMS: tuple[Stream, ...] = (Stream.OIL, Stream.GAS, Stream.WATER)
_FORECAST_N_MONTHS = 600


def _empty_tc_for_resolver() -> TypeCurve:
    """Stub TC carrying empty per-curve overrides — for the unsaved-cohort
    path. Mirrors api.type_curves._empty_tc_for_resolver so resolution
    falls through to the forecasts-table globals."""
    tc = TypeCurve()
    tc.forecast_overrides = {}
    return tc


def _collect_api10s(
    raw: list[str] | None, path: str | None
) -> list[str] | None:
    """Flatten --api10s (CSV/repeated) + --api10s-file into a de-duped,
    order-preserving list. None when neither is supplied."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(piece: str) -> None:
        piece = piece.strip()
        if piece and piece not in seen:
            seen.add(piece)
            out.append(piece)

    for chunk in raw or []:
        for piece in chunk.split(","):
            _add(piece)
    if path:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                _add(line)
    return out or None


def _load_type_curve(
    session: Session, *, name: str, version_id: str | None
) -> TypeCurve | None:
    if version_id is not None:
        return session.get(TypeCurve, uuid.UUID(version_id))
    rows = (
        session.execute(
            select(TypeCurve)
            .where(TypeCurve.name == name)
            .order_by(TypeCurve.created_at.desc())
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    if len(rows) > 1:
        print(
            f"note: {len(rows)} type curves named {name!r}; using the most "
            f"recent (created_at={rows[0].created_at}, id={rows[0].id}). "
            f"Pass --version-id to pin a different one."
        )
    return rows[0]


def _global_forecasts(
    session: Session, api10s: list[str]
) -> dict[tuple[str, Stream], Forecast]:
    """{(api10, stream): Forecast} for the cohort — the override fallback,
    same source the loader's resolver reads from."""
    rows = (
        session.execute(select(Forecast).where(Forecast.api10.in_(api10s)))
        .scalars()
        .all()
    )
    return {(f.api10, f.stream): f for f in rows}


def _median_well_stats(
    tc: TypeCurve,
    api10s: list[str],
    globals_by: dict[tuple[str, Stream], Forecast],
    stream: Stream,
) -> dict[str, float | int] | None:
    """Per-well resolved (Di, b) → first-year effective, medianed across
    wells. Mirrors the Review tab's diMedians: median of per-well effective
    declines. Component-wise medians of Di and b are reported too as a
    representative central well for the decomposition.
    """
    di_vals: list[float] = []
    b_vals: list[float] = []
    eff_vals: list[float] = []
    for api10 in api10s:
        params = _resolve_params(tc, api10, stream, globals_by.get((api10, stream)))
        if params is None:
            continue
        di, b = params["Di"], params["b"]
        di_vals.append(di)
        b_vals.append(b)
        eff_vals.append(effective_decline_first_year(di, b))
    if not eff_vals:
        return None
    return {
        "n": len(eff_vals),
        "di_med": float(np.median(di_vals)),
        "b_med": float(np.median(b_vals)),
        # Headline matches the Review tab: median of the per-well effectives.
        "eff_med": float(np.median(eff_vals)),
    }


def _shapley_split(
    di_m: float, b_m: float, di_f: float, b_f: float
) -> tuple[float, float]:
    """Order-independent (Shapley) attribution of the effective-decline gap
    eff(di_f, b_f) - eff(di_m, b_m) to the Di change and the b change.

    Two factors → average over both orderings. The two contributions sum
    exactly to the total gap.
    """

    def e(di: float, b: float) -> float:
        return effective_decline_first_year(di, b)

    # Di's marginal effect, averaged over "b at median" and "b at fit".
    di_contrib = 0.5 * ((e(di_f, b_m) - e(di_m, b_m)) + (e(di_f, b_f) - e(di_m, b_f)))
    # b's marginal effect, averaged over "Di at median" and "Di at fit".
    b_contrib = 0.5 * ((e(di_m, b_f) - e(di_m, b_m)) + (e(di_f, b_f) - e(di_f, b_m)))
    return di_contrib, b_contrib


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Decompose the Review-vs-TypeCurve Di gap for a cohort."
    )
    parser.add_argument(
        "--name", default=None, help="Name of a SAVED type curve to diagnose."
    )
    parser.add_argument(
        "--version-id", default=None, help="Pin an exact type_curves.id (UUID)."
    )
    parser.add_argument(
        "--api10s",
        action="append",
        default=None,
        help="Comma-separated api10 list (or repeated) for an UNSAVED cohort.",
    )
    parser.add_argument(
        "--api10s-file",
        default=None,
        help="File of api10s, one per line, for an UNSAVED cohort.",
    )
    parser.add_argument("--basis", default="per_lateral_ft")
    # Match the API/aggregation default (peak_ramp is the alignment of
    # record). An unsaved-cohort diagnostic that omits --alignment should
    # reproduce what the app aggregates, not the legacy first_prod_month.
    parser.add_argument("--alignment", default="peak_ramp")
    parser.add_argument("--log-level", default="WARNING")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    ad_hoc_api10s = _collect_api10s(args.api10s, args.api10s_file)
    if args.name is None and ad_hoc_api10s is None:
        print(
            "Provide either --name (a saved type curve) or --api10s / "
            "--api10s-file (an unsaved cohort).",
            file=sys.stderr,
        )
        return 2

    with SessionLocal() as session:
        if args.name is not None:
            tc = _load_type_curve(session, name=args.name, version_id=args.version_id)
            if tc is None:
                print(f"No type curve named {args.name!r} found.", file=sys.stderr)
                return 1
            api10s = list(tc.included_api10s)
            basis = tc.normalization_basis.value
            alignment = tc.alignment_method.value
            label = f"saved type curve {tc.name!r} (id={tc.id})"
        else:
            # Unsaved cohort: stub TC with empty overrides → resolution
            # falls back to the forecasts-table globals (which include the
            # detail-modal edits saved during review).
            tc = _empty_tc_for_resolver()
            api10s = ad_hoc_api10s  # type: ignore[assignment]
            basis = args.basis
            alignment = args.alignment
            label = f"ad-hoc cohort ({len(api10s)} api10s, unsaved)"

        n_overrides = len(tc.forecast_overrides or {})
        print(
            f"Cohort: {label}\n"
            f"  wells={len(api10s)}  basis={basis}  alignment={alignment}  "
            f"per_curve_overridden_wells={n_overrides}\n"
        )

        globals_by = _global_forecasts(session, api10s)

        # --- fit-of-the-median path (the Type Curve tab) ---
        forecast_wells = load_wells_with_forecast(
            session, tc, api10s, alignment=alignment, n_months=_FORECAST_N_MONTHS
        )
        agg = aggregate(
            forecast_wells,
            normalization_basis=basis,
            alignment_method=alignment,
            n_months=_FORECAST_N_MONTHS,
        )

        header = (
            f"{'stream':<6} {'n':>3} | "
            f"{'MEDIAN WELL (Review)':^26} | {'P50 FIT (Type Curve)':^26} | "
            f"{'gap':>7} = {'Di flat':>8} + {'b creep':>8}"
        )
        print(header)
        print(
            f"{'':<6} {'':>3} | "
            f"{'Di_nom   b      eff':^26} | {'Di_nom   b      eff':^26} | "
            f"{'(eff)':>7}   {'(pts)':>8}   {'(pts)':>8}"
        )
        print("-" * len(header))

        for stream in _STREAMS:
            med = _median_well_stats(tc, api10s, globals_by, stream)
            fitted = fit_p50_series(agg.streams[stream.value].p50)

            if med is None or fitted is None:
                reason = "no per-well fits" if med is None else "P50 fit failed"
                print(f"{stream.value:<6} {'-':>3} | {reason}")
                continue

            di_m, b_m, eff_m = med["di_med"], med["b_med"], med["eff_med"]
            di_f, b_f = fitted["Di"], fitted["b"]
            eff_f = effective_decline_first_year(di_f, b_f)

            gap = eff_f - eff_m
            di_contrib, b_contrib = _shapley_split(di_m, b_m, di_f, b_f)

            print(
                f"{stream.value:<6} {med['n']:>3} | "
                f"{di_m:>6.2f}  {b_m:>4.2f}  {_pct(eff_m):>6} | "
                f"{di_f:>6.2f}  {b_f:>4.2f}  {_pct(eff_f):>6} | "
                f"{gap * 100:>+6.1f}   {di_contrib * 100:>+7.1f}   "
                f"{b_contrib * 100:>+7.1f}"
            )

        print(
            "\nReading it: 'gap' is TypeCurve eff − Review-median eff (negative "
            "= TC reads flatter).\n'Di flat' is how much of that gap comes from "
            "a lower fitted nominal Di;\n'b creep' is how much comes from the "
            "fit landing on a higher b. They sum to the gap."
        )

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
