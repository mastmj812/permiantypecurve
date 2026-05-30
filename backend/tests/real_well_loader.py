"""Load a user-provided real-well CSV into the same monthly-DataFrame
shape the fitter expects from ``production_monthly``.

CSV header (BOM-prefixed in the user's exports):
    api14, prod_date, oil_bbl, gas_mcf, water_bbl, producing_days,
    first_prod_date, lateral_ft, formation

Calendar-day rates are computed inline as ``volume / calendar_days_in_month``.
This matches what Novi computes upstream post-cutover; the legacy
month-1 ``producing_days`` exception (which compensated for Enverus
quirks) is no longer applied. CSV fixtures are hand-curated for fitter
regression, so the simpler calendar-day rule is fine here.

``api14`` is kept as the identifier name in the fixture even after the
api14 -> api10 cutover, because the committed baselines JSON is keyed
on those CSV values. Renaming would invalidate the baselines without
changing what the test actually exercises (Arps fit math).
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class RealWellFixture:
    api14: str
    first_prod_date: date
    lateral_ft: float
    formation: str
    monthly: pd.DataFrame  # ready to feed fit_rate_cum


def _calendar_days_in(month_date: date) -> int:
    return monthrange(month_date.year, month_date.month)[1]


def _safe_div(volume: float | None, denom: float | None) -> float | None:
    if volume is None or denom is None or denom <= 0:
        return None
    return volume / denom


def load_real_well_csv(path: Path) -> RealWellFixture:
    # ``utf-8-sig`` strips the BOM if present without choking on plain UTF-8.
    raw = pd.read_csv(path, encoding="utf-8-sig")
    raw["prod_date"] = pd.to_datetime(raw["prod_date"], format="mixed").dt.date
    raw["first_prod_date"] = pd.to_datetime(
        raw["first_prod_date"], format="mixed"
    ).dt.date

    # Static fields are repeated on every row — take from the first.
    first = raw.iloc[0]
    api14 = str(first["api14"])
    first_prod_date = first["first_prod_date"]
    lateral_ft = float(first["lateral_ft"])
    formation = str(first["formation"])

    raw = raw.sort_values("prod_date").reset_index(drop=True)

    out_rows: list[dict] = []
    for _, r in raw.iterrows():
        prod_date = r["prod_date"]
        oil_bbl = float(r["oil_bbl"]) if pd.notna(r["oil_bbl"]) else None
        gas_mcf = float(r["gas_mcf"]) if pd.notna(r["gas_mcf"]) else None
        water_bbl = float(r["water_bbl"]) if pd.notna(r["water_bbl"]) else None
        producing_days = (
            int(r["producing_days"]) if pd.notna(r["producing_days"]) else None
        )
        cal_days = _calendar_days_in(prod_date)

        out_rows.append(
            {
                "prod_date": prod_date,
                "oil_bbl": oil_bbl,
                "gas_mcf": gas_mcf,
                "water_bbl": water_bbl,
                "producing_days": producing_days,
                "rate_calday_bopd": _safe_div(oil_bbl, cal_days),
                "rate_calday_mcfd": _safe_div(gas_mcf, cal_days),
                "rate_calday_bwpd": _safe_div(water_bbl, cal_days),
            }
        )
    monthly = pd.DataFrame(out_rows)
    return RealWellFixture(
        api14=api14,
        first_prod_date=first_prod_date,
        lateral_ft=lateral_ft,
        formation=formation,
        monthly=monthly,
    )
