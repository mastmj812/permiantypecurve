"""Geologic risking — per-stream scalar multipliers on a saved type curve.

After geologic review a curve sometimes takes a haircut: a scalar MUL on
the oil / gas / water streams (e.g. 0.85). The stored ``series`` stays
the CLEAN technical fit; risking is applied at the read/export boundary
by :func:`apply_risking` so that

* re-aggregation can never lose or double-apply the multiplier, and
* the TweakPanel / fit-override round-trip edits the clean fit (a risked
  series in the editor would re-risk on the next read).

Arps is homogeneous in qi: scaling ``qi``/``qo`` scales the whole rate
vector and every EUR by the same factor, so one scalar applied to the
rate arrays, the fit's qi/qo, and the EUR scalars is exactly
self-consistent. Shape parameters (``b``, ``Di``, ``Df``,
``peak_index``) and fit quality (``r2``) are untouched — risking changes
magnitude, not decline character — and a uniform positive scalar
preserves the SPE percentile order (P10 >= P50 >= P90). EUR remains the
raw 50-yr technical integral, just scaled; no economic limit enters.

``observed_streams`` (the actuals QC overlay) is never risked — history
is history. Per-well analog stats are likewise out of scope here.
"""

from __future__ import annotations

import copy
import math
from typing import Any

STREAMS: tuple[str, ...] = ("oil", "gas", "water")

# Rate-array keys inside one stream dict (percentile series + mean).
_RATE_KEYS: tuple[str, ...] = ("p10", "p25", "p50", "p75", "p90", "mean")

# Fit-param keys that scale with the multiplier (all rate- or
# volume-dimensioned). b/Di/Df/peak_index/r2 stay put.
_FIT_SCALAR_KEYS: tuple[str, ...] = ("qi", "qo", "ramp_eur", "arps_eur", "eur_per_unit")


def normalize_multipliers(raw: dict[str, Any] | None) -> dict[str, float]:
    """``{stream: mul}`` with every stream present and validated.

    Raises ``ValueError`` on unknown stream keys or non-finite /
    non-positive values (a zero MUL would silently delete a stream —
    if a stream shouldn't ship, that's a curve/zone decision, not a
    risking value).
    """
    out = dict.fromkeys(STREAMS, 1.0)
    for key, value in (raw or {}).items():
        if key not in STREAMS:
            raise ValueError(f"unknown risking stream {key!r} (expected oil/gas/water)")
        mul = float(value)
        if not math.isfinite(mul) or mul <= 0.0:
            raise ValueError(
                f"risking multiplier for {key} must be a positive finite number, got {value!r}"
            )
        out[key] = mul
    return out


def is_risked(raw: dict[str, Any] | None) -> bool:
    """True when any stream's multiplier differs from 1.0."""
    return any(m != 1.0 for m in normalize_multipliers(raw).values())


def risked_name_suffix(raw: dict[str, Any] | None) -> str:
    """Curve-name suffix carrying the risking factor(s).

    Collapses to a single factor when all three streams share one
    multiplier; otherwise lists the non-1.0 streams (same text as the
    UI badge). Empty string when unrisked. MUST stay byte-for-byte
    with the frontend's ``risking.ts::riskedNameSuffix`` — the slide
    preview renders the same string client-side.
    """
    if not is_risked(raw):
        return ""
    muls = normalize_multipliers(raw)
    if len(set(muls.values())) == 1:
        return f" [RISKED ×{muls['oil']:.2f}]"  # noqa: RUF001
    label = " · ".join(f"×{muls[s]:.2f} {s}" for s in STREAMS if muls[s] != 1.0)  # noqa: RUF001
    return f" [RISKED {label}]"


def _scale_rates(values: list[Any], mul: float) -> list[Any]:
    return [v * mul if isinstance(v, (int, float)) else v for v in values]


def _scale_fit(fit: dict[str, Any], mul: float) -> None:
    for key in _FIT_SCALAR_KEYS:
        v = fit.get(key)
        if isinstance(v, (int, float)):
            fit[key] = v * mul
    smoothed = fit.get("smoothed_rate")
    if isinstance(smoothed, list):
        fit["smoothed_rate"] = _scale_rates(smoothed, mul)


def apply_risking(series: dict[str, Any], raw_muls: dict[str, Any] | None) -> dict[str, Any]:
    """Risked copy of a saved ``series`` dict; the input is never mutated.

    Identity fast-path: when every multiplier is 1.0 the SAME object is
    returned (callers may rely on this to skip re-serialization).

    Scales, per stream: the percentile/mean rate arrays,
    ``implied_eur_per_1000ft``, ``fitted`` (qi/qo/ramp_eur/arps_eur/
    eur_per_unit/smoothed_rate), every ``fitted_per_percentile`` slot,
    and ``fitted_eur_per_unit``. ``observed_streams`` and ``well_count``
    are untouched.
    """
    muls = normalize_multipliers(raw_muls)
    if all(m == 1.0 for m in muls.values()):
        return series

    risked = copy.deepcopy(series)
    streams = risked.get("streams") or {}
    for stream, mul in muls.items():
        if mul == 1.0:
            continue
        block = streams.get(stream)
        if not isinstance(block, dict):
            continue
        for key in _RATE_KEYS:
            vals = block.get(key)
            if isinstance(vals, list):
                block[key] = _scale_rates(vals, mul)
        implied = block.get("implied_eur_per_1000ft")
        if isinstance(implied, dict):
            block["implied_eur_per_1000ft"] = {
                k: (v * mul if isinstance(v, (int, float)) else v) for k, v in implied.items()
            }
        fitted = block.get("fitted")
        if isinstance(fitted, dict):
            _scale_fit(fitted, mul)
        per_pct = block.get("fitted_per_percentile")
        if isinstance(per_pct, dict):
            for fit in per_pct.values():
                if isinstance(fit, dict):
                    _scale_fit(fit, mul)
        eur_map = block.get("fitted_eur_per_unit")
        if isinstance(eur_map, dict):
            block["fitted_eur_per_unit"] = {
                k: (v * mul if isinstance(v, (int, float)) else v) for k, v in eur_map.items()
            }
    return risked
