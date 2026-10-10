"""``fit_well_streams`` reproduces anduin's per-well forecasts exactly.

The goldens were captured from anduin's ``orchestrator.forecast_well`` on
main 96470eb, BEFORE the extraction — so this pins the moved pipeline to
the pre-move behaviour without importing anything from anduin. Two real
wells x Delaware (Df 0.08) / Midland (Df 0.06), all three streams.
"""

from __future__ import annotations

import json
import math
from calendar import monthrange
from pathlib import Path

import pandas as pd
import pytest
from boxfit.ramp_arps import compute_total_eur
from boxfit.well import fit_well_streams

FIXTURES = Path(__file__).parent / "fixtures"
GOLDENS = json.loads((FIXTURES / "well_goldens.json").read_text(encoding="utf-8"))["wells"]
REL = 1e-9
_VOLUME_RATE = {
    "oil_bbl": "rate_calday_bopd",
    "gas_mcf": "rate_calday_mcfd",
    "water_bbl": "rate_calday_bwpd",
}


def _monthly(path: Path) -> pd.DataFrame:
    """Same frame anduin's tests/real_well_loader builds (calendar-day rates)."""
    raw = pd.read_csv(path, encoding="utf-8-sig")
    raw["prod_date"] = pd.to_datetime(raw["prod_date"], format="mixed").dt.date
    raw = raw.sort_values("prod_date").reset_index(drop=True)
    rows = []
    for _, r in raw.iterrows():
        d = r["prod_date"]
        days = monthrange(d.year, d.month)[1]
        row: dict[str, object] = {
            "prod_date": d,
            "producing_days": int(r["producing_days"]) if pd.notna(r["producing_days"]) else None,
        }
        for vol, rate in _VOLUME_RATE.items():
            v = float(r[vol]) if pd.notna(r[vol]) else None
            row[vol] = v
            row[rate] = None if v is None else v / days
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.mark.parametrize("key", sorted(GOLDENS))
def test_matches_pre_extraction_forecast(key: str) -> None:
    well, subbasin, bench = key.split("|")
    out = fit_well_streams(
        _monthly(FIXTURES / f"{well}.csv"), subbasin=subbasin, formation_blueox=bench
    )
    for stream, want in GOLDENS[key].items():
        got = out[stream]
        if want is None:
            assert got is None
            continue
        assert got is not None
        assert got.model_type == want["model_type"]
        assert got.peak_index_months == want["peak_index_months"]
        assert set(got.params) == set(want["params"])
        for p, v in want["params"].items():
            assert math.isclose(got.params[p], v, rel_tol=REL), (stream, p)
        assert got.eur is not None
        assert math.isclose(got.eur, want["eur"], rel_tol=REL), stream


def test_stored_eur_reconciles_with_recompute() -> None:
    """The stored scalar EUR equals a recompute-from-params (same integral)."""
    out = fit_well_streams(
        _monthly(FIXTURES / "real_well_1.csv"), subbasin="DELAWARE", formation_blueox="WCA_1"
    )
    oil = out["oil"]
    assert oil is not None and oil.eur is not None
    again = compute_total_eur(
        model_type=oil.model_type, params=oil.params, horizon_years=50.0, economic_limit=0.0
    )
    assert math.isclose(again, oil.eur, rel_tol=1e-12)


def test_callbacks_fire_in_stream_order() -> None:
    seen: list[str] = []
    fit_well_streams(
        _monthly(FIXTURES / "real_well_1.csv"),
        on_result=lambda s, _r: seen.append(s),
        on_no_peak=lambda s: seen.append(f"skip:{s}"),
    )
    assert seen == ["oil", "gas", "water"]
