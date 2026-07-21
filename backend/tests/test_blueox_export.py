"""Contract tests for the Blue Ox curve-drop workbook.

Pure-builder tests (no DB): construct ``BlueOxExportData`` directly and
assert the workbook shape + the acceptance-gate rules from the contract
(engineering_db docs/blue_ox_curve_drop_contract.md) — complete
triplets, ascending percentile monotonicity, Block B reconciliation
computed from the delivered sheets, naming/lateral bounds, history
tie-out. One assembly-level test pins the SPE->ascending flip end to
end (our p90 fit must feed the file's _p10 columns).
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from openpyxl import load_workbook

from app.exports.blueox import (
    LEVEL_TO_SPE_KEY,
    NGL_BASIS,
    BlueOxContractError,
    BlueOxExportData,
    InventoryRow,
    ZoneData,
    blueox_filename,
    build_blueox_workbook,
    monthly_volumes_from_rates,
)

_MONTHS = 24
_ANALOG_HEADERS = (
    "api10", "Wellname", "Operator", "Formation", "Lateral Length",
)

# Ascending Blue Ox convention: p10 < base < p90.
_LEVEL_SCALE = {"P10": 0.5, "P25": 0.75, "P50": 1.0, "P75": 1.25, "P90": 1.5}


def _volumes(levels: tuple[str, ...], with_water: bool = True) -> dict[str, dict[str, list[float]]]:
    base_oil = [1000.0 - 30.0 * i for i in range(_MONTHS)]
    base_gas = [5000.0 - 100.0 * i for i in range(_MONTHS)]
    out: dict[str, dict[str, list[float]]] = {}
    for lv in ("P50", *levels):
        scale = _LEVEL_SCALE[lv]
        out[lv] = {
            "oil": [v * scale for v in base_oil],
            "gas": [v * scale for v in base_gas],
        }
    if with_water:
        out["P50"]["water"] = [2000.0 - 50.0 * i for i in range(_MONTHS)]
    return out


def _params(levels: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for stream in ("oil", "gas"):
        for lv in ("P50", *levels):
            rows.append({
                "stream": stream,
                "level": lv,
                "qi": 250.0 * _LEVEL_SCALE[lv],
                "qi_units": "bbl/d per 1,000 ft" if stream == "oil" else "Mcf/d per 1,000 ft",
                "b_factor": 1.1,
                "di": 2.5,
                "dmin": 0.08,
                "notes": "peak_ramp alignment; qi at peak month 4",
            })
    return rows


def _zone(
    name: str,
    apis: tuple[str, ...],
    levels: tuple[str, ...] = ("P10", "P90"),
    n_inventory: int = 3,
) -> ZoneData:
    return ZoneData(
        zone_name=name,
        reserve_category="PUD",
        normalization_basis="per_1000_lateral_ft",
        volumes=_volumes(levels),
        curve_params=_params(levels),
        analog_headers=_ANALOG_HEADERS,
        analog_rows=[
            (api, f"WELL {i} 123H", "OPERATOR X", "WOLFCAMP A", 9800.0 + i)
            for i, api in enumerate(apis)
        ],
        inventory=[
            InventoryRow(
                producing_lateral_ft=10_000.0,
                drilled_lateral_ft=12_500.0 + 100.0 * i,
                well_name=f"PLANNED {i + 1}",
            )
            for i in range(n_inventory)
        ],
    )


def _production_rows(apis: tuple[str, ...]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for api in sorted(apis):
        for m in (1, 2, 3):
            rows.append([api, f"2025-0{m}", 30_000.0 / m, 90_000.0 / m, 40_000.0 / m, 30.0])
    return rows


def _data(
    zones: list[ZoneData] | None = None,
    levels: tuple[str, ...] = ("P10", "P90"),
    codename: str = "holdtheline",
    production_rows: list[list[Any]] | None = None,
    history_exceptions: tuple[str, ...] = (),
) -> BlueOxExportData:
    if zones is None:
        zones = [
            _zone("WOLFCAMP A", ("4200000001", "4200000002"), levels),
            _zone("THIRD BONE SPRING", ("4200000003",), levels),
        ]
    apis = tuple(
        str(r[0]) for z in zones for r in z.analog_rows
        if str(r[0]) not in history_exceptions
    )
    return BlueOxExportData(
        codename=codename,
        export_date=date(2026, 7, 20),
        curve_months=_MONTHS,
        levels=levels,
        prepared_by="M. Mast",
        source_system="anduin type-curve DB (synced from oilgas warehouse)",
        governing_export="deal holdTheLine — holdtheline_curves_2026-07-20.xlsx",
        curve_params_source="wolfcamp_a_v3 saved 2026-07-18",
        zones=zones,
        production_headers=("api10", "date", "oil_bbl", "gas_mcf", "water_bbl", "days_on"),
        production_rows=production_rows if production_rows is not None else _production_rows(apis),
        production_history_through="2025-03",
        history_exceptions=history_exceptions,
    )


# ============================ shape ============================


def test_sheet_set_and_order() -> None:
    wb = load_workbook(io.BytesIO(build_blueox_workbook(_data())))
    assert wb.sheetnames == [
        "WOLFCAMP A",
        "THIRD BONE SPRING",
        "meta",
        "inventory",
        "WOLFCAMP A meta",
        "THIRD BONE SPRING meta",
        "analog_production",
        "curve_params",
        "manifest",
    ]


def test_zone_sheet_shape_and_ngl_zero() -> None:
    wb = load_workbook(io.BytesIO(build_blueox_workbook(_data())))
    ws = wb["WOLFCAMP A"]
    header = [c.value for c in ws[1]]
    # No month column; exact lowercase headers; water after the base
    # triplet; percentile triplets complete (ngl rides along all-zero).
    assert header == [
        "oil_bbl", "gas_mcf", "ngl_bbl", "water_bbl",
        "oil_bbl_p10", "gas_mcf_p10", "ngl_bbl_p10",
        "oil_bbl_p90", "gas_mcf_p90", "ngl_bbl_p90",
    ]
    assert ws.max_row == _MONTHS + 1
    ngl_cols = [i + 1 for i, h in enumerate(header) if "ngl" in str(h)]
    for col in ngl_cols:
        vals = [ws.cell(row=r, column=col).value for r in range(2, ws.max_row + 1)]
        assert all(v == 0.0 for v in vals)
    # Numbers as numbers (openpyxl reads whole floats back as int —
    # both satisfy the contract's numbers-not-text rule).
    assert isinstance(ws.cell(row=2, column=1).value, (int, float))


def test_analog_sheet_marker_and_single_api_column() -> None:
    wb = load_workbook(io.BytesIO(build_blueox_workbook(_data())))
    ws = wb["WOLFCAMP A meta"]
    assert ws.cell(row=1, column=1).value == "per_well_summary"
    headers = [c.value for c in ws[2]]
    api_cols = [h for h in headers if h and "api" in str(h).lower()]
    assert api_cols == ["api10"]


def test_meta_sheet_rows() -> None:
    wb = load_workbook(io.BytesIO(build_blueox_workbook(_data())))
    rows = list(wb["meta"].iter_rows(values_only=True))
    assert rows[0] == ("area", "normalization_basis", "reserve_category")
    assert rows[1] == ("WOLFCAMP A", "per_1000_lateral_ft", "PUD")


# ============================ manifest ============================


def _manifest_kv(wb: Any) -> dict[str, Any]:
    rows = list(wb["manifest"].iter_rows(values_only=True))
    return {r[0]: r[1] for r in rows if r and r[0] is not None and r[1] is not None}


def test_manifest_block_a_declarations() -> None:
    wb = load_workbook(io.BytesIO(build_blueox_workbook(_data())))
    kv = _manifest_kv(wb)
    assert kv["percentile_orientation"] == "ascending"
    assert kv["gas_basis"] == "wellhead_unshrunk"
    assert kv["risking"] == "unrisked"
    # 2026-07-20 amendment: Blue Ox applies their own yield.
    assert kv["ngl_basis"] == NGL_BASIS == "derived_by_blue_ox_via_yield"
    assert kv["curve_months"] == _MONTHS
    assert kv["production_history_through"] == "2025-03"
    assert kv["deal_codename"] == "holdtheline"


def test_manifest_block_b_ties_to_delivered_sheets() -> None:
    wb = load_workbook(io.BytesIO(build_blueox_workbook(_data())))
    rows = list(wb["manifest"].iter_rows(values_only=True))
    header_idx = next(i for i, r in enumerate(rows) if r and r[0] == "area")
    header = rows[header_idx]
    by_zone = {
        r[0]: dict(zip(header, r, strict=False))
        for r in rows[header_idx + 1:]
        if r and r[0]
    }

    ws = wb["WOLFCAMP A"]
    zone_header = [c.value for c in ws[1]]
    oil_col = zone_header.index("oil_bbl") + 1
    oil_sum = sum(
        float(ws.cell(row=r, column=oil_col).value)
        for r in range(2, ws.max_row + 1)
    )
    block_b = by_zone["WOLFCAMP A"]
    # ±0.1% is the acceptance gate; delivered-sheet arithmetic should
    # tie essentially exactly.
    assert abs(float(block_b["eur_oil_bbl"]) - oil_sum) < 1e-6 * max(oil_sum, 1.0)
    assert block_b["eur_ngl_bbl"] == 0.0
    assert block_b["gross_locations"] == 3
    inv_ws = wb["inventory"]
    drilled = [
        float(r[2].value)
        for r in inv_ws.iter_rows(min_row=2)
        if r[0].value == "WOLFCAMP A"
    ]
    assert len(drilled) == 3
    assert abs(float(block_b["avg_drilled_lateral_ft"]) - sum(drilled) / 3) < 1e-9


# ============================ gates ============================


def test_percentile_monotonicity_enforced() -> None:
    zones = [_zone("WOLFCAMP A", ("4200000001",))]
    vols = dict(zones[0].volumes)
    # Sabotage: hand the P10 (low case) the HIGH volumes — the classic
    # un-flipped SPE export.
    vols["P10"], vols["P90"] = vols["P90"], vols["P10"]
    bad = ZoneData(**{**zones[0].__dict__, "volumes": vols})
    with pytest.raises(BlueOxContractError, match="not strictly ascending"):
        build_blueox_workbook(_data(zones=[bad]))


def test_partial_triplet_is_hard_failure() -> None:
    zones = [_zone("WOLFCAMP A", ("4200000001",))]
    vols = {lv: dict(by) for lv, by in zones[0].volumes.items()}
    del vols["P10"]["gas"]
    bad = ZoneData(**{**zones[0].__dict__, "volumes": vols})
    with pytest.raises(BlueOxContractError, match="partial triplet"):
        build_blueox_workbook(_data(zones=[bad]))


def test_zone_name_rules() -> None:
    for name, fragment in (
        ("A ZONE NAME THAT RUNS MUCH TOO LONG", "exceeds 26"),
        ("manifest", "reserved"),
        ("WOLF/CAMP", "forbidden character"),
        (" WOLFCAMP A", "leading/trailing"),
    ):
        with pytest.raises(BlueOxContractError, match=fragment):
            build_blueox_workbook(
                _data(zones=[_zone(name, ("4200000001",))])
            )


def test_lateral_bounds() -> None:
    z = _zone("WOLFCAMP A", ("4200000001",))
    bad = ZoneData(**{
        **z.__dict__,
        "inventory": [InventoryRow(producing_lateral_ft=2_500.0, drilled_lateral_ft=12_000.0)],
    })
    with pytest.raises(BlueOxContractError, match="outside 3000-25000 ft"):
        build_blueox_workbook(_data(zones=[bad]))


def test_history_tieout_both_ways() -> None:
    # A listed analog with no history rows must be declared or fail.
    with pytest.raises(BlueOxContractError, match="no production history"):
        build_blueox_workbook(_data(history_exceptions=(), production_rows=_production_rows(("4200000001", "4200000002"))))
    # Declared exception passes.
    build_blueox_workbook(_data(
        history_exceptions=("4200000003",),
        production_rows=_production_rows(("4200000001", "4200000002")),
    ))
    # And the exception is surfaced in the manifest, never silent.
    wb = load_workbook(io.BytesIO(build_blueox_workbook(_data(
        history_exceptions=("4200000003",),
        production_rows=_production_rows(("4200000001", "4200000002")),
    ))))
    kv = _manifest_kv(wb)
    assert "4200000003" in str(kv["analog_history_exceptions"])


def test_filename_and_reserved_stems() -> None:
    assert (
        blueox_filename("holdtheline", date(2026, 7, 20))
        == "holdtheline_curves_2026-07-20.xlsx"
    )
    with pytest.raises(BlueOxContractError, match="reserved stem"):
        build_blueox_workbook(_data(codename="pinned_ranch"))
    # The mandated "_curves_" stem itself must not trip "type_curves"
    # when a codename ends in "type".
    build_blueox_workbook(_data(codename="prototype"))


# ============================ conventions ============================


def test_level_to_spe_flip() -> None:
    # Blue Ox ascending -> anduin SPE (migration 0021): their low case
    # is our P90 fit. If this mapping ever changes, a whole deal is
    # silently inverted — pin it.
    assert LEVEL_TO_SPE_KEY == {
        "P10": "p90", "P25": "p75", "P50": "p50", "P75": "p25", "P90": "p10",
    }


def test_monthly_volumes_trapezoid_and_zero_fill() -> None:
    d = 365.0 / 12.0
    vols = monthly_volumes_from_rates([100.0, 80.0, 60.0], 5, d)
    assert vols[0] == pytest.approx((100.0 + 80.0) / 2.0 * d)
    assert vols[1] == pytest.approx((80.0 + 60.0) / 2.0 * d)
    # Last available month flat-extrapolates; the tail zero-fills.
    assert vols[2] == pytest.approx(60.0 * d)
    assert vols[3] == 0.0 and vols[4] == 0.0


def test_assembly_flip_feeds_p10_columns_from_spe_p90_fit() -> None:
    """End-to-end pin of the orientation flip: a stub curve whose SPE
    p10 (high) fit is qi=200 and SPE p90 (low) fit is qi=50 must land
    qi=50-derived volumes in the file's _p10 columns."""
    from unittest.mock import patch

    from app.api.deals import BlueOxInventoryRowIn, BlueOxZoneSpec, _collect_blueox_zone
    from app.db.models import AlignmentMethod, NormalizationBasis, TypeCurve

    def _fit(qi: float) -> dict[str, Any]:
        # Constant-rate Arps (Di=0) — volumes reduce to qi * days.
        return {"qi": qi, "Di": 0.0, "b": 0.0, "Df": 0.0, "qo": qi, "peak_index": 0}

    def _stream(q_low: float, q_mid: float, q_high: float) -> dict[str, Any]:
        return {
            "fitted_per_percentile": {
                # SPE orientation as persisted: p10 = HIGH case.
                "p10": _fit(q_high), "p25": _fit(q_high * 0.9),
                "p50": _fit(q_mid),
                "p75": _fit(q_low * 1.1), "p90": _fit(q_low),
                "mean": _fit(q_mid),
            },
        }

    tc = TypeCurve()
    tc.id = uuid.uuid4()
    tc.name = "WOLFCAMP A"
    tc.included_api10s = ["4200000001"]
    tc.normalization_basis = NormalizationBasis.PER_LATERAL_FT
    tc.alignment_method = AlignmentMethod.PEAK_RAMP
    tc.created_at = datetime(2026, 7, 18, tzinfo=UTC)
    tc.series = {
        "streams": {
            "oil": _stream(50.0, 100.0, 200.0),
            "gas": _stream(300.0, 600.0, 1200.0),
        },
    }

    spec = BlueOxZoneSpec(
        type_curve_id=tc.id,
        reserve_category="PUD",
        inventory=[BlueOxInventoryRowIn(drilled_lateral_ft=10_000.0)],
    )
    errors: list[str] = []
    with patch(
        "app.api.deals.per_well_rows",
        return_value=[("4200000001", "W 1H", "OP", "WOLFCAMP A", 10_000.0)],
    ):
        zone = _collect_blueox_zone(
            None, tc, spec, ["P10", "P50", "P90"], 12, errors,  # type: ignore[arg-type]
        )
    assert errors == []
    assert zone is not None

    d = 365.0 / 12.0
    # Constant-rate: every month's volume = qi * days_per_month.
    assert zone.volumes["P10"]["oil"][0] == pytest.approx(50.0 * d)
    assert zone.volumes["P50"]["oil"][0] == pytest.approx(100.0 * d)
    assert zone.volumes["P90"]["oil"][0] == pytest.approx(200.0 * d)
    # curve_params quotes the SAME flipped fit (level P10 row = qi 50).
    p10_oil = next(
        r for r in zone.curve_params if r["stream"] == "oil" and r["level"] == "P10"
    )
    assert p10_oil["qi"] == 50.0
    assert p10_oil["qi_units"] == "bbl/d per 1,000 ft"
    # producing_lateral_ft defaulted to the cohort mean.
    assert zone.inventory[0].producing_lateral_ft == 10_000.0
