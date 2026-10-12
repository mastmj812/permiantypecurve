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
