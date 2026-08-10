"""Type-well build-up sheet + CSV — the partner-facing deliverable.

Pure writers over ``compute_buildup`` (no DB, no HTTP): everything
comes from the TypeCurve row itself, so the deal workbook stays
callable with ``session=None`` and the per-curve zip needs no extra
queries. Layout follows the house metadata-sheet conventions (bold
two-column header block, blank-row section separators).

Sheet sections:
  1. header block — curve identity, formations, AOI, universe as-of,
     stated cull criteria (or the degraded/no-AOI note)
  2. waterfall — stage | description | wells culled | wells remaining,
     closed by "+ added outside AOI" and the reconciled final count
  3. per-well roster grouped by disposition (included first, culls in
     stage order), with reason code + note on the coded stages
"""

from __future__ import annotations

import csv
import io
from typing import Any

from app.db.models import TypeCurve
from app.type_curves.buildup import (
    STAGE_DESCRIPTIONS,
    Buildup,
    BuildupRow,
    compute_buildup,
    reason_label,
)
from app.wells_api.filters import SPACING_SENTINEL_FT

ROSTER_HEADERS = [
    "api10",
    "well_name",
    "operator",
    "first_prod_date",
    "lateral_ft",
    "spacing_ft",
    "formation",
    "disposition",
    "reason_code",
    "reason",
    "note",
]

# Roster group order: the survivors first, then culls in stage order.
_DISPOSITION_ORDER = ["included"] + [k for k, _ in STAGE_DESCRIPTIONS]


def _sorted_roster(b: Buildup) -> list[BuildupRow]:
    rank = {k: i for i, k in enumerate(_DISPOSITION_ORDER)}
    return sorted(b.rows, key=lambda r: (rank.get(r.disposition, 99), r.api10))


def _roster_values(r: BuildupRow) -> list[Any]:
    disposition = r.annotation if r.annotation else r.disposition
    # Sentinel disclosure: 2800.0 is Novi's no-neighbor cap, not a
    # measured distance — print the class, not the misleading number.
    spacing: Any = r.lateral_closer_xy_ft
    if spacing == SPACING_SENTINEL_FT:
        spacing = "no neighbor (2800 cap)"
    return [
        r.api10,
        r.name,
        r.operator,
        r.first_prod_date,
        r.lateral_ft,
        spacing,
        r.formation,
        disposition,
        r.reason_code,
        reason_label(r.reason_code),
        r.note,
    ]


def _header_block(tc: TypeCurve, b: Buildup) -> list[tuple[str, Any]]:
    lines: list[tuple[str, Any]] = [
        ("curve_name", tc.name),
        ("curve_id", str(tc.id)),
        ("created_at", tc.created_at.isoformat() if tc.created_at else ""),
    ]
    if b.header is not None:
        h = b.header
        lines.append(("formations", ", ".join(h.formations) or "(none)"))
        aoi = f"{h.aoi_polygon_count}"
        if h.aoi_total_area_sq_mi is not None:
            aoi += f" (total {h.aoi_total_area_sq_mi} sq mi)"
        lines.append(("aoi_polygons", aoi))
        if h.universe_computed_at:
            lines.append(
                (
                    "universe_computed_at",
                    f"{h.universe_computed_at} "
                    "(as-of snapshot; later warehouse syncs not reflected)",
                )
            )
        lines.append(
            (
                "starting_universe",
                f"{h.universe_count} wells (formation + AOI only)",
            )
        )
        lines.extend(h.criteria)
        if h.cutoff_months is not None:
            lines.append(
                (
                    "forecast_cutoff",
                    f"{h.cutoff_months} months post-peak minimum history",
                )
            )
    for note in b.notes:
        lines.append(("note", note))
    return lines


def _waterfall_rows(b: Buildup) -> list[list[Any]]:
    rows: list[list[Any]] = [
        [
            "universe",
            "Formation wells inside AOI",
            None,
            b.header.universe_count if b.header else 0,
        ],
    ]
    for w in b.waterfall:
        rows.append([w.stage, w.description, w.culled, w.remaining])
    rows.append(
        [
            "added_outside_aoi",
            "Add-wells / pasted list (outside universe)",
            None,
            f"+{len(b.out_of_universe_included)}",
        ]
    )
    rows.append(["final_cohort", "Final type-curve cohort", None, b.included_count])
    return rows


def write_buildup_sheet(ws: Any, tc: TypeCurve) -> None:
    """Render the build-up sheet onto an openpyxl worksheet."""
    from openpyxl.styles import Font

    bold = Font(bold=True)
    b = compute_buildup(tc)

    def append_bold(values: list[Any]) -> None:
        ws.append(values)
        for col_idx in range(1, len(values) + 1):
            ws.cell(row=ws.max_row, column=col_idx).font = bold

    # -- section 1: header block ------------------------------------
    append_bold(["field", "value"])
    for key, value in _header_block(tc, b):
        ws.append([key, value])
    ws.append([])

    # -- section 2: waterfall ----------------------------------------
    if b.waterfall:
        append_bold(["stage", "description", "wells_culled", "wells_remaining"])
        for row in _waterfall_rows(b):
            ws.append(row)
        ws.append([])

    # -- section 3: roster -------------------------------------------
    append_bold(ROSTER_HEADERS)
    for r in _sorted_roster(b):
        ws.append(_roster_values(r))
    if b.out_of_universe_included:
        for api10 in b.out_of_universe_included:
            ws.append(
                [
                    api10,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "added outside AOI/universe",
                    None,
                    None,
                    None,
                ]
            )

    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 42
    for letter in ("C", "D", "E", "F", "G"):
        ws.column_dimensions[letter].width = 16
    for letter in ("H", "I", "J"):
        ws.column_dimensions[letter].width = 24


def buildup_csv(tc: TypeCurve) -> str:
    """CSV twin of the sheet for the per-curve zip: `#`-prefixed header
    and waterfall comment blocks, then the roster table."""
    b = compute_buildup(tc)
    buf = io.StringIO()
    w = csv.writer(buf)

    for key, value in _header_block(tc, b):
        w.writerow([f"# {key}", value])
    if b.waterfall:
        w.writerow(["# stage", "description", "wells_culled", "wells_remaining"])
        for row in _waterfall_rows(b):
            w.writerow([f"# {row[0]}", *row[1:]])

    w.writerow(ROSTER_HEADERS)
    for r in _sorted_roster(b):
        w.writerow(_roster_values(r))
    for api10 in b.out_of_universe_included:
        w.writerow(
            [
                api10,
                None,
                None,
                None,
                None,
                None,
                None,
                "added outside AOI/universe",
                None,
                None,
                None,
            ]
        )
    return buf.getvalue()
