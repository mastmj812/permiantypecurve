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
from datetime import date

import pytest

from app.api import forecasts as api_forecasts
from app.api.forecasts import apply_autofit, revert_streams_to_autofit
from app.db.models import FitMethod, Forecast, ModelType, Stream
from app.forecasting import orchestrator
from app.forecasting.orchestrator import (
    ManualOverrideGuardError,
    at_risk_forecasts,
    bulk_refit_dry_run,
    forecast_wells,
)
from app.forecasting.types import ForecastResult


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


# ---------------------------------------------------------------------
# Revert to auto-fit — the UI's triage path for the at-risk set
# ---------------------------------------------------------------------


def _result(qi: float = 480.0) -> ForecastResult:
    return ForecastResult(
        model_type="modified_hyperbolic",
        params={"qi": qi, "Di": 2.5, "b": 1.0, "Df": 0.08},
        qi=qi,
        di_initial=2.5,
        b=1.0,
        df_terminal=0.08,
        eur=250_000.0,
        peak_month_date=date(2023, 2, 1),
        peak_rate=500.0,
        fit_method="rate_cum",
        fit_r2=0.97,
        fit_rmse=10.0,
        n_points_fit=20,
        diagnostics={"source": "machine"},
    )


class _FakeSession:
    """Answers revert_streams_to_autofit's single SELECT with fixed rows."""

    def __init__(self, rows: list[Forecast]) -> None:
        self._rows = rows

    def execute(self, _stmt: object) -> _FakeSession:
        return self

    def scalars(self) -> _FakeSession:
        return self

    def all(self) -> list[Forecast]:
        return self._rows


def test_apply_autofit_clears_both_flags_and_writes_machine_fit() -> None:
    f = _fc(manual_override=True, locked=True)
    f.diagnostics = {"source": "mode_switch_arps_refit"}

    apply_autofit(f, _result(qi=321.0))

    assert f.manual_override is False
    assert f.locked is False
    assert f.qi == pytest.approx(321.0)
    assert f.params["qi"] == pytest.approx(321.0)
    assert f.diagnostics == {"source": "machine"}


def test_revert_reverts_fittable_streams_and_keeps_unfittable_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oil = _fc(manual_override=True, locked=False, stream=Stream.OIL)
    gas = _fc(manual_override=True, locked=False, stream=Stream.GAS)
    calls: list[tuple[str, bool]] = []

    def _fake_forecast_well(
        session: object, api10: str, *, persist: bool = True
    ) -> dict[str, ForecastResult | None]:
        calls.append((api10, persist))
        return {"oil": _result(), "gas": None, "water": None}

    monkeypatch.setattr(api_forecasts, "forecast_well", _fake_forecast_well)

    reverted, failed = revert_streams_to_autofit(
        _FakeSession([oil, gas]),  # type: ignore[arg-type]
        {"42100000000001": {"oil", "gas"}},
    )

    # One fit per well, and never persisted by the fitter itself (the
    # fitter's _persist would skip/guard; the revert writes the rows).
    assert calls == [("42100000000001", False)]
    assert reverted == [oil]
    assert oil.manual_override is False and oil.locked is False
    # The stream the fitter couldn't fit keeps the engineer's edit.
    assert failed == [("42100000000001", "gas")]
    assert gas.manual_override is True
    assert gas.params["qi"] == pytest.approx(500.0)
