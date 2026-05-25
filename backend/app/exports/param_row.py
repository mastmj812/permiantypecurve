"""Python port of the frontend's SlideParamTable formatters.

Goal: PPTX cell strings for the type-curve param table match the
iframe preview's `<td>` text byte-for-byte. The frontend logic lives
at ``frontend/src/components/slide/SlideParamTable.tsx`` — when that
changes, port the change here too.

Two conventions baked in:
  * per-10kft scaling on rates and EURs (`PER_10K_FACTOR = 10`)
    because the saved curve's ``eur_per_unit`` is per-1k-ft and the
    deck displays per-10k-ft.
  * Di rendered as 1-yr effective decline (``effective_decline``)
    rather than the Arps nominal Di. Operators read decline in
    effective terms.
"""

from __future__ import annotations

import math

from app.db.models import TypeCurve


# Per-10k-ft scaling for rate / EUR cells. Mo / B / Di are unit-
# independent and pass through unscaled.
PER_10K_FACTOR: float = 10.0

# 17 columns. Order matches the user's template's slide-1 param table
# and ``frontend/src/components/slide/SlideParamTable.tsx::HEADERS``.
PARAM_HEADERS: tuple[str, ...] = (
    "Type Curve Name",
    "Oil Initial", "Oil Mo", "Oil Peak", "Oil B", "Oil Di", "Oil EUR",
    "Gas Initial", "Gas Mo", "Gas Peak", "Gas B", "Gas Di", "Gas EUR",
    "Water Peak", "Water B", "Water Di", "Water EUR",
)


def effective_decline(Di: float | None, b: float | None) -> float:
    """First-year effective decline as a 0–1 fraction.

    Mirrors ``frontend/src/api/forecasts.ts::effectiveDecline``. Returns
    0 for invalid Di so callers can render an em-dash via the > 0 check.
    """
    if Di is None or not math.isfinite(Di) or Di <= 0:
        return 0.0
    if b is None or abs(b) < 1e-6:
        return 1 - math.exp(-Di)
    if abs(b - 1) < 1e-4:
        return Di / (1 + Di)
    return 1 - 1 / math.pow(1 + b * Di, 1 / b)


def _num(v: object) -> float | None:
    if v is None:
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _scale(v: object, factor: float) -> float | None:
    n = _num(v)
    return None if n is None else n * factor


def _fmt_rate(v: object) -> str:
    n = _num(v)
    return "—" if n is None else f"{round(n):,}"


def _fmt_mo(v: object) -> str:
    n = _num(v)
    return "—" if n is None else f"{n:.1f}"


def _fmt_b(v: object) -> str:
    n = _num(v)
    return "—" if n is None else f"{n:.2f}"


def _fmt_di_effective(Di: object, b: object) -> str:
    dn = _num(Di)
    bn = _num(b)
    if dn is None or dn <= 0:
        return "—"
    eff = effective_decline(dn, bn)
    return f"{eff:.2f}" if eff > 0 else "—"


def _fmt_eur(v: object) -> str:
    n = _num(v)
    return "—" if n is None else f"{round(n):,}"


def _get_fitted(curve: TypeCurve, stream: str) -> dict[str, object]:
    streams = (curve.series or {}).get("streams") or {}
    fitted = (streams.get(stream) or {}).get("fitted")
    return fitted or {}


def format_param_row(curve: TypeCurve) -> tuple[str, ...]:
    """17-cell row matching ``PARAM_HEADERS``.

    Reads the published P50 fit per stream from
    ``curve.series.streams.<stream>.fitted``. Scales rates / EUR by
    ``PER_10K_FACTOR`` and converts Di to 1-yr effective.
    """
    oil = _get_fitted(curve, "oil")
    gas = _get_fitted(curve, "gas")
    wat = _get_fitted(curve, "water")
    F = PER_10K_FACTOR
    return (
        curve.name,
        _fmt_rate(_scale(oil.get("qo"), F)),
        _fmt_mo(oil.get("peak_index")),
        _fmt_rate(_scale(oil.get("qi"), F)),
        _fmt_b(oil.get("b")),
        _fmt_di_effective(oil.get("Di"), oil.get("b")),
        _fmt_eur(_scale(oil.get("eur_per_unit"), F)),
        _fmt_rate(_scale(gas.get("qo"), F)),
        _fmt_mo(gas.get("peak_index")),
        _fmt_rate(_scale(gas.get("qi"), F)),
        _fmt_b(gas.get("b")),
        _fmt_di_effective(gas.get("Di"), gas.get("b")),
        _fmt_eur(_scale(gas.get("eur_per_unit"), F)),
        _fmt_rate(_scale(wat.get("qi"), F)),
        _fmt_b(wat.get("b")),
        _fmt_di_effective(wat.get("Di"), wat.get("b")),
        _fmt_eur(_scale(wat.get("eur_per_unit"), F)),
    )
