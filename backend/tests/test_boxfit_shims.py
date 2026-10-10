"""``app.forecasting.*`` / ``app.type_curves.{aggregate,fit_p50,ratio_mode}``
are aliases of the boxfit modules (BOX step 5a) — one implementation, and a
monkeypatch through the old path still reaches the code that runs."""

from __future__ import annotations

import importlib

import pytest

_FORECASTING = (
    "types",
    "models",
    "metrics",
    "cumulative",
    "eur",
    "peak_detection",
    "ramp_arps",
    "ratio",
    "fit",
    "b_prior",
)
ALIASES = {
    **{f"app.forecasting.{m}": f"boxfit.{m}" for m in _FORECASTING},
    "app.type_curves.aggregate": "boxfit.tc.aggregate",
    "app.type_curves.fit_p50": "boxfit.tc.fit_p50",
    "app.type_curves.ratio_mode": "boxfit.tc.ratio_mode",
}


@pytest.mark.parametrize("old", sorted(ALIASES))
def test_old_path_is_the_boxfit_module(old: str) -> None:
    assert importlib.import_module(old) is importlib.import_module(ALIASES[old])


def test_orchestrator_reexports_the_pure_pipeline() -> None:
    from boxfit import well

    from app.forecasting import orchestrator

    assert orchestrator.detect_stream_peaks is well.detect_stream_peaks
    assert orchestrator.df_terminal_for_subbasin is well.df_terminal_for_subbasin
    assert orchestrator.fit_well_streams is well.fit_well_streams
