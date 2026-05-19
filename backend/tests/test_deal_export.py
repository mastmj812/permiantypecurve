"""Workbook-shape tests for the deal export.

Builds the workbook bytes directly via ``_build_workbook`` (no DB
required) and asserts the sheet layout the engineering team handed us:
two sheets per assigned type curve — one metadata, one forecast — with
the forecast tab holding the 50-year fitted projection (600 monthly
rows + 1 header).
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from typing import Any

from openpyxl import load_workbook

from app.api.deals import _build_workbook, _sheet_slug
from app.db.models import AlignmentMethod, Deal, NormalizationBasis, TypeCurve


def _stub_stream(rate: float = 100.0, n: int = 24) -> dict[str, Any]:
    """Minimal per-stream payload: flat-rate per-percentile arrays + a
    ``fitted_per_percentile`` entry so ``_evaluate_fitted_rates`` can
    skip the refit path and just evaluate the persisted params."""
    arr = [rate] * n
    fit = {
        # Constant-rate Arps: Di=0 reduces to qi forever. Good enough
        # to verify the workbook plumbing without exercising scipy.
        "qi": rate,
        "Di": 0.0,
        "b": 0.0,
        "Df": 0.0,
        "qo": rate,
        "peak_index": 0,
        "eur_per_unit": rate * 365.0 * 50.0,
    }
    return {
        "p10": arr, "p25": arr, "p50": arr, "p75": arr, "p90": arr,
        "mean": arr, "well_count": [1] * n,
        "implied_eur_per_1000ft": {
            "p10": 100.0, "p25": 100.0, "p50": 100.0,
            "p75": 100.0, "p90": 100.0, "mean": 100.0,
        },
        "fitted_per_percentile": {
            "p10": fit, "p25": fit, "p50": fit,
            "p75": fit, "p90": fit, "mean": fit,
        },
    }


def _curve(name: str) -> TypeCurve:
    tc = TypeCurve()
    tc.id = uuid.uuid4()
    tc.name = name
    tc.notes = "stub for export test"
    tc.filter_spec = {"formation": "Wolfcamp A", "county": "Loving"}
    tc.included_api14s = ["42100000000001", "42100000000002"]
    tc.normalization_basis = NormalizationBasis.PER_LATERAL_FT
    tc.alignment_method = AlignmentMethod.FIRST_PROD_MONTH
    tc.series = {
        "n_months": 24,
        "n_wells": 2,
        "streams": {
            "oil": _stub_stream(rate=100.0),
            "gas": _stub_stream(rate=500.0),
            "water": _stub_stream(rate=200.0),
        },
    }
    tc.created_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
    tc.version_of = None
    tc.deal_id = None
    return tc


def _deal() -> Deal:
    d = Deal()
    d.id = uuid.uuid4()
    d.name = "holdTheLine"
    d.notes = None
    d.created_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
    return d


def test_export_has_two_sheets_per_curve() -> None:
    curves = [_curve("holdTheLine_bs1s_v2"), _curve("holdTheLine_bs2s_v2")]
    content = _build_workbook(_deal(), curves)
    wb = load_workbook(io.BytesIO(content), read_only=True)
    names = wb.sheetnames
    assert len(names) == 4
    # Sheet pairs are emitted in input order: meta then forecast per curve.
    assert "meta" in names[0] and "bs1s_v2" in names[0]
    assert "forecast" in names[1] and "bs1s_v2" in names[1]
    assert "meta" in names[2] and "bs2s_v2" in names[2]
    assert "forecast" in names[3] and "bs2s_v2" in names[3]


def test_forecast_sheet_has_full_50yr_horizon() -> None:
    curves = [_curve("holdTheLine_bs1s_v2")]
    content = _build_workbook(_deal(), curves)
    wb = load_workbook(io.BytesIO(content))
    forecast_ws = [s for s in wb.sheetnames if "forecast" in s][0]
    ws = wb[forecast_ws]
    # 600 monthly rows + 1 header.
    assert ws.max_row == 601
    # 1 month column + 3 streams × 6 pcts × 2 (rate/cum).
    assert ws.max_column == 1 + 3 * 6 * 2
    # Header sanity: first column reflects alignment method, last column
    # is the water-mean cum.
    assert ws.cell(row=1, column=1).value == "month_since_first_prod"
    assert ws.cell(row=1, column=ws.max_column).value == "water_mean_cum"
    # Row 1 month index = 1; row 600's last data row = 600.
    assert ws.cell(row=2, column=1).value == 1
    assert ws.cell(row=601, column=1).value == 600


def test_metadata_sheet_contains_curve_fields() -> None:
    tc = _curve("holdTheLine_bs1s_v2")
    content = _build_workbook(_deal(), [tc])
    wb = load_workbook(io.BytesIO(content))
    meta_ws = [s for s in wb.sheetnames if "meta" in s][0]
    ws = wb[meta_ws]
    # Convert to a flat field->value dict for the two-column section so
    # the test stays robust to non-essential row ordering tweaks.
    rows = list(ws.iter_rows(values_only=True))
    kv = {r[0]: r[1] for r in rows if r and r[0] is not None}
    assert kv["name"] == "holdTheLine_bs1s_v2"
    assert kv["normalization_basis"] == "per_lateral_ft"
    assert kv["alignment_method"] == "first_prod_month"
    assert kv["n_wells"] == 2
    # filter_spec entries land further down in the same column-A layout.
    assert kv["formation"] == "Wolfcamp A"


def test_sheet_slug_collision_disambiguates() -> None:
    used: set[str] = set()
    # Long names with identical 22-char prefix should produce distinct slugs.
    a = _sheet_slug("holdTheLine_bs1s_v2_alpha", used)
    b = _sheet_slug("holdTheLine_bs1s_v2_beta", used)
    assert a != b
    assert a in used and b in used


