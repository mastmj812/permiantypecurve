"""Ratio-driven forecasting (gas/water vs cumulative oil) — DB-free tests.

Covers the design brief's required cases:
  * synthetic constant-WOR well → the fit recovers k with beta ~ 0;
  * synthetic rising-WOR well → beta recovered within tolerance;
  * the two integration paths (stored midpoint-volume EUR vs trapezoid
    over the derived rate grid) reconcile < 0.1%;
  * beta is clipped to the data-derived band (no exploding exponential);
  * the export guard refuses ratio-mode zones (Blue Ox helper + dossier);
  * a mode switch stamps manual_override=True, locked=True;
  * a TC ratio stream builds from the cohort's aggregated mean series.
"""

from __future__ import annotations

import math
import uuid
from datetime import date

import pytest

from app.db.models import Stream, TypeCurve
from app.db.models.forecasts import FitMethod, Forecast, ModelType
from app.exports.blueox import RATIO_REFUSAL_NOTE, ratio_mode_streams
from app.forecasting.ramp_arps import build_ramp_arps_rate, trapezoid_eur
from app.forecasting.ratio import (
    RATIO_MAX_ABS_LN_DRIFT,
    derive_ratio_rates_masked,
    derive_ratio_stream,
    fit_ratio_vs_cum_oil,
    implied_effective_decline_yr1,
    is_ratio_params,
    ratio_forecast_from_oil_params,
)
from app.type_curves.ratio_mode import (
    build_ratio_fitted,
    ratio_override_conflicts,
    stream_modes_from_series,
    validate_stream_modes,
)

# ---------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------


def _oil_volumes(n: int = 30, q0: float = 15_000.0, decl: float = 0.06) -> list[float]:
    """Synthetic monthly oil volumes (BBL/month), exponential-ish decline."""
    return [q0 * math.exp(-decl * i) for i in range(n)]


def _np_mid(oil: list[float]) -> list[float]:
    """Cumulative oil at month midpoints — same rule the fitter uses."""
    out = []
    cum = 0.0
    for v in oil:
        out.append(cum + v / 2.0)
        cum += v
    return out


def test_constant_wor_recovers_k_with_zero_beta():
    oil = _oil_volumes()
    water = [0.5 * v for v in oil]
    fit = fit_ratio_vs_cum_oil(oil, water, start_index=0, np_max_forecast=400_000.0)
    assert fit is not None
    assert fit.sub_mode == "exp_cum"
    # ln r is exactly constant → slope is numerically zero and the
    # regression reproduces the target (zero-variance R² convention: 1).
    np_window = fit.diagnostics["np_window"]
    assert abs(fit.beta) * np_window < 1e-6
    assert math.exp(fit.alpha) == pytest.approx(0.5, rel=1e-9)
    assert fit.r2 == pytest.approx(1.0)
    assert fit.n_months == len(oil)


def test_rising_wor_beta_recovered():
    beta_true = 2.0e-6  # per BBL of cum oil
    oil = _oil_volumes(n=36)
    mids = _np_mid(oil)
    water = [o * 0.3 * math.exp(beta_true * m) for o, m in zip(oil, mids, strict=True)]
    # np_max chosen inside the band: cap = 3.0 / 1e6 = 3e-6 > beta_true,
    # so the recovered slope is the unconstrained LSQ solution.
    fit = fit_ratio_vs_cum_oil(oil, water, start_index=0, np_max_forecast=1_000_000.0)
    assert fit is not None
    assert fit.sub_mode == "exp_cum"
    assert fit.diagnostics["beta_clipped"] is False
    assert fit.beta == pytest.approx(beta_true, rel=0.01)
    assert math.exp(fit.alpha) == pytest.approx(0.3, rel=0.01)
    assert fit.r2 is not None and fit.r2 > 0.99


def test_post_peak_window_and_zero_months_excluded():
    """Months before the oil peak and zero-stream months don't enter the
    regression, but Np still accumulates from month 0 (true cum oil)."""
    oil = _oil_volumes(n=20)
    water = [0.4 * v for v in oil]
    water[5] = 0.0  # a zero-stream month — no finite ln r
    fit = fit_ratio_vs_cum_oil(oil, water, start_index=3, np_max_forecast=400_000.0)
    assert fit is not None
    # 20 months - 3 pre-peak - 1 zero month = 16 valid
    assert fit.n_months == 16
    assert math.exp(fit.alpha) == pytest.approx(0.4, rel=1e-6)


def test_short_history_falls_back_to_constant_median():
    oil = [10_000.0] * 5
    ratios = [0.2, 0.3, 0.4, 0.5, 0.6]
    water = [o * r for o, r in zip(oil, ratios, strict=True)]
    fit = fit_ratio_vs_cum_oil(oil, water, start_index=0, np_max_forecast=400_000.0)
    assert fit is not None
    assert fit.sub_mode == "constant"  # n=5 < 6-month gate
    assert fit.beta == 0.0
    assert fit.r_const == pytest.approx(0.4)
    assert math.exp(fit.alpha) == pytest.approx(0.4)
    assert "rejected_fit" in fit.diagnostics


def test_low_r2_falls_back_to_constant():
    oil = [10_000.0] * 12
    # Ratio alternates with no Np trend — ln-fit R² ~ 0 < 0.3 gate.
    water = [o * (0.2 if i % 2 == 0 else 0.8) for i, o in enumerate(oil)]
    fit = fit_ratio_vs_cum_oil(oil, water, start_index=0, np_max_forecast=400_000.0)
    assert fit is not None
    assert fit.sub_mode == "constant"
    # median of the last 6 valid months' ratios (0.2/0.8 alternating)
    assert fit.r_const == pytest.approx(0.5)
    assert fit.r2 is not None and fit.r2 < 0.3


def test_beta_clipped_to_data_derived_band():
    beta_true = 2.0e-6
    oil = _oil_volumes(n=36)
    mids = _np_mid(oil)
    water = [o * 0.3 * math.exp(beta_true * m) for o, m in zip(oil, mids, strict=True)]
    # Small forecast Np → tight band: cap = 3.0 / np_max < beta_true.
    np_max = RATIO_MAX_ABS_LN_DRIFT / 1.0e-6  # cap works out to exactly 1e-6
    fit = fit_ratio_vs_cum_oil(oil, water, start_index=0, np_max_forecast=np_max)
    assert fit is not None
    assert fit.diagnostics["beta_clipped"] is True
    assert fit.beta == pytest.approx(1.0e-6)
    assert fit.diagnostics["beta_unclipped"] == pytest.approx(beta_true, rel=0.01)
    # Structured misfit from the clipped slope, but still well over the
    # gate → stays exp_cum with the bounded slope.
    assert fit.sub_mode == "exp_cum"


def test_no_valid_months_returns_none():
    oil = [0.0] * 10
    water = [0.0] * 10
    assert fit_ratio_vs_cum_oil(oil, water, start_index=0, np_max_forecast=1e5) is None


# ---------------------------------------------------------------------
# forecast evaluation + the two-integration-paths invariant
# ---------------------------------------------------------------------

_OIL_PARAMS = {
    "qi": 800.0,  # BOPD
    "Di": 2.0,  # nominal /yr (~67% effective yr-1 at b=1)
    "b": 1.0,
    "Df": 0.08,
    "qo": 300.0,
    "peak_index_months": 2,
}


def _oil_rates_600() -> list[float]:
    return build_ramp_arps_rate(
        n_months=600,
        qo=_OIL_PARAMS["qo"],
        qi=_OIL_PARAMS["qi"],
        peak_index=int(_OIL_PARAMS["peak_index_months"]),
        Di=_OIL_PARAMS["Di"],
        b=_OIL_PARAMS["b"],
        Df=_OIL_PARAMS["Df"],
    )


@pytest.mark.parametrize("beta", [1.0e-6, -1.0e-6, 0.0])
def test_integration_paths_reconcile_below_point1_pct(beta: float):
    """Stored EUR (midpoint-volume sum) vs recompute (trapezoid over the
    derived rate grid) — the ratio-stream analog of the closed-form-vs-
    trapezoid Arps invariant. Must stay < 0.1%."""
    series = ratio_forecast_from_oil_params(
        qi=_OIL_PARAMS["qi"],
        Di=_OIL_PARAMS["Di"],
        b=_OIL_PARAMS["b"],
        Df=_OIL_PARAMS["Df"],
        qo=_OIL_PARAMS["qo"],
        peak_index_months=int(_OIL_PARAMS["peak_index_months"]),
        alpha=math.log(0.6),
        beta=beta,
    )
    recomputed = trapezoid_eur(series.rates)
    rel = abs(series.eur - recomputed) / series.eur
    assert rel < 1e-3, f"integration paths diverge: {rel:.6%}"


def test_np_bounded_by_oil_eur_and_ratio_cannot_explode():
    oil_rates = _oil_rates_600()
    oil_eur = trapezoid_eur(oil_rates)
    beta_cap = RATIO_MAX_ABS_LN_DRIFT / oil_eur
    series = derive_ratio_stream(oil_rates, alpha=math.log(0.6), beta=beta_cap)
    # Np trajectory is the oil integral — bounded by oil's EUR.
    assert series.np_total == pytest.approx(oil_eur, rel=1e-9)
    # At the band edge the ratio drifts at most e^RATIO_MAX_ABS_LN_DRIFT.
    max_ratio = max(
        r / q for r, q in zip(series.rates, oil_rates, strict=True) if q > 1e-9
    )
    assert max_ratio <= 0.6 * math.exp(RATIO_MAX_ABS_LN_DRIFT) * (1 + 1e-9)
    assert all(math.isfinite(v) for v in series.rates)


def test_constant_sub_mode_scales_oil_eur_exactly():
    oil_rates = _oil_rates_600()
    series = derive_ratio_stream(oil_rates, alpha=math.log(0.5), beta=0.0)
    assert series.eur == pytest.approx(0.5 * trapezoid_eur(oil_rates), rel=1e-12)


def test_masked_rates_preserve_nulls():
    oil = [None, None, 100.0, 90.0, 80.0]
    out = derive_ratio_rates_masked(oil, alpha=math.log(0.5), beta=0.0)
    assert out[0] is None and out[1] is None
    assert out[2] == pytest.approx(50.0)


def test_implied_effective_decline_from_derived_series():
    series = ratio_forecast_from_oil_params(
        qi=_OIL_PARAMS["qi"],
        Di=_OIL_PARAMS["Di"],
        b=_OIL_PARAMS["b"],
        Df=_OIL_PARAMS["Df"],
        qo=_OIL_PARAMS["qo"],
        peak_index_months=int(_OIL_PARAMS["peak_index_months"]),
        alpha=math.log(0.6),
        beta=5.0e-7,
    )
    eff = implied_effective_decline_yr1(series.rates)
    assert eff is not None
    # A declining oil stream with a slowly rising ratio still declines
    # in year 1 — sanity band, not a pinned baseline.
    assert 0.2 < eff < 0.95


# ---------------------------------------------------------------------
# mode switch — manual_override + locked convention
# ---------------------------------------------------------------------


def test_apply_ratio_mode_sets_manual_override_and_locked():
    from app.api.forecasts import apply_ratio_mode

    oil = _oil_volumes(n=24)
    water = [0.5 * v for v in oil]
    fit = fit_ratio_vs_cum_oil(oil, water, start_index=0, np_max_forecast=400_000.0)
    assert fit is not None
    series = ratio_forecast_from_oil_params(
        qi=_OIL_PARAMS["qi"],
        Di=_OIL_PARAMS["Di"],
        b=_OIL_PARAMS["b"],
        Df=_OIL_PARAMS["Df"],
        alpha=fit.alpha,
        beta=fit.beta,
    )
    f = Forecast(
        id=uuid.uuid4(),
        api10="4200000000",
        stream=Stream.WATER,
        model_type=ModelType.MODIFIED_HYPERBOLIC,
        params={"qi": 500.0, "Di": 3.0, "b": 1.0, "Df": 0.08},
        fit_method=FitMethod.RATE_CUM,
        manual_override=False,
        locked=False,
    )
    apply_ratio_mode(
        f,
        fit=fit,
        series=series,
        peak_month_date=date(2023, 2, 1),
        peak_rate=250.0,
        oil_forecast_id=str(uuid.uuid4()),
        oil_eur_at_fit=400_000.0,
    )
    assert f.manual_override is True
    assert f.locked is True
    assert f.model_type == ModelType.RATIO
    assert f.fit_method == FitMethod.RATIO_CUM_OIL
    assert is_ratio_params(f.params)
    assert f.params["sub_mode"] == "exp_cum"
    # No Arps params on a ratio row — the columns are nulled.
    assert f.qi is None and f.di_initial is None and f.b is None and f.df_terminal is None
    assert f.eur == pytest.approx(series.eur)
    assert f.diagnostics is not None
    assert f.diagnostics["implied_eur"] == pytest.approx(series.eur)


def test_apply_arps_refit_sets_manual_override_and_locked():
    from app.api.forecasts import apply_arps_refit
    from app.forecasting.types import ForecastResult

    result = ForecastResult(
        model_type="modified_hyperbolic",
        params={"qi": 480.0, "Di": 2.5, "b": 1.0, "Df": 0.08},
        qi=480.0,
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
    )
    f = Forecast(
        id=uuid.uuid4(),
        api10="4200000000",
        stream=Stream.WATER,
        model_type=ModelType.RATIO,
        params={"mode": "ratio", "alpha": -0.7, "beta": 1e-6},
        fit_method=FitMethod.RATIO_CUM_OIL,
        manual_override=True,
        locked=True,
    )
    apply_arps_refit(f, result)
    assert f.manual_override is True
    assert f.locked is True
    assert f.model_type == ModelType.MODIFIED_HYPERBOLIC
    assert f.fit_method == FitMethod.RATE_CUM
    assert f.qi == pytest.approx(480.0)
    assert not is_ratio_params(f.params)


# ---------------------------------------------------------------------
# type-curve ratio streams (cohort aggregates)
# ---------------------------------------------------------------------


def _tc_oil_fitted(n_months: int = 600) -> tuple[dict, list[float]]:
    """A per-1,000-ft oil fitted block + its mean series."""
    params = {"qi": 60.0, "Di": 2.0, "b": 1.0, "Df": 0.08, "qo": 25.0, "peak_index": 2}
    grid = build_ramp_arps_rate(
        n_months=n_months,
        qo=params["qo"],
        qi=params["qi"],
        peak_index=params["peak_index"],
        Di=params["Di"],
        b=params["b"],
        Df=params["Df"],
    )
    fitted = {**params, "eur_per_unit": trapezoid_eur(grid)}
    return fitted, grid


def test_tc_ratio_stream_builds_from_cohort_mean_series():
    oil_fitted, oil_grid = _tc_oil_fitted()
    mean_oil = oil_grid[:60]  # observed-length mean series
    mean_water = [0.4 * v for v in mean_oil]
    fitted = build_ratio_fitted(
        mean_oil=list(mean_oil),
        mean_stream=mean_water,
        oil_fitted=oil_fitted,
        n_months=600,
    )
    assert fitted is not None
    assert fitted["mode"] == "ratio"
    assert fitted["sub_mode"] == "exp_cum"
    np_window = fitted["diagnostics"]["np_window"]
    assert abs(fitted["beta"]) * np_window < 1e-6
    assert math.exp(fitted["alpha"]) == pytest.approx(0.4, rel=1e-6)
    # Constant 0.4 ratio → derived EUR = 0.4 x the oil fitted EUR.
    assert fitted["eur_per_unit"] == pytest.approx(
        0.4 * oil_fitted["eur_per_unit"], rel=1e-3
    )
    assert len(fitted["smoothed_rate"]) == 600
    # No Arps keys — the param table renders em-dashes, the export
    # guards refuse.
    assert "qi" not in fitted and "Di" not in fitted


def test_tc_ratio_stream_none_when_mean_series_unusable():
    oil_fitted, _ = _tc_oil_fitted()
    assert (
        build_ratio_fitted(
            mean_oil=[0.0] * 24,
            mean_stream=[0.0] * 24,
            oil_fitted=oil_fitted,
            n_months=600,
        )
        is None
    )


def test_validate_stream_modes():
    assert validate_stream_modes(None) == {}
    assert validate_stream_modes({"gas": "arps"}) == {}
    assert validate_stream_modes({"water": "ratio"}) == {"water": "ratio"}
    with pytest.raises(ValueError, match="oil is always"):
        validate_stream_modes({"oil": "ratio"})
    with pytest.raises(ValueError, match="must be 'arps' or 'ratio'"):
        validate_stream_modes({"gas": "wor"})


def _series_with_ratio_water() -> dict:
    return {
        "streams": {
            "oil": {"fitted": {"qi": 60.0, "Di": 2.0, "b": 1.0, "Df": 0.08}},
            "gas": {"fitted": {"qi": 200.0, "Di": 2.0, "b": 1.0, "Df": 0.08}},
            "water": {"fitted": {"mode": "ratio", "alpha": -0.7, "beta": 1e-6}},
        }
    }


def test_stream_modes_survive_series_roundtrip():
    series = _series_with_ratio_water()
    assert stream_modes_from_series(series) == {"water": "ratio"}
    assert stream_modes_from_series({}) == {}
    assert stream_modes_from_series(None) == {}


def test_ratio_override_conflicts_detected():
    series = _series_with_ratio_water()
    assert ratio_override_conflicts(series, {"water": {"qi": 1.0}}) == ["water"]
    assert ratio_override_conflicts(series, {"gas": {"qi": 1.0}}) == []
    assert ratio_override_conflicts(series, None) == []


def test_loader_resolves_ratio_params():
    from app.type_curves.loader import _resolve_params

    tc = TypeCurve()
    tc.forecast_overrides = {}
    f = Forecast(
        id=uuid.uuid4(),
        api10="4200000000",
        stream=Stream.WATER,
        model_type=ModelType.RATIO,
        params={"mode": "ratio", "alpha": -0.7, "beta": 1e-6},
        fit_method=FitMethod.RATIO_CUM_OIL,
    )
    resolved = _resolve_params(tc, "4200000000", Stream.WATER, f)
    assert resolved == {"mode": "ratio", "alpha": -0.7, "beta": 1e-6}


# ---------------------------------------------------------------------
# export guards
# ---------------------------------------------------------------------


def test_ratio_mode_streams_helper():
    assert ratio_mode_streams(_series_with_ratio_water()) == ["water"]
    arps_only = {
        "streams": {
            "oil": {"fitted": {"qi": 60.0}},
            "gas": {"fitted": {"qi": 200.0}},
            "water": {"fitted": {"qi": 30.0}},
        }
    }
    assert ratio_mode_streams(arps_only) == []
    assert ratio_mode_streams(None) == []
    assert ratio_mode_streams({}) == []


class _StubTC:
    name = "wcb_test"
    series = _series_with_ratio_water()


class _StubSession:
    def get(self, model, key):  # duck-typed Session stand-in
        return _StubTC()


def test_dossier_export_refuses_ratio_mode_curve():
    from app.exports.dossier import CurveSlideInput, build_deal_dossier_pptx

    with pytest.raises(ValueError, match="ratio-mode"):
        build_deal_dossier_pptx(
            _StubSession(),  # type: ignore[arg-type]
            scenarios=[],
            curves=[CurveSlideInput(type_curve_id=uuid.uuid4())],
        )


def test_refusal_note_names_the_remedy():
    assert "refit the stream as Arps" in RATIO_REFUSAL_NOTE
    assert "contract amendment" in RATIO_REFUSAL_NOTE
