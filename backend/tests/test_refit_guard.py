"""The bulk-refit guard: a refit must REFUSE to run over forecast rows in the
ambiguous ``manual_override=True, locked=False`` state, so an engineer's
unlocked edit is never silently overwritten (``_persist`` only protects
``locked`` rows).

DB-free like the rest of the suite: the pure predicate is exercised on
in-memory ``Forecast`` rows, and the ``forecast_wells`` guard is exercised by
stubbing the SQL finder — no database is touched.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models import FitMethod, Forecast, ModelType, Stream
from app.forecasting import orchestrator
from app.forecasting.orchestrator import (
    ManualOverrideGuardError,
    at_risk_forecasts,
    bulk_refit_dry_run,
    forecast_wells,
)


def _fc(
    *,
    manual_override: bool,
    locked: bool,
    api10: str = "42100000000001",
    stream: Stream = Stream.OIL,
) -> Forecast:
    """Minimal in-memory Forecast row (no DB flush) — mirrors the in-memory
    construction used in test_type_curve_overrides.py."""
    f = Forecast()
    f.id = uuid.uuid4()
    f.api10 = api10
    f.stream = stream
    f.model_type = ModelType.MODIFIED_HYPERBOLIC
    f.params = {"qi": 500.0, "Di": 1.5, "b": 1.0, "Df": 0.08}
    f.fit_method = FitMethod.RATE_CUM
    f.manual_override = manual_override
    f.locked = locked
    return f


def test_at_risk_flags_only_unlocked_overrides() -> None:
    rows = [
        _fc(manual_override=True, locked=False),  # at risk — unlocked edit
        _fc(manual_override=True, locked=True),  # locked keeper — safe
        _fc(manual_override=False, locked=False),  # machine fit — safe
        _fc(manual_override=False, locked=True),  # locked machine fit — safe
    ]

    at_risk = at_risk_forecasts(rows)

    assert len(at_risk) == 1
    assert at_risk[0].manual_override is True
    assert at_risk[0].locked is False


def test_at_risk_empty_when_every_override_is_locked() -> None:
    # The defused end-state: all manual overrides are locked keepers.
    rows = [_fc(manual_override=True, locked=True) for _ in range(5)]
    assert at_risk_forecasts(rows) == []


def test_forecast_wells_refuses_when_rows_are_at_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "find_at_risk_rows",
        lambda session, api10s: [("42100000000001", "oil")],
    )

    fit_calls: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "forecast_well",
        lambda session, api10, *, config=None: fit_calls.append(api10) or {},
    )

    with pytest.raises(ManualOverrideGuardError) as excinfo:
        forecast_wells(object(), ["42100000000001"])

    assert excinfo.value.at_risk == [("42100000000001", "oil")]
    assert "Refusing bulk refit" in str(excinfo.value)
    # The guard must fire BEFORE any well is fit/persisted.
    assert fit_calls == []


def test_forecast_wells_proceeds_when_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orchestrator, "find_at_risk_rows", lambda session, api10s: [])

    seen: list[str] = []
    monkeypatch.setattr(
        orchestrator,
        "forecast_well",
        lambda session, api10, *, config=None: (seen.append(api10), {"oil": None})[1],
    )

    out = forecast_wells(object(), ["42100000000001", "42100000000002"])

    assert seen == ["42100000000001", "42100000000002"]
    assert set(out) == {"42100000000001", "42100000000002"}


def test_dry_run_reports_at_risk_without_fitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "find_at_risk_rows",
        lambda session, api10s: [("x", "oil"), ("y", "gas")],
    )

    def _must_not_fit(*args: object, **kwargs: object) -> object:
        raise AssertionError("dry-run must not fit any well")

    monkeypatch.setattr(orchestrator, "forecast_well", _must_not_fit)

    assert bulk_refit_dry_run(object(), None) == [("x", "oil"), ("y", "gas")]
