"""Pin the technical-EUR rule: NO economic limit anywhere by default.

The tool is strictly a technical type-curve / decline generator — EUR is
the raw 50-yr integral and economics happens downstream on the export.
The API request body (``ForecastConfigBody``) once defaulted gas/water
limits to 30 MCFD / 50 BWPD while the canonical ``ForecastConfig``
defaulted to 0.0, so EURs became path-dependent: the batch endpoint
truncated gas/water tails while PATCH / TC overrides / CLI refit did
not. These tests keep every config surface pinned to the same 0.0.
"""

from __future__ import annotations

from app.api.forecasts import ForecastConfigBody
from app.forecasting.types import ForecastConfig


def test_api_body_defaults_match_canonical_config() -> None:
    body_cfg = ForecastConfigBody().to_config()
    canon = ForecastConfig()
    assert body_cfg.economic_limit_bopd == canon.economic_limit_bopd
    assert body_cfg.economic_limit_mcfd == canon.economic_limit_mcfd
    assert body_cfg.economic_limit_bwpd == canon.economic_limit_bwpd


def test_no_economic_limit_by_default_anywhere() -> None:
    # The canonical defaults themselves must stay 0.0 — a nonzero value
    # here would silently reintroduce econ-limit truncation tool-wide.
    canon = ForecastConfig()
    assert canon.economic_limit_bopd == 0.0
    assert canon.economic_limit_mcfd == 0.0
    assert canon.economic_limit_bwpd == 0.0
    body = ForecastConfigBody()
    assert body.economic_limit_bopd == 0.0
    assert body.economic_limit_mcfd == 0.0
    assert body.economic_limit_bwpd == 0.0
