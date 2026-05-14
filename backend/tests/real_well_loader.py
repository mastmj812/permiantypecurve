"""Load a user-provided real-well CSV into the same monthly-DataFrame
shape the fitter expects from `production_monthly`.

CSV header (BOM-prefixed in the user's exports):
    api14, prod_date, oil_bbl, gas_mcf, water_bbl, producing_days,
    first_prod_date, lateral_ft, formation

Dates are US m/d/yyyy. We compute calday rates here using the same
month-1 partial-month rule that production-ingest applies — so the real-well
test path exercises the same business logic as the live ingest path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from app.ingest.rates import RateInputs, compute_rates


@dataclass(frozen=True)
class RealWellFixture:
    api14: str
    first_prod_date: date
    lateral_ft: float
    formation: str
    monthly: pd.DataFrame  # ready to feed fit_rate_cum


def load_real_well_csv(path: Path) -> RealWellFixture:
    # `utf-8-sig` strips the BOM if present without choking on plain UTF-8.
    raw = pd.read_csv(path, encoding="utf-8-sig")
    raw["prod_date"] = pd.to_datetime(raw["prod_date"], format="mixed").dt.date
    raw["first_prod_date"] = pd.to_datetime(raw["first_prod_date"], format="mixed").dt.date

    # Static fields are repeated on every row — take from the first.
    first = raw.iloc[0]
    api14 = str(first["api14"])
    first_prod_date = first["first_prod_date"]
    lateral_ft = float(first["lateral_ft"])
    formation = str(first["formation"])

    raw = raw.sort_values("prod_date").reset_index(drop=True)

    # Compute calday/prodday rates row by row using the production-ingest
    # helper. This is the SAME function used in the live pipeline, so any
    # change to the month-1 rule is automatically reflected here.
    out_rows: list[dict] = []
    for _, r in raw.iterrows():
        rates = compute_rates(
            RateInputs(
                prod_date=r["prod_date"],
                first_prod_date=first_prod_date,
                producing_days=int(r["producing_days"]) if pd.notna(r["producing_days"]) else None,
                oil_bbl=float(r["oil_bbl"]) if pd.notna(r["oil_bbl"]) else None,
                gas_mcf=float(r["gas_mcf"]) if pd.notna(r["gas_mcf"]) else None,
                water_bbl=float(r["water_bbl"]) if pd.notna(r["water_bbl"]) else None,
            )
        )
        out_rows.append(
            {
                "prod_date": r["prod_date"],
                "oil_bbl": float(r["oil_bbl"]) if pd.notna(r["oil_bbl"]) else None,
                "gas_mcf": float(r["gas_mcf"]) if pd.notna(r["gas_mcf"]) else None,
                "water_bbl": float(r["water_bbl"]) if pd.notna(r["water_bbl"]) else None,
                "producing_days": int(r["producing_days"]) if pd.notna(r["producing_days"]) else None,
                "rate_calday_bopd": rates.rate_calday_bopd,
                "rate_calday_mcfd": rates.rate_calday_mcfd,
                "rate_calday_bwpd": rates.rate_calday_bwpd,
                "rate_prodday_bopd": rates.rate_prodday_bopd,
                "rate_prodday_mcfd": rates.rate_prodday_mcfd,
                "rate_prodday_bwpd": rates.rate_prodday_bwpd,
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
