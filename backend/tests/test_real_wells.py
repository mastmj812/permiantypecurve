"""Real-well regression tests — oil, gas, and water.

For each ``real_well_N.csv`` in tests/fixtures/real_wells/ we fit every stream
on its OWN detected peak (domain rule: gas commonly peaks after oil, water in
flowback — anchoring on the oil peak reads the wrong qi/limb):

  * **oil** pins ``fit_rate_cum`` — its historical, reviewed baseline.
  * **gas / water** pin ``fit_with_fallback`` — the production wrapper the
    orchestrator actually runs (rate-cum with a rate-time retry; water also
    has the qi-underfit rescue). This is what ships, so it's what we regress.

The fitted params snapshot to ``_baselines.json`` (nested
``{api14: {stream: {qi, Di, b, eur, fit_r2}}}``). Baselines PIN CURRENT
BEHAVIOR — they are the tripwire for silent numeric drift.

Re-seeding is EXPLICIT. In CI a missing baseline is a hard failure, never a
silent seed-and-skip: that's the whole point — you cannot get a green run by
deleting the file. To (re)seed after a deliberate math change, run

    RESEED_GOLDENS=1 pytest tests/test_real_wells.py

review the diff in ``_baselines.json``, and commit it.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from app.forecasting.fit import fit_rate_cum, fit_with_fallback
from app.forecasting.peak_detection import detect_peak
from app.forecasting.types import ForecastResult
from tests.real_well_loader import RealWellFixture, load_real_well_csv

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_wells"
BASELINES_PATH = FIXTURES_DIR / "_baselines.json"

# Tolerance for snapshot-baseline comparisons.
BASELINE_TOLERANCE_PCT: float = 0.05  # 5%
# Below-this R² indicates either a fitter bug or an unusually messy well.
MIN_FIT_R2: float = 0.70

# Re-seeding is opt-in only. Without this env flag a missing baseline FAILS
# (CI can't silently regenerate goldens and pass).
RESEED: bool = os.getenv("RESEED_GOLDENS") == "1"

STREAMS: tuple[str, ...] = ("oil", "gas", "water")
_RATE_COL: dict[str, str] = {
    "oil": "rate_calday_bopd",
    "gas": "rate_calday_mcfd",
    "water": "rate_calday_bwpd",
}


def _all_well_csvs() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("real_well_*.csv"))


def _load_baselines() -> dict[str, dict[str, dict[str, float]]]:
    if not BASELINES_PATH.exists():
        return {}
    return json.loads(BASELINES_PATH.read_text())


def _save_baselines(d: dict[str, dict[str, dict[str, float]]]) -> None:
    BASELINES_PATH.write_text(json.dumps(d, indent=2, sort_keys=True))


def _fit_stream(fixture: RealWellFixture, stream: str) -> ForecastResult | None:
    """Fit one stream on its own peak, or None when the stream has no real
    production (no detectable peak / zero-rate peak) — mirrors the
    orchestrator, which SKIPS such a stream rather than fitting zeros."""
    peak = detect_peak(fixture.monthly, rate_column=_RATE_COL[stream])
    if peak is None or peak.peak_rate <= 0:
        return None
    if stream == "oil":
        # Oil keeps its historical fit_rate_cum baseline (already reviewed);
        # don't disturb the pinned values.
        return fit_rate_cum(
            fixture.monthly, model_type="modified_hyperbolic", peak=peak, stream="oil"
        )
    # Gas / water go through the production wrapper.
    return fit_with_fallback(
        fixture.monthly, model_type="modified_hyperbolic", peak=peak, stream=stream
    )


def _snapshot(r: ForecastResult) -> dict[str, float]:
    return {
        "qi": r.qi,
        "Di": r.params["Di"],
        "b": r.params["b"],
        "eur": r.eur,
        "fit_r2": r.fit_r2,
    }


@pytest.mark.parametrize("csv_path", _all_well_csvs(), ids=lambda p: p.stem)
def test_real_well_oil_fit_is_physically_reasonable(csv_path: Path) -> None:
    fixture = load_real_well_csv(csv_path)
    r = _fit_stream(fixture, "oil")
    assert r is not None, f"no oil peak found for {fixture.api14}"

    # Physical reasonableness — catches a catastrophic regression even on a
    # fresh checkout with no baselines yet.
    assert r.qi > 0, f"qi must be positive (got {r.qi})"
    assert 0.0 < r.params["Di"] < 5.0, f"Di out of bounds (got {r.params['Di']})"
    assert 0.0 <= r.params["b"] <= 2.0, f"b out of bounds (got {r.params['b']})"
    assert r.params["Df"] == pytest.approx(0.08), "Df should be the 8%/yr default"
    assert r.eur > 0 and math.isfinite(r.eur), f"EUR not finite/positive: {r.eur}"
    assert r.fit_r2 > MIN_FIT_R2, f"fit_r2={r.fit_r2:.3f} for {fixture.api14}"


@pytest.mark.parametrize("stream", STREAMS)
@pytest.mark.parametrize("csv_path", _all_well_csvs(), ids=lambda p: p.stem)
def test_real_well_fit_matches_baseline(csv_path: Path, stream: str) -> None:
    """Snapshot regression per (well, stream): fitted params must stay within
    tolerance of the committed baseline. A missing baseline FAILS unless
    RESEED_GOLDENS=1 is set (explicit re-seed only)."""
    fixture = load_real_well_csv(csv_path)
    result = _fit_stream(fixture, stream)
    if result is None:
        pytest.skip(f"{stream}: no real peak for {fixture.api14} — no forecast, no golden")

    current = _snapshot(result)
    baselines = _load_baselines()
    well = baselines.get(fixture.api14, {})

    if stream not in well:
        if RESEED:
            well[stream] = current
            baselines[fixture.api14] = well
            _save_baselines(baselines)
            pytest.skip(
                f"Seeded baseline for {fixture.api14}/{stream}; re-run without "
                f"RESEED_GOLDENS to verify drift detection."
            )
        pytest.fail(
            f"No committed baseline for {fixture.api14}/{stream}. Baselines are "
            f"NOT auto-seeded in CI. Run `RESEED_GOLDENS=1 pytest "
            f"tests/test_real_wells.py`, review the values, and commit them."
        )

    expected = well[stream]
    for field in ("qi", "Di", "eur"):
        e, a = expected[field], current[field]
        if e == 0:
            assert a == pytest.approx(0, abs=1e-9), f"{fixture.api14}/{stream}.{field}"
        else:
            assert abs(a - e) / abs(e) < BASELINE_TOLERANCE_PCT, (
                f"{fixture.api14}/{stream}.{field} drifted: baseline={e:.4g}, "
                f"current={a:.4g} (tolerance ±{BASELINE_TOLERANCE_PCT * 100:.0f}%)"
            )
    # b can sit near a bound; use absolute tolerance.
    assert abs(current["b"] - expected["b"]) < 0.15, (
        f"{fixture.api14}/{stream}.b drifted: baseline={expected['b']:.3f}, "
        f"current={current['b']:.3f}"
    )
    # R² shouldn't degrade.
    assert current["fit_r2"] >= expected["fit_r2"] - 0.05, (
        f"{fixture.api14}/{stream}.fit_r2 degraded: baseline={expected['fit_r2']:.3f}, "
        f"current={current['fit_r2']:.3f}"
    )
