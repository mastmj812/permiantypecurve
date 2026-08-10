"""Water-specific fit behavior: the qi-underfit rate-time fallback and the
raised water Di cap.

The cum fit integrates a hard early water peak away, landing qi far below
the observed peak with a shallow Di (see project memory
project_water_cum_fit_smoothing). fit_with_fallback rescues this for
water; oil/gas are unaffected. Tests use a synthetic hard-early-peak
series shaped like real Permian flowback.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.forecasting.eur import DAYS_PER_YEAR
from app.forecasting.fit import (
    DI_NOMINAL_HI_PER_YEAR,
    _bounds_and_p0,
    _di_at_bound,
    _stream_di_hi,
    detect_at_bound,
    fit_rate_cum,
    fit_with_fallback,
)
from app.forecasting.peak_detection import detect_peak
from app.forecasting.types import ForecastConfig

_DAYS_PER_MONTH = DAYS_PER_YEAR / 12.0

# Hard early water peak shaped from the motivating real well (api10
# 4222740500): spikes at month 1, craters over two months, then a long
# low tail with mid-life frac-hit bumps. The decline is steeper than the
# 12/yr water Di cap AND the noisy tail pulls the cumulative integral, so
# the cum fit lands qi well under the peak even with the raised cap — the
# case the rate-time fallback exists to rescue. See project memory
# project_water_cum_fit_smoothing.
_HARD_WATER = [
    729, 854, 320, 182, 157, 140, 93, 107, 72, 66, 52, 89, 62, 75, 117,
    183, 103, 95, 100, 75, 61, 46, 62, 64, 61, 67, 64, 60, 54, 52, 49,
    64, 63, 59, 57, 49, 71, 66, 58, 48, 56, 29, 1, 105, 174, 130, 124,
    104, 96, 121, 48, 42, 42, 50, 33, 22, 22, 16, 0, 41, 62, 46, 39,
    39, 42,
]


def _water_frame(rates: list[float]) -> pd.DataFrame:
    months = []
    y, m = 2023, 1
    for _ in range(len(rates)):
        months.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return pd.DataFrame(
        {
            "prod_date": months,
            "rate_calday_bwpd": [float(r) for r in rates],
            "water_bbl": [float(r) * _DAYS_PER_MONTH for r in rates],
        }
    )


def test_stream_di_hi_raises_cap_for_water_only() -> None:
    cfg = ForecastConfig()
    assert _stream_di_hi("oil", cfg) == DI_NOMINAL_HI_PER_YEAR
    assert _stream_di_hi("gas", cfg) == DI_NOMINAL_HI_PER_YEAR
    assert _stream_di_hi("water", cfg) == cfg.water_di_nominal_hi_per_year
    assert cfg.water_di_nominal_hi_per_year > DI_NOMINAL_HI_PER_YEAR


def test_bounds_apply_overridden_di_hi() -> None:
    (lower, upper), _p0 = _bounds_and_p0(
        "modified_hyperbolic", 1000.0, di_hi=12.0
    )
    assert upper[1] == 12.0  # Di upper bound is the override


def test_water_qi_underfit_triggers_rate_time_fallback() -> None:
    df = _water_frame(_HARD_WATER)
    peak = detect_peak(df, rate_column="rate_calday_bwpd")
    assert peak is not None

    # Peak-anchored qi (the default) makes a qi under-pick impossible, so
    # this fallback path is only reachable with anchoring off. Exercise it
    # in isolation with anchoring disabled — it's still the fallback for
    # callers that opt out of qi-anchoring.
    cfg = ForecastConfig(qi_anchor_lo_frac=None, qi_anchor_hi_frac=None)
    primary = fit_rate_cum(df, peak=peak, stream="water", config=cfg)
    # The cum fit smooths the peak away: qi lands well under the observed
    # peak (this is the precondition for the fallback to fire).
    assert primary.qi < 0.6 * peak.peak_rate

    rescued = fit_with_fallback(df, peak=peak, stream="water", config=cfg)
    assert rescued.fit_method == "rate_time_fallback"
    # Rate-time recovers far more of the peak than the cum fit did.
    assert rescued.qi > primary.qi
    # And it captures the steep early decline the cum fit missed — water
    # is allowed past the oil Di cap.
    assert rescued.di_initial > DI_NOMINAL_HI_PER_YEAR
    assert "water cum qi" in rescued.notes


def test_oil_with_same_shape_does_not_use_water_qi_underfit_path() -> None:
    # Same hard-early shape but fit as OIL: the water-only qi-underfit
    # trigger must not apply. (Oil may still fall back via the Di-at-bound
    # path, but if it does it carries the Di-bound note, never the water
    # note — that's what we assert here.)
    df = _water_frame(_HARD_WATER).rename(
        columns={"rate_calday_bwpd": "rate_calday_bopd", "water_bbl": "oil_bbl"}
    )
    peak = detect_peak(df, rate_column="rate_calday_bopd")
    assert peak is not None
    result = fit_with_fallback(df, peak=peak, stream="oil")
    assert "water cum qi" not in result.notes


def test_bound_badge_di_cap_is_per_stream() -> None:
    # Di = 8/yr sits below the water cap (12) but above the oil cap (4).
    # So it must NOT flag as at-bound for water, while it WOULD for oil —
    # the whole point of the per-stream badge.
    _, water_note = detect_at_bound(
        qi=500, di=8.0, b=1.0, peak_rate=1000, di_hi=12.0
    )
    _, oil_note = detect_at_bound(
        qi=500, di=8.0, b=1.0, peak_rate=1000, di_hi=DI_NOMINAL_HI_PER_YEAR
    )
    assert "Di at upper bound" not in (water_note or "")
    assert "Di at upper bound (4.0)" in (oil_note or "")


def test_bound_badge_note_reports_the_passed_cap() -> None:
    # A water fit pinned near its 12.0 cap should read "(12.0)", not the
    # oil-tuned "(4.0)".
    at_bound, note = detect_at_bound(
        qi=500, di=11.9, b=1.0, peak_rate=1000, di_hi=12.0
    )
    assert at_bound is True
    assert "Di at upper bound (12.0)" in note


def test_di_at_bound_respects_stream_cap() -> None:
    # The fallback trigger must judge "pinned" against the SAME cap the
    # stream's fit ran with — water's 12.0, not the oil-tuned 4.0. With
    # the oil cap hardcoded, every water fit with Di in (3.92, 11.76)
    # read as pinned and needlessly fired (or wrongly adopted) the
    # rate-time fallback.
    assert _di_at_bound(6.0) is True  # oil cap: 6.0 > 4.0 → pinned
    assert _di_at_bound(6.0, di_hi=12.0) is False  # water: mid-band
    assert _di_at_bound(11.9, di_hi=12.0) is True  # water: at its cap
    assert _di_at_bound(0.505, di_hi=12.0) is True  # lower bound, shared
    assert _di_at_bound(None, di_hi=12.0) is False


def test_mid_band_water_di_does_not_trigger_fallback() -> None:
    # Harmonic-shaped water decline with nominal Di ≈ 6/yr — steeper
    # than the oil cap (4.0) but comfortably inside the water cap (12.0).
    # The cum fit recovers it directly; the Di-at-bound trigger must NOT
    # fire (pre-fix it did, judging Di=6 against the oil cap).
    rates = [800.0 / (1.0 + 6.0 * (i / 12.0)) for i in range(36)]
    df = _water_frame(rates)
    peak = detect_peak(df, rate_column="rate_calday_bwpd")
    assert peak is not None
    result = fit_with_fallback(df, peak=peak, stream="water")
    assert result.fit_method == "rate_cum"
    assert result.di_initial is not None
    assert 4.2 < result.di_initial < 11.5


def test_clean_water_decline_keeps_cum_fit() -> None:
    # A well-behaved water decline (no hard early spike): the cum fit
    # captures qi near the peak, so the qi-underfit trigger must NOT fire.
    rates = [800.0 * 0.82 ** i for i in range(24)]  # smooth ~18%/mo decline
    df = _water_frame(rates)
    peak = detect_peak(df, rate_column="rate_calday_bwpd")
    assert peak is not None
    result = fit_with_fallback(df, peak=peak, stream="water")
    assert result.fit_method == "rate_cum"
