"""Short-history b regularization: the prior table, the taper, the pooled
bench curve, and the regularized fit itself. DB-free — the committed
``b_priors.json`` is only ever read through ``b_prior._load``, which the
lookup tests replace."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.forecasting import b_prior as bp
from app.forecasting import orchestrator
from app.forecasting.cumulative import cum_modified_hyperbolic
from app.forecasting.eur import DAYS_PER_YEAR
from app.forecasting.fit import fit_rate_cum, fit_rate_time, fit_with_fallback
from app.forecasting.peak_detection import PeakResult
from app.forecasting.types import ForecastConfig

_DPM = DAYS_PER_YEAR / 12.0


def _well(
    months: int, *, b: float, di: float = 2.5, noise: float = 0.0, seed: int = 0
) -> tuple[pd.DataFrame, PeakResult]:
    """Monthly oil frame from a modified-hyperbolic truth (qi 1000 BOPD,
    Df 8%/yr), optional multiplicative lognormal noise. Di is NOMINAL/yr —
    2.5/yr is ~72% effective in year 1 at b = 1."""
    t = np.arange(months + 1, dtype=float) / 12.0
    vol = np.diff(cum_modified_hyperbolic(t, 1000.0, di, b, 0.08))
    if noise:
        vol = vol * np.exp(np.random.default_rng(seed).normal(0.0, noise, months))
    dates = [date(2021 + (i // 12), 1 + (i % 12), 1) for i in range(months)]
    df = pd.DataFrame({"prod_date": dates, "oil_bbl": vol, "rate_calday_bopd": vol / _DPM})
    peak = PeakResult(peak_month_date=dates[0], peak_rate=float(vol[0] / _DPM), peak_index=0)
    return df, peak


# ---------------- taper ----------------


@pytest.mark.parametrize(
    ("n", "expected"),
    [(6, 1.0), (12, 1.0), (18, 0.75), (24, 0.5), (30, 0.25), (36, 0.0), (90, 0.0)],
)
def test_prior_weight_tapers_linearly_from_12_to_36_months(n: int, expected: float) -> None:
    cfg = ForecastConfig()
    w = bp.prior_weight(
        n,
        full_weight_months=cfg.b_prior_full_weight_months,
        zero_weight_months=cfg.b_prior_zero_weight_months,
    )
    assert w == pytest.approx(expected)


# ---------------- lookup chain ----------------


@pytest.fixture
def prior_table(monkeypatch: pytest.MonkeyPatch) -> None:
    table = {
        "bench": {"Delaware|WCA_1": {"oil": {"b": 1.11, "n_wells": 400}}},
        "subbasin": {"Delaware": {"oil": {"b": 1.05, "n_wells": 9000}}},
    }
    monkeypatch.setattr(bp, "_load", lambda: table)


@pytest.mark.usefixtures("prior_table")
def test_lookup_falls_back_bench_then_subbasin_then_default() -> None:
    hit = bp.lookup_b_prior("Delaware", "WCA_1", "oil")
    assert (hit.b, hit.source, hit.key, hit.n_wells) == (1.11, "bench", "Delaware|WCA_1", 400)

    # Bench has no gas entry and neither does the sub-basin -> default.
    assert bp.lookup_b_prior("Delaware", "WCA_1", "gas").source == "default"

    sub = bp.lookup_b_prior("Delaware", "BS9_X", "oil")
    assert (sub.b, sub.source, sub.key) == (1.05, "subbasin", "Delaware")

    # NULL formation_blueox uses the stack-wide '(unmapped)' key, then falls back.
    assert bp.bench_key("Delaware", None) == "Delaware|(unmapped)"
    assert bp.lookup_b_prior("Delaware", None, "oil").source == "subbasin"

    default = bp.lookup_b_prior("Midland", "LSSH", "oil")
    assert (default.b, default.source) == (bp.DEFAULT_B_PRIOR, "default")


def test_committed_prior_table_is_well_formed() -> None:
    bp._load.cache_clear()
    data = bp._load()
    assert data["bench"], "b_priors.json missing or empty — run app.cli.build_b_priors"
    for level in ("bench", "subbasin"):
        for key, streams in data[level].items():
            for stream, entry in streams.items():
                assert stream in ("oil", "gas", "water"), key
                assert 0.9 <= entry["b"] <= 1.2, (key, stream)  # production b bounds
                assert entry["n_wells"] >= bp.MIN_WELLS, (key, stream)
                assert entry["n_months"] >= bp.MIN_FIT_MONTHS, (key, stream)


# ---------------- pooled curve ----------------


def test_pooled_curve_is_the_median_and_stops_at_half_coverage() -> None:
    lenders = [
        {0: 1.0, 1: 0.8, 2: 0.6, 3: 0.5},
        {0: 1.0, 1: 0.7, 2: 0.5},
        {0: 1.0, 1: 0.9},  # downtime/short: absent months just don't vote
        {0: 1.0, 2: 0.4},
    ]
    # month 3 is reported by 1 of 4 lenders (< 50%) -> curve ends at month 2.
    assert bp.pooled_decline_curve(lenders) == pytest.approx([1.0, 0.8, 0.5])
    assert bp.pooled_decline_curve([]) == []


def test_fit_pooled_b_recovers_the_cohort_b() -> None:
    df, _ = _well(60, b=1.10)
    curve = list(df["rate_calday_bopd"] / df["rate_calday_bopd"].iloc[0])
    out = bp.fit_pooled_b(curve, stream="oil", df_terminal_per_year=0.08)
    assert out is not None
    assert out["b"] == pytest.approx(1.10, abs=0.01)
    assert out["b_at_bound"] is False
    assert bp.fit_pooled_b(curve[:20], stream="oil", df_terminal_per_year=0.08) is None


# ---------------- the regularized fit ----------------


def test_short_noisy_well_is_pulled_toward_the_prior_and_tagged() -> None:
    free_cfg, prior_cfg = ForecastConfig(), ForecastConfig(b_prior=1.10)
    free_err, reg_err = [], []
    for seed in range(12):
        df, peak = _well(14, b=1.10, noise=0.10, seed=seed)
        free = fit_rate_cum(df, peak=peak, stream="oil", config=free_cfg)
        reg = fit_rate_cum(df, peak=peak, stream="oil", config=prior_cfg)
        assert free.fit_method == "rate_cum" and free.diagnostics is None
        assert reg.fit_method == "rate_cum_bprior"
        info = reg.diagnostics["b_prior"]  # type: ignore[index]
        assert info["weight"] == pytest.approx(22 / 24, abs=1e-3)  # 14 months: inside the taper
        assert info["b_unregularized"] == pytest.approx(free.b)
        assert info["b_regularized"] == pytest.approx(reg.b)
        assert "regularized toward bench prior 1.10" in reg.notes
        # Never further from the prior than the free fit was.
        assert abs(reg.b - 1.10) <= abs(free.b - 1.10) + 1e-9
        free_err.append(abs(free.b - 1.10))
        reg_err.append(abs(reg.b - 1.10))
    # On average the pull is substantial, not cosmetic.
    assert np.mean(reg_err) < 0.5 * np.mean(free_err)


def test_long_history_fit_is_exactly_the_unregularized_fit() -> None:
    df, peak = _well(48, b=0.95, noise=0.10, seed=3)
    free = fit_rate_cum(df, peak=peak, stream="oil", config=ForecastConfig())
    reg = fit_rate_cum(df, peak=peak, stream="oil", config=ForecastConfig(b_prior=1.2))
    assert reg.fit_method == "rate_cum" and reg.diagnostics is None
    assert reg.params == free.params and reg.eur == free.eur


def test_clean_data_overrules_a_wrong_prior() -> None:
    """sigma_y -> 0 means the data already determine b; the prior's pull
    scales with sigma_y, so it vanishes instead of dragging a clean fit."""
    df, peak = _well(14, b=0.95)
    reg = fit_rate_cum(df, peak=peak, stream="oil", config=ForecastConfig(b_prior=1.2))
    assert reg.b == pytest.approx(0.95, abs=0.01)


def test_prior_is_clipped_into_the_b_bounds_and_only_b_is_regularized() -> None:
    df, peak = _well(10, b=1.0, noise=0.10, seed=1)
    reg = fit_rate_cum(df, peak=peak, stream="oil", config=ForecastConfig(b_prior=1.6))
    assert reg.diagnostics["b_prior"]["b_prior"] == pytest.approx(1.2)  # type: ignore[index]
    assert 0.9 <= reg.b <= 1.2  # type: ignore[operator]
    # R²/RMSE are on the data, never the pseudo-observation.
    assert 0.0 < reg.fit_r2 <= 1.0


def test_rate_time_and_fallback_carry_the_prior_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    df, peak = _well(14, b=1.0, noise=0.10, seed=5)
    cfg = ForecastConfig(b_prior=1.05)
    rt = fit_rate_time(df, peak=peak, stream="oil", config=cfg)
    assert rt.fit_method == "rate_time" and rt.diagnostics is not None

    # Force the Di-at-bound trigger: a cum fit that pins Di makes
    # fit_with_fallback adopt the (regularized) rate-time result.
    pinned = replace(fit_rate_cum(df, peak=peak, stream="oil", config=cfg), di_initial=4.0)
    monkeypatch.setattr("app.forecasting.fit.fit_rate_cum", lambda *a, **k: pinned)
    out = fit_with_fallback(df, peak=peak, stream="oil", config=cfg)
    assert out.fit_method == "rate_time_fallback_bprior"
    assert out.diagnostics == rt.diagnostics


# ---------------- orchestrator wiring ----------------


class _WellRow:
    subbasin = "Delaware"
    formation_blueox = "WCA_1"


class _Result:
    def one_or_none(self) -> _WellRow:
        return _WellRow()


class _Session:
    def execute(self, stmt: object) -> _Result:
        return _Result()


def _three_stream_monthly(months: int) -> pd.DataFrame:
    df, _ = _well(months, b=1.0, noise=0.10, seed=2)
    oil = df["rate_calday_bopd"]
    return pd.DataFrame(
        {
            "prod_date": df["prod_date"],
            "oil_bbl": df["oil_bbl"],
            "gas_mcf": df["oil_bbl"] * 2.0,
            "water_bbl": df["oil_bbl"] * 3.0,
            "producing_days": 30.0,
            "rate_calday_bopd": oil,
            "rate_calday_mcfd": oil * 2.0,
            "rate_calday_bwpd": oil * 3.0,
        }
    )


@pytest.mark.usefixtures("prior_table")
def test_forecast_well_applies_the_bench_prior_per_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orchestrator, "_load_monthly", lambda s, a: _three_stream_monthly(14))
    out = orchestrator.forecast_well(_Session(), "4230100001", persist=False)  # type: ignore[arg-type]

    oil = out["oil"]
    assert oil is not None and oil.fit_method == "rate_cum_bprior"
    info = oil.diagnostics["b_prior"]  # type: ignore[index]
    assert (info["b_prior"], info["source"], info["key"], info["n_wells"]) == (
        1.11,
        "bench",
        "Delaware|WCA_1",
        400,
    )
    # Gas has no bench/sub-basin entry in the fixture table -> default prior.
    gas = out["gas"]
    assert gas is not None
    assert gas.diagnostics["b_prior"]["source"] == "default"  # type: ignore[index]
    assert gas.diagnostics["b_prior"]["b_prior"] == bp.DEFAULT_B_PRIOR  # type: ignore[index]


@pytest.mark.usefixtures("prior_table")
@pytest.mark.parametrize(
    "cfg",
    [
        ForecastConfig(b_prior_enabled=False),
        ForecastConfig(fit_method="rate_time"),
        ForecastConfig(fit_method="rate_cum_strict"),
    ],
)
def test_prior_is_off_when_disabled_or_on_explicit_fit_methods(
    monkeypatch: pytest.MonkeyPatch, cfg: ForecastConfig
) -> None:
    monkeypatch.setattr(orchestrator, "_load_monthly", lambda s, a: _three_stream_monthly(14))
    out = orchestrator.forecast_well(_Session(), "4230100001", config=cfg, persist=False)  # type: ignore[arg-type]
    for result in out.values():
        assert result is not None and result.diagnostics is None
        assert not result.fit_method.endswith("_bprior")


@pytest.mark.usefixtures("prior_table")
def test_long_history_well_is_untouched_by_the_prior(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "_load_monthly", lambda s, a: _three_stream_monthly(48))
    out = orchestrator.forecast_well(_Session(), "4230100001", persist=False)  # type: ignore[arg-type]
    assert out["oil"] is not None and out["oil"].fit_method in ("rate_cum", "rate_time_fallback")
    assert out["oil"].diagnostics is None
