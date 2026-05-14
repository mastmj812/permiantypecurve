"""Real-well regression tests.

For each `real_well_N.csv` in tests/fixtures/real_wells/:
  1. Load and run through `detect_oil_peak` + `fit_rate_cum` for oil.
  2. Physical-reasonableness assertions on the fit (qi>0, decline bounded,
     EUR positive, fit_r2 above a sane floor).
  3. Snapshot the fitted params to tests/fixtures/real_wells/_baselines.json
     so future code changes that drift the fit get flagged.

The baselines file is auto-created on first run. Subsequent runs assert
against it within `BASELINE_TOLERANCE_PCT`. To intentionally re-baseline
after a deliberate math change, delete the file and re-run.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.forecasting.fit import fit_rate_cum
from app.forecasting.peak_detection import detect_oil_peak
from tests.real_well_loader import load_real_well_csv

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_wells"
BASELINES_PATH = FIXTURES_DIR / "_baselines.json"

# Tolerance for snapshot-baseline comparisons.
BASELINE_TOLERANCE_PCT: float = 0.05  # 5%
# Below-this R² indicates either a fitter bug or an unusually messy well.
MIN_FIT_R2: float = 0.70


def _all_well_csvs() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("real_well_*.csv"))


def _load_baselines() -> dict[str, dict[str, float]]:
    if not BASELINES_PATH.exists():
        return {}
    return json.loads(BASELINES_PATH.read_text())


def _save_baselines(d: dict[str, dict[str, float]]) -> None:
    BASELINES_PATH.write_text(json.dumps(d, indent=2, sort_keys=True))


@pytest.mark.parametrize("csv_path", _all_well_csvs(), ids=lambda p: p.stem)
def test_real_well_oil_fit_is_physically_reasonable(csv_path: Path) -> None:
    fixture = load_real_well_csv(csv_path)
    peak = detect_oil_peak(fixture.monthly)
    assert peak is not None, f"no oil peak found for {fixture.api14}"

    r = fit_rate_cum(
        fixture.monthly, model_type="modified_hyperbolic", peak=peak, stream="oil"
    )

    # Physical reasonableness — these would catch a catastrophic regression
    # even on a fresh checkout with no baselines yet.
    assert r.qi > 0, f"qi must be positive (got {r.qi})"
    assert 0.0 < r.params["Di"] < 5.0, f"Di out of bounds (got {r.params['Di']})"
    assert 0.0 <= r.params["b"] <= 2.0, f"b out of bounds (got {r.params['b']})"
    assert r.params["Df"] == pytest.approx(0.08), "Df should be the 8%/yr default"
    assert r.eur > 0 and math.isfinite(r.eur), f"EUR not finite/positive: {r.eur}"
    assert r.fit_r2 > MIN_FIT_R2, f"fit_r2={r.fit_r2:.3f} for {fixture.api14}"


@pytest.mark.parametrize("csv_path", _all_well_csvs(), ids=lambda p: p.stem)
def test_real_well_oil_fit_matches_baseline(csv_path: Path) -> None:
    """Snapshot regression: fitted params must stay within tolerance of the
    committed baseline. On first run (no baseline file), this test writes
    the current values and passes; subsequent runs compare."""
    fixture = load_real_well_csv(csv_path)
    peak = detect_oil_peak(fixture.monthly)
    assert peak is not None
    r = fit_rate_cum(
        fixture.monthly, model_type="modified_hyperbolic", peak=peak, stream="oil"
    )

    current = {
        "qi": r.qi,
        "Di": r.params["Di"],
        "b": r.params["b"],
        "eur": r.eur,
        "fit_r2": r.fit_r2,
    }

    baselines = _load_baselines()
    key = fixture.api14
    if key not in baselines:
        # First run for this well — seed and pass. The first commit of the
        # baselines file is the user's responsibility (review the values
        # before locking them in).
        baselines[key] = current
        _save_baselines(baselines)
        pytest.skip(f"Seeded baseline for {key}; re-run to verify drift detection.")

    expected = baselines[key]
    for field in ("qi", "Di", "eur"):
        e, a = expected[field], current[field]
        if e == 0:
            assert a == pytest.approx(0, abs=1e-9), f"{key}.{field}"
        else:
            assert abs(a - e) / abs(e) < BASELINE_TOLERANCE_PCT, (
                f"{key}.{field} drifted: baseline={e:.4g}, current={a:.4g} "
                f"(tolerance ±{BASELINE_TOLERANCE_PCT * 100:.0f}%)"
            )
    # b can be near 0 or near 2 (bounded); use absolute tolerance.
    assert abs(current["b"] - expected["b"]) < 0.15, (
        f"{key}.b drifted: baseline={expected['b']:.3f}, current={current['b']:.3f}"
    )
    # R² shouldn't degrade.
    assert current["fit_r2"] >= expected["fit_r2"] - 0.05, (
        f"{key}.fit_r2 degraded: baseline={expected['fit_r2']:.3f}, "
        f"current={current['fit_r2']:.3f}"
    )
