"""Build the bench-level b priors used to regularize short-history fits.

READ-ONLY against anduin's own DB; the only thing written is the JSON
file. Review the diff and commit it — fits read the committed file, never
the DB (see app.forecasting.b_prior for the method and the thresholds).

    docker compose exec backend python -m app.cli.build_b_priors   # ~12 min

Re-run after a material change to the synced well set, the fit bounds,
the peak anchor, or the downtime filter — all of which shape the pooled
fit. Refit short-history wells afterwards; persisted rows keep the prior
they were fit with (it is recorded in forecasts.diagnostics).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from app.db.models import Well
from app.db.session import SessionLocal
from app.forecasting.b_prior import (
    MIN_FIT_MONTHS,
    MIN_LATERAL_FT,
    MIN_MONTH_COVERAGE,
    MIN_VINTAGE_YEAR,
    MIN_WELLS,
    PRIORS_PATH,
    UNMAPPED,
    bench_key,
    fit_pooled_b,
    pooled_decline_curve,
)
from app.forecasting.fit import (
    STREAM_DOWNTIME_FLOOR_FIELD,
    STREAM_RATE_COLUMN,
    STREAM_VOLUME_COLUMN,
    _post_peak_slice,
)
from app.forecasting.orchestrator import (
    STREAMS,
    _load_monthly,
    detect_stream_peaks,
    df_terminal_for_cohort,
)
from app.forecasting.types import ForecastConfig


def _normalized_series(monthly: Any, cfg: ForecastConfig) -> dict[str, dict[int, float]]:
    """{stream: {months_since_peak: rate / peak_rate}} for the streams
    where this well has >= MIN_FIT_MONTHS of post-peak FIT data — i.e. the
    exact months the production fitter would use (downtime dropped)."""
    out: dict[str, dict[int, float]] = {}
    peaks = detect_stream_peaks(monthly)
    for stream in STREAMS:
        peak = peaks[stream]
        if peak is None or peak.peak_rate <= 0:
            continue
        df, _ratio = _post_peak_slice(
            monthly,
            peak,
            STREAM_RATE_COLUMN[stream],
            STREAM_VOLUME_COLUMN[stream],
            downtime_floor=getattr(cfg, STREAM_DOWNTIME_FLOOR_FIELD[stream]),
        )
        if len(df) < MIN_FIT_MONTHS:
            continue
        months = (df["t_years"] * 12.0).round().astype(int) - 1
        out[stream] = {
            int(k): float(r) / peak.peak_rate for k, r in zip(months, df["rate"], strict=True)
        }
    return out


def _cohort_entry(
    lenders: list[dict[int, float]],
    stream: str,
    subbasins: list[str | None],
    cfg: ForecastConfig,
) -> dict[str, Any] | None:
    if len(lenders) < MIN_WELLS:
        return None
    curve = pooled_decline_curve(lenders)
    fitted = fit_pooled_b(
        curve, stream=stream, df_terminal_per_year=df_terminal_for_cohort(subbasins, cfg)
    )
    if fitted is None:
        return None
    return {**fitted, "n_wells": len(lenders)}


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=PRIORS_PATH)
    args = ap.parse_args()

    cfg = ForecastConfig()
    # (cohort key, stream) -> lender curves / their sub-basins
    bench: dict[tuple[str, str], list[dict[int, float]]] = defaultdict(list)
    sub: dict[tuple[str, str], list[dict[int, float]]] = defaultdict(list)
    subbasin_of: dict[str, str | None] = {}

    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        wells = session.execute(
            select(Well.api10, Well.subbasin, Well.formation_blueox).where(
                Well.lateral_ft >= MIN_LATERAL_FT,
                Well.vintage_year >= MIN_VINTAGE_YEAR,
                Well.api10.in_(text("(select distinct api10 from production_monthly)")),
            )
        ).all()
        for i, w in enumerate(wells):
            monthly = _load_monthly(session, w.api10)
            if monthly.empty:
                continue
            bkey, skey = bench_key(w.subbasin, w.formation_blueox), w.subbasin or UNMAPPED
            subbasin_of[bkey] = subbasin_of[skey] = w.subbasin
            for stream, series in _normalized_series(monthly, cfg).items():
                bench[(bkey, stream)].append(series)
                sub[(skey, stream)].append(series)
            if i % 500 == 0:
                print(f"  {i}/{len(wells)} wells", flush=True)
        session.rollback()

    def build(groups: dict[tuple[str, str], list[dict[int, float]]]) -> dict[str, Any]:
        table: dict[str, dict[str, Any]] = defaultdict(dict)
        for (key, stream), lenders in sorted(groups.items()):
            entry = _cohort_entry(lenders, stream, [subbasin_of[key]] * len(lenders), cfg)
            if entry is not None:
                table[key][stream] = entry
        return dict(table)

    payload = {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "git_sha": _git_sha(),
            "n_wells_scanned": len(wells),
            "min_fit_months": MIN_FIT_MONTHS,
            "min_lateral_ft": MIN_LATERAL_FT,
            "min_vintage_year": MIN_VINTAGE_YEAR,
            "min_wells": MIN_WELLS,
            "min_month_coverage": MIN_MONTH_COVERAGE,
            "method": "pooled peak-normalized median decline, fit_rate_cum, production bounds",
        },
        "bench": build(bench),
        "subbasin": build(sub),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    n = sum(len(v) for v in payload["bench"].values())
    print(f"wrote {args.out}: {n} bench-stream priors, {len(payload['subbasin'])} sub-basins")


if __name__ == "__main__":
    main()
