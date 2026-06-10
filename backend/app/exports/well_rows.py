"""Per-well summary rows shared by the deal xlsx export and the
type-curve pptx export.

Both exports want the same 12 columns (the user-specified order on
the deal-deck slide and the metadata sheet) and the same EUR
convention (monthly trapezoid, matching the Review tab and the slide
`/well-stats` endpoint). Keeping the implementation here avoids a
circular import between `api.deals` and `api.type_curves`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Forecast, Well


# Columns for the per-well summary block. Order matches the deal-deck
# slide and the xlsx metadata sheet's per-well summary block.
#   BWPF = barrels of completion fluid per foot = fluid_bbl / lateral_ft
#   PPF  = pounds of proppant per foot = proppant_lbs / lateral_ft
#   Oil EUR / ft = oil_eur / lateral_ft (BBL / ft)
#   GOR EUR = gas_eur / oil_eur (MCF / BBL; lifetime gas-to-oil ratio)
#   WOR EUR = water_eur / oil_eur (BBL / BBL; lifetime water-to-oil ratio)
PER_WELL_HEADERS: tuple[str, ...] = (
    "api10",
    "Wellname",
    "Operator",
    "Formation",
    "Lateral Length",
    "BWPF",
    "PPF",
    "Oil EUR",
    "Gas EUR",
    "Oil EUR/ft",
    "GOR EUR",
    "WOR EUR",
)

# Excel number formats applied per column on the xlsx export's per-
# well summary rows. 1-indexed (matches openpyxl's `ws.cell(row,
# column)` API). The pptx export mirrors these as Python format
# strings server-side since PowerPoint cells are text.
PER_WELL_COL_FORMATS: dict[int, str] = {
    6: "#,##0",   # BWPF        — 0 decimals
    7: "#,##0",   # PPF         — 0 decimals
    8: "#,##0",   # Oil EUR     — 0 decimals
    9: "#,##0",   # Gas EUR     — 0 decimals
    10: "#,##0",  # Oil EUR/ft  — 0 decimals
    11: "0.0",    # GOR EUR     — 1 decimal
    12: "0.0",    # WOR EUR     — 1 decimal
}


def eur_monthly_trapezoid(params: dict[str, Any] | None) -> float | None:
    """Recompute a well's 50-yr EUR via the shared display convention.

    Thin wrapper over ``display_eur_from_params`` — a trapezoid over
    the monthly ramp+Arps grid, ramp prefix included, mirroring the
    frontend's ``eurFromForecastParams`` — so the per-well EURs in both
    exports match the Review tab, the probit dots, and the slide
    /well-stats endpoint. (The previous inline version was a left-
    endpoint rectangle sum that dropped the ramp prefix: workbook EURs
    ran ~1-3% high on the integration while losing the ramp volume the
    stored ``forecasts.eur`` includes.) None when the core Arps params
    are missing/non-finite — callers render an empty cell.
    """
    # Lazy import — ramp_arps pulls in numpy / scipy, which we don't
    # want forced into the import graph for endpoints that never touch
    # EUR math.
    from app.forecasting.ramp_arps import display_eur_from_params

    try:
        return display_eur_from_params(params)
    except Exception:
        return None


def per_well_rows(
    session: Session, api10s: list[str]
) -> list[tuple[Any, ...]]:
    """Materialize per-well summary rows for the api10s in order.

    Single query joins wells + their oil/gas/water forecasts; EUR is
    recomputed via the monthly trapezoid (see ``eur_monthly_trapezoid``).
    Returns rows in the order described by ``PER_WELL_HEADERS``. Wells
    without an oil forecast or lateral_ft still appear, with empty
    cells for the streams they're missing — engineers auditing the
    cohort should see the full membership.
    """
    if not api10s:
        return []
    wells = {
        w.api10: w
        for w in session.execute(
            select(Well).where(Well.api10.in_(api10s))
        ).scalars().all()
    }
    forecasts_by_well: dict[str, dict[str, Forecast]] = {}
    for f in session.execute(
        select(Forecast).where(Forecast.api10.in_(api10s))
    ).scalars().all():
        forecasts_by_well.setdefault(f.api10, {})[f.stream.value] = f

    rows: list[tuple[Any, ...]] = []
    for api10 in api10s:
        w = wells.get(api10)
        if w is None:
            rows.append((api10,) + tuple([None] * (len(PER_WELL_HEADERS) - 1)))
            continue
        fs = forecasts_by_well.get(api10, {})
        oil_eur = eur_monthly_trapezoid(fs.get("oil").params) if fs.get("oil") else None
        gas_eur = eur_monthly_trapezoid(fs.get("gas").params) if fs.get("gas") else None
        water_eur = eur_monthly_trapezoid(fs.get("water").params) if fs.get("water") else None

        lat = float(w.lateral_ft) if w.lateral_ft is not None else None
        prop = float(w.proppant_lbs) if w.proppant_lbs is not None else None
        fluid = float(w.fluid_bbl) if w.fluid_bbl is not None else None

        bwpf = fluid / lat if (fluid is not None and lat and lat > 0) else None
        ppf = prop / lat if (prop is not None and lat and lat > 0) else None
        oil_per_ft = oil_eur / lat if (oil_eur is not None and lat and lat > 0) else None
        gor_eur = gas_eur / oil_eur if (gas_eur is not None and oil_eur and oil_eur > 0) else None
        wor_eur = water_eur / oil_eur if (water_eur is not None and oil_eur and oil_eur > 0) else None

        rows.append((
            w.api10,
            w.name,
            w.operator,
            w.formation,
            lat,
            bwpf,
            ppf,
            oil_eur,
            gas_eur,
            oil_per_ft,
            gor_eur,
            wor_eur,
        ))
    return rows
