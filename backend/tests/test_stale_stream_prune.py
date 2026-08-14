"""Stale-stream pruning on the no-peak skip path.

When a refit skips a stream (no detectable peak in the window) but a
forecast row for that stream already exists, the row's data basis is
gone — usually a vendor restatement (Novi production-sharing
re-allocation zeroing early water rewrote history under a stored fit;
UL 20 unit, 2026-08). A skip writes nothing, so without pruning the
stale row survives every refit and feeds the TC water band with a
phantom multi-MMbbl EUR.

Contract pinned here:
  * unlocked machine rows are deleted on skip;
  * ``locked`` rows are never touched (same contract as ``_persist``);
  * ``manual_override`` rows are engineer work — kept, never silently
    deleted (the manual-override guard is the surface for those);
  * pruning fires only when ``persist=True`` (previews never mutate).

DB-free like the rest of the suite: stub sessions for the unit cases,
monkeypatched internals for the forecast_well wiring.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pandas as pd
import pytest

from app.db.models import FitMethod, Forecast, ModelType, Stream
from app.forecasting import orchestrator
from app.forecasting.orchestrator import _prune_stale_stream_forecast, forecast_well


def _fc(*, manual_override: bool, locked: bool) -> Forecast:
    f = Forecast()
    f.id = uuid.uuid4()
    f.api10 = "4249533993"
    f.stream = Stream.WATER
    f.model_type = ModelType.MODIFIED_HYPERBOLIC
    f.params = {"qi": 3358.0, "Di": 1.48, "b": 1.0, "Df": 0.08}
    f.fit_method = FitMethod.RATE_CUM
    f.manual_override = manual_override
    f.locked = locked
    return f


class _StubResult:
    def __init__(self, row: Forecast | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Forecast | None:
        return self._row


class _StubSession:
    """Answers the single row lookup and records delete/commit calls."""

    def __init__(self, row: Forecast | None) -> None:
        self._row = row
        self.deleted: list[Forecast] = []
        self.commits = 0

    def execute(self, stmt: Any) -> _StubResult:
        return _StubResult(self._row)

    def delete(self, obj: Forecast) -> None:
        self.deleted.append(obj)

    def commit(self) -> None:
        self.commits += 1


def test_prune_deletes_unlocked_machine_row() -> None:
    row = _fc(manual_override=False, locked=False)
    s = _StubSession(row)
    _prune_stale_stream_forecast(s, api10=row.api10, stream="water")  # type: ignore[arg-type]
    assert s.deleted == [row]
    assert s.commits == 1


@pytest.mark.parametrize(
    ("manual_override", "locked"),
    [(False, True), (True, False), (True, True)],
)
def test_prune_keeps_locked_and_engineer_rows(manual_override: bool, locked: bool) -> None:
    row = _fc(manual_override=manual_override, locked=locked)
    s = _StubSession(row)
    _prune_stale_stream_forecast(s, api10=row.api10, stream="water")  # type: ignore[arg-type]
    assert s.deleted == []
    assert s.commits == 0


def test_prune_noop_when_no_row() -> None:
    s = _StubSession(None)
    _prune_stale_stream_forecast(s, api10="4249533993", stream="water")  # type: ignore[arg-type]
    assert s.deleted == []
    assert s.commits == 0


# ---- forecast_well wiring ------------------------------------------------


def _monthly_zero_water(n: int = 24) -> pd.DataFrame:
    """Realistic oil/gas decline with an all-zero water column — the
    restated-well shape (well HAS production; water's window is empty)."""
    oil = [400.0] + [900.0 / (1.0 + 2.0 * (m / 12.0)) for m in range(1, n)]
    gas = [r * 2.0 for r in oil]
    return pd.DataFrame(
        {
            "prod_date": [date(2023 + m // 12, m % 12 + 1, 1) for m in range(n)],
            "oil_bbl": [r * 30.4 for r in oil],
            "gas_mcf": [r * 30.4 for r in gas],
            "water_bbl": [0.0] * n,
            "producing_days": [30.0] * n,
            "rate_calday_bopd": oil,
            "rate_calday_mcfd": gas,
            "rate_calday_bwpd": [0.0] * n,
        }
    )


class _SubbasinSession:
    """Serves only the Well.subbasin lookup forecast_well makes."""

    def execute(self, stmt: Any) -> _StubResult:
        return _StubResult(None)


def _wire(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    persisted: list[str] = []
    pruned: list[str] = []

    def _fake_persist(s: Any, *, api10: str, stream: str, result: Any) -> uuid.UUID:
        persisted.append(stream)
        return uuid.uuid4()

    monkeypatch.setattr(orchestrator, "_load_monthly", lambda s, a: _monthly_zero_water())
    monkeypatch.setattr(orchestrator, "_persist", _fake_persist)
    monkeypatch.setattr(
        orchestrator,
        "_prune_stale_stream_forecast",
        lambda s, *, api10, stream: pruned.append(stream),
    )
    return persisted, pruned


def test_forecast_well_prunes_skipped_stream_on_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted, pruned = _wire(monkeypatch)
    out = forecast_well(_SubbasinSession(), "4249534169", persist=True)  # type: ignore[arg-type]
    assert pruned == ["water"]
    assert out["water"] is None
    # The producing streams still fit and persist normally.
    assert "oil" in persisted and "gas" in persisted
    assert out["oil"] is not None and out["gas"] is not None


def test_forecast_well_never_prunes_without_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, pruned = _wire(monkeypatch)
    out = forecast_well(_SubbasinSession(), "4249534169", persist=False)  # type: ignore[arg-type]
    assert pruned == []
    assert out["water"] is None
