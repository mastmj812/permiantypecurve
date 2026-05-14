"""Heel-point detection.

Pure function over a survey: scan stations in MD-ascending order and return
the first station that crosses inclination >= 80° as the well's heel. If no
such station exists (or no survey at all), report the appropriate fallback
so the caller can render a surface-to-bottomhole wellstick or a point.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.db.models import WellstickSource

DEFAULT_HEEL_INCLINATION_DEG: float = 80.0


@dataclass(frozen=True)
class SurveyStation:
    """Minimal station shape — keep this decoupled from the ORM so the
    function is trivially callable from tests and adapters."""

    station_seq: int
    md_ft: float
    inclination_deg: float
    lat: float | None
    lon: float | None


@dataclass(frozen=True)
class HeelResult:
    heel_lat: float | None
    heel_lon: float | None
    source: WellstickSource


def compute_heel(
    stations: Iterable[SurveyStation],
    *,
    has_bottomhole: bool,
    has_surface: bool,
    inclination_threshold_deg: float = DEFAULT_HEEL_INCLINATION_DEG,
) -> HeelResult:
    """Determine the heel point (if any) and the wellstick source to use.

    Rules (from the build brief):
      1. Sort stations by MD ascending.
      2. First station with inclination_deg >= 80° → heel, source = heel_to_bh.
      3. If no station crosses 80° (or no survey at all) but we have both SH
         and BH → source = surface_to_bh, heel coords NULL.
      4. Otherwise → source = none, heel coords NULL (rendered as a point).

    A station whose lat/lon is missing is skipped, not treated as the heel —
    we'd have nothing to put in the LINESTRING.
    """
    ordered = sorted(stations, key=lambda s: s.md_ft)
    for s in ordered:
        if s.inclination_deg >= inclination_threshold_deg:
            if s.lat is None or s.lon is None:
                # Malformed station — keep scanning; the next high-inclination
                # station might have coords. This is a real-world thing: some
                # Enverus surveys have inc/azi but no lat/lon on intermediate
                # stations.
                continue
            return HeelResult(
                heel_lat=s.lat, heel_lon=s.lon, source=WellstickSource.HEEL_TO_BH
            )

    if has_surface and has_bottomhole:
        return HeelResult(
            heel_lat=None, heel_lon=None, source=WellstickSource.SURFACE_TO_BH
        )
    return HeelResult(heel_lat=None, heel_lon=None, source=WellstickSource.NONE)
