"""Phase 1 of the LateralLine migration: header upsert path.

These tests pin the shape of the values dict the ingest layer hands to
PostGIS — specifically, that a header carrying a `lateral_line_wkt` lands
a `wellstick = ST_GeomFromText(<wkt>, 4326)` + `wellstick_source =
'heel_to_bh'` pair on its row, and that a header without one omits both
keys so an ON CONFLICT update can't clobber a previously-stored
survey-derived wellstick.

The actual ST_GeomFromText call only resolves at execute time, but its
identity, arguments, and presence in the dict are what we want to lock
down — those are what determine whether the survey-fetch phase remains
needed for the well or can be skipped.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.sql.elements import ColumnElement

from app.enverus_client.base import WellHeader
from app.ingest.wells import header_to_upsert_values


_NOW = datetime(2026, 5, 20, tzinfo=timezone.utc)


def _header(**overrides) -> WellHeader:
    base = {
        "api14": "42301000000000",
        "operator": "TEST OP",
        "formation": "Wolfcamp A",
        "lateral_ft": 10_000.0,
        "county": "Loving",
        "basin": "Permian",
        "status": "PRODUCING",
        "sh_lat": 31.9,
        "sh_lon": -103.7,
        "bh_lat": 31.92,
        "bh_lon": -103.69,
    }
    base.update(overrides)
    return WellHeader(**base)


def test_lateral_line_header_emits_wellstick_and_source() -> None:
    """When the upstream row carries LateralLine, the header upsert
    populates wellstick + wellstick_source directly. The downstream
    survey-fetch phase can then skip this well."""
    wkt = "LINESTRING (-103.69 31.901, -103.68 31.921)"
    values = header_to_upsert_values(_header(lateral_line_wkt=wkt), _NOW)

    assert "wellstick" in values, (
        "header with LateralLine must drop a wellstick expression into "
        "the upsert values dict — otherwise the survey-fetch phase has "
        "to do redundant work and the no-survey wells stay sticky-less"
    )
    assert values["wellstick_source"] == "heel_to_bh"

    # ST_GeomFromText is built via sqlalchemy.func — the resulting node
    # is a ColumnElement carrying the wkt + SRID as bound parameters.
    expr = values["wellstick"]
    assert isinstance(expr, ColumnElement)
    # Compile the expression and confirm the wkt + SRID flow through
    # as the function's first two args.
    args = list(expr.clauses)
    assert len(args) == 2
    assert args[0].value == wkt
    assert args[1].value == 4326


def test_empty_string_lateral_line_treated_as_missing() -> None:
    """Defensive: an empty / whitespace wkt shouldn't land in the values
    dict either. The Prism parser already filters these, but the ingest
    layer is the right place to draw the second guard. With Phase 2 the
    fallback then engages (sh + bh both present), so we expect the
    SURFACE_TO_BH branch to land here, not "wellstick unset"."""
    for falsy in ("", "   ", None):
        values = header_to_upsert_values(
            _header(lateral_line_wkt=falsy), _NOW
        )
        # Fallback fires because the stub header has sh + bh populated.
        assert values["wellstick_source"] == "surface_to_bh"
        assert "wellstick" in values


def test_surface_to_bh_fallback_engages_when_no_lateral_line() -> None:
    """Phase 2 of the LateralLine migration retired the survey-fetch
    pipeline. The straight-line surface→BH wellstick used to come out
    of survey-ingest's no-heel fallback; that responsibility now lives
    in header_to_upsert_values. Pin the behavior so the fallback can't
    silently regress to "no wellstick at all" for the ~1% of horizontals
    without LateralLine."""
    values = header_to_upsert_values(
        _header(lateral_line_wkt=None), _NOW
    )
    assert values["wellstick_source"] == "surface_to_bh"
    expr = values["wellstick"]
    # ST_MakeLine with two ST_SetSRID(ST_MakePoint(...), 4326) children.
    assert isinstance(expr, ColumnElement)
    args = list(expr.clauses)
    assert len(args) == 2  # sh point, bh point


def test_no_wellstick_when_lateral_line_and_endpoints_both_missing() -> None:
    """If neither LateralLine nor sh+bh are available, no wellstick is
    written. ON CONFLICT then preserves whatever's already on the row.
    A new well in this state stays at wellstick = NULL, source = NONE."""
    h = _header(lateral_line_wkt=None, sh_lat=None, sh_lon=None,
                bh_lat=None, bh_lon=None)
    values = header_to_upsert_values(h, _NOW)
    assert "wellstick" not in values
    assert "wellstick_source" not in values


# ---------------------- Prism parser ----------------------


def test_prism_parser_extracts_lateral_line() -> None:
    """Enverus delivers LateralLine as a WKT LINESTRING string on the
    wells row. The parser pulls it through unchanged for valid input."""
    from app.enverus_client.prism import _parse_well_header

    item = {
        "API_UWI_14_Unformatted": "42301000000000",
        "Latitude": 31.9,
        "Longitude": -103.7,
        "Latitude_BH": 31.92,
        "Longitude_BH": -103.69,
        "LateralLine": "LINESTRING (-103.69 31.901, -103.68 31.921)",
    }
    h = _parse_well_header(item)
    assert h.lateral_line_wkt == "LINESTRING (-103.69 31.901, -103.68 31.921)"


def test_prism_parser_rejects_non_linestring() -> None:
    """If Enverus emits something other than a LINESTRING here (an empty
    string, whitespace, a different geometry type, a WKB hex), drop it
    so it can't blow up PostGIS at execute time."""
    from app.enverus_client.prism import _parse_well_header

    base = {
        "API_UWI_14_Unformatted": "42301000000000",
        "Latitude": 31.9, "Longitude": -103.7,
        "Latitude_BH": 31.92, "Longitude_BH": -103.69,
    }
    for bad in ["", "   ", "POINT (-103 31)", 12345, None, []]:
        h = _parse_well_header({**base, "LateralLine": bad})
        assert h.lateral_line_wkt is None, f"expected None for {bad!r}"
