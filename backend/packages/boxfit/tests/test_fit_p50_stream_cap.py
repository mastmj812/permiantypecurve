"""fit_p50_series bounds each stream by its OWN nominal-Di cap (rule 1): water 12/yr, oil/gas 4/yr."""

from __future__ import annotations

import numpy as np
import pytest
from boxfit.tc.fit_p50 import fit_p50_series


def _steep(di: float = 8.0, b: float = 1.0, qi: float = 500.0, n: int = 120) -> list[float]:
    """Point-sampled hyperbolic per-kft series with a 1-month ramp, steeper than the oil cap."""
    t = np.arange(n) / 12.0
    q = qi / (1.0 + b * di * t) ** (1.0 / b)
    return [0.6 * qi, *q[: n - 1].tolist()]


def test_water_gets_its_own_cap() -> None:
    oil = fit_p50_series(_steep())
    water = fit_p50_series(_steep(), stream="water")
    assert oil is not None and water is not None
    assert oil["Di"] == pytest.approx(4.0, abs=1e-6)  # pinned at the oil bound
    assert 4.0 < water["Di"] < 12.0  # free under the water cap
    assert water["r2"] >= oil["r2"]


def test_oil_and_gas_unchanged_by_naming_their_stream() -> None:
    s = _steep(di=2.5, b=1.05)
    base = fit_p50_series(s)
    for stream in ("oil", "gas"):
        assert fit_p50_series(s, stream=stream) == base


def test_unknown_stream_rejected() -> None:
    with pytest.raises(ValueError):
        fit_p50_series(_steep(), stream="ngl")


def test_tc_fit_uses_every_month_of_a_low_rate_series(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-1,000-ft cohort series falls below the per-well floors (5 bopd absolute, 1.0 clip)
    within a few years. Per-well fits keep the downtime filter; the TC fitter turns it off so every
    post-peak month is fitted instead of only the first few years."""
    import boxfit.tc.fit_p50 as fp
    from boxfit.fit import fit_rate_cum
    from boxfit.peak_detection import PeakResult, detect_oil_peak
    from boxfit.ramp_arps import build_ramp_arps_rate
    from boxfit.types import ForecastConfig

    series = build_ramp_arps_rate(n_months=600, qo=15.0, qi=30.0, peak_index=1, Di=2.0, b=1.1, Df=0.08)
    df = fp._make_monthly_df(series)
    pk = detect_oil_peak(df)
    post = df.iloc[pk.peak_index :].reset_index(drop=True)
    peak0 = PeakResult(pk.peak_month_date, pk.peak_rate, 0)
    kw = {"model_type": "modified_hyperbolic", "peak": peak0, "stream": "oil"}
    base = {"min_post_peak_months": 3, "qi_anchor_hi_basis": "instantaneous"}
    filtered = fit_rate_cum(post, config=ForecastConfig(**base), **kw)  # per-well default
    unfiltered = fit_rate_cum(post, config=ForecastConfig(**base, filter_downtime=False), **kw)
    assert ForecastConfig().filter_downtime
    assert unfiltered.n_points_fit == len(post)
    assert filtered.n_points_fit < len(post) // 4  # the per-well floors cut the tail of a per-kft series

    seen: list[ForecastConfig] = []
    real = fp.fit_rate_cum

    def spy(*a, **k):  # type: ignore[no-untyped-def]
        seen.append(k["config"])
        return real(*a, **k)

    monkeypatch.setattr(fp, "fit_rate_cum", spy)
    assert fit_p50_series(series) is not None
    assert seen and seen[0].filter_downtime is False
