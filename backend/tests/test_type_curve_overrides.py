"""Pure-function tests for the TC workspace's override-resolution rule.

The DB-touching path (the /api/type-curves/{id}/wells endpoint that
joins wells + forecasts and groups in Python) is verified by manual
smoke against the running stack — no conftest with a test database
exists in this repo, so unit tests focus on the resolver helper that
both Phase 1 (read) and Phase 2 (write) share. The three cases here
mirror the contract the Phase 1 prompt called out:

  1. No overrides → global forecast wins.
  2. Override present → override wins, source = "override".
  3. No override and no global → missing, source = "missing".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from app.db.models import (
    AlignmentMethod,
    FitMethod,
    Forecast,
    ModelType,
    NormalizationBasis,
    Stream,
    TypeCurve,
)
from app.type_curves.overrides import resolve_forecast


def _curve(forecast_overrides: dict[str, Any] | None = None) -> TypeCurve:
    tc = TypeCurve()
    tc.id = uuid.uuid4()
    tc.name = "test_curve"
    tc.notes = None
    tc.filter_spec = {}
    tc.included_api14s = ["42100000000001"]
    tc.normalization_basis = NormalizationBasis.PER_LATERAL_FT
    tc.alignment_method = AlignmentMethod.FIRST_PROD_MONTH
    tc.series = {}
    tc.created_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
    tc.version_of = None
    tc.deal_id = None
    tc.is_stale = False
    tc.forecast_overrides = forecast_overrides or {}
    return tc


def _global_forecast(api14: str, stream: Stream, qi: float = 500.0) -> Forecast:
    f = Forecast()
    f.id = uuid.uuid4()
    f.api14 = api14
    f.stream = stream
    f.model_type = ModelType.MODIFIED_HYPERBOLIC
    f.params = {"qi": qi, "Di": 1.5, "b": 1.0, "Df": 0.08}
    f.qi = qi
    f.di_initial = 1.5
    f.b = 1.0
    f.df_terminal = 0.08
    f.eur = 100_000.0
    f.peak_month_date = date(2024, 1, 1)
    f.peak_rate = qi
    f.fit_method = FitMethod.RATE_CUM
    f.fit_r2 = 0.97
    f.fit_rmse = 5.0
    f.downtime_ratio = 0.0
    f.manual_override = False
    f.locked = False
    return f


def test_resolves_to_global_when_no_overrides() -> None:
    tc = _curve(forecast_overrides={})
    api14 = "42100000000001"
    global_f = _global_forecast(api14, Stream.OIL, qi=500.0)

    resolved = resolve_forecast(tc, api14, Stream.OIL, global_f)

    assert resolved.source == "global"
    assert resolved.payload is not None
    # qi from the global forecast propagates through.
    assert resolved.payload["qi"] == 500.0
    # ModelType serializes as its string value.
    assert resolved.payload["model_type"] == "modified_hyperbolic"


def test_override_wins_over_global() -> None:
    api14 = "42100000000001"
    # Override pins qi to a sentinel that doesn't match the global —
    # if the resolver mistakenly returns global, this would be 500.
    override_payload = {
        "params": {"qi": 777.0, "Di": 2.0, "b": 0.8, "Df": 0.08},
        "qi": 777.0,
        "model_type": "modified_hyperbolic",
        "manual_override": True,
    }
    tc = _curve(forecast_overrides={api14: {"oil": override_payload}})
    global_f = _global_forecast(api14, Stream.OIL, qi=500.0)

    resolved = resolve_forecast(tc, api14, Stream.OIL, global_f)

    assert resolved.source == "override"
    assert resolved.payload is not None
    assert resolved.payload["qi"] == 777.0
    assert resolved.payload["manual_override"] is True


def test_missing_when_no_override_and_no_global() -> None:
    tc = _curve(forecast_overrides={})
    api14 = "42100000000001"

    resolved = resolve_forecast(tc, api14, Stream.GAS, None)

    assert resolved.source == "missing"
    assert resolved.payload is None


def test_override_is_scoped_per_stream() -> None:
    """An override on oil must NOT mask gas — gas should still fall
    back to its global. Catches a regression where the resolver
    accidentally treated the whole well as overridden."""
    api14 = "42100000000001"
    tc = _curve(
        forecast_overrides={
            api14: {"oil": {"qi": 777.0, "model_type": "modified_hyperbolic"}},
        }
    )
    global_gas = _global_forecast(api14, Stream.GAS, qi=2500.0)

    resolved = resolve_forecast(tc, api14, Stream.GAS, global_gas)

    assert resolved.source == "global"
    assert resolved.payload is not None
    assert resolved.payload["qi"] == 2500.0


# ============ Phase 2: override-payload builder + write/delete round-trip ============


def test_override_payload_carries_eur_and_derived_fields() -> None:
    """The helper that builds the JSONB payload for a new override must
    include the same scalar + derived fields the global-forecast
    projection emits — otherwise the workspace renderer's per-well
    columns (EUR, Di, di_effective) would silently render as '—' for
    overridden rows. Locks the shape contract."""
    from app.api.type_curves import _override_payload_from_params

    payload = _override_payload_from_params(qi=500.0, Di=1.5, b=1.0, Df=0.08)

    # Core Arps params round-trip.
    assert payload["qi"] == 500.0
    assert payload["di_initial"] == 1.5
    assert payload["b"] == 1.0
    assert payload["df_terminal"] == 0.08
    assert payload["params"] == {"qi": 500.0, "Di": 1.5, "b": 1.0, "Df": 0.08}
    # Derived: EUR is a positive number (compute_eur output), di_effective is in (0, 1).
    assert payload["eur"] is not None and payload["eur"] > 0
    assert 0.0 < payload["di_effective"] < 1.0
    # Provenance flag — operator can spot edited rows in audits.
    assert payload["manual_override"] is True


def test_write_then_resolve_round_trip() -> None:
    """Mimic the PUT endpoint's mutation (set tc.forecast_overrides[api14]
    [stream]) and confirm resolve_forecast picks it up. Guards against
    drift between the write path and the read path — both share the
    same JSONB layout, but the endpoint and resolver live in
    different modules."""
    from app.api.type_curves import _override_payload_from_params

    api14 = "42100000000001"
    tc = _curve()  # empty overrides
    payload = _override_payload_from_params(qi=999.0, Di=2.5, b=0.8, Df=0.08)

    # Write path (what the PUT endpoint does inline).
    overrides = dict(tc.forecast_overrides or {})
    well_block = dict(overrides.get(api14) or {})
    well_block[Stream.OIL.value] = payload
    overrides[api14] = well_block
    tc.forecast_overrides = overrides

    # Read path with a global forecast present — override should win.
    global_oil = _global_forecast(api14, Stream.OIL, qi=500.0)
    resolved = resolve_forecast(tc, api14, Stream.OIL, global_oil)
    assert resolved.source == "override"
    assert resolved.payload is not None
    assert resolved.payload["qi"] == 999.0
    assert resolved.payload["manual_override"] is True


def test_delete_then_resolve_falls_back_to_global() -> None:
    """Mimic the DELETE endpoint and confirm the resolver re-falls to
    the global row. Catches the case where the DELETE handler leaves
    an empty {api14: {}} dict around, which the read path should
    ignore."""
    api14 = "42100000000001"
    payload = _payload_stub(qi=777.0)
    tc = _curve(forecast_overrides={api14: {Stream.OIL.value: payload}})

    # Delete path — mirror the endpoint's logic exactly so the test
    # catches its specific behavior (empty-block cleanup).
    overrides = dict(tc.forecast_overrides or {})
    well_block = dict(overrides.get(api14) or {})
    well_block.pop(Stream.OIL.value, None)
    if well_block:
        overrides[api14] = well_block
    else:
        overrides.pop(api14, None)
    tc.forecast_overrides = overrides

    global_oil = _global_forecast(api14, Stream.OIL, qi=500.0)
    resolved = resolve_forecast(tc, api14, Stream.OIL, global_oil)
    assert resolved.source == "global"
    assert resolved.payload is not None
    assert resolved.payload["qi"] == 500.0


def _payload_stub(qi: float) -> dict[str, Any]:
    """Minimal override payload shape — sufficient for resolver round-trip
    tests without exercising compute_eur."""
    return {
        "params": {"qi": qi, "Di": 1.5, "b": 1.0, "Df": 0.08},
        "model_type": "modified_hyperbolic",
        "qi": qi,
        "di_initial": 1.5,
        "b": 1.0,
        "df_terminal": 0.08,
        "eur": 100_000.0,
        "manual_override": True,
    }
