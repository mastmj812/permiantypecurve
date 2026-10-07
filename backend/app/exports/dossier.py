"""Deal dossier PPTX — a working discussion deck, not a final exhibit.

Structure: a zone summary slide first (which planned sticks take which
type curve, the cohort behind it, TC vs Novi — built server-side from
``dossier_summary``), the curve-assignment overview map, one curve
support slide per zone (sticks + the wells that build its curve, wells
coloured by anduin oil EUR/ft | zoom on the sticks), then one slide per
narvi scenario (plan-view map left, gunbarrel
right, well-count subtitle), then the deal's type curves rendered
exactly like the existing per-curve slide export (param table + rate /
cum charts + cohort map, one slide per stream). The wells-table slide
and probit are deliberately omitted — this deck is for talking through
a deal while work is ongoing.

All raster panels arrive from the client (the dossier preview page
captures its SVG charts and MapLibre canvases), mirroring the
type-curve slide export: the backend only assembles the deck from the
brand template, so charts and maps stay pixel-identical to the app.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.shapes.graphfrm import GraphicFrame
from pptx.shapes.picture import Picture
from pptx.slide import Slide
from pptx.util import Inches, Pt
from sqlalchemy.orm import Session

from app.db.models import TypeCurve
from app.exports.blueox import RATIO_REFUSAL_NOTE, ratio_mode_streams
from app.exports.dossier_summary import (
    COHORT_HEADERS,
    COHORT_NOTE,
    SUMMARY_HEADERS,
    SUMMARY_NOTE,
    ZoneSummary,
    summary_cells,
)
from app.exports.pptx_builder import (
    _STREAM_TITLE,
    TEMPLATE_PATH,
    _duplicate_slide,
    _fill_param_table,
    _find_table,
    _find_title_shape,
    _place_chart_images,
    _set_title_text,
)

# Scenario-slide geometry (16:9, 13.333" x 7.5"): two side-by-side
# panels under the title, mirroring the chart column's 0.64" margins.
_MARGIN_IN = 0.64
_SLIDE_WIDTH_IN = 13.333
_PANEL_TOP_IN = 1.75
_PANEL_HEIGHT_IN = 4.9
_PANEL_GAP_IN = 0.15
_PANEL_WIDTH_IN = (_SLIDE_WIDTH_IN - 2 * _MARGIN_IN - _PANEL_GAP_IN) / 2
_SUBTITLE_TOP_IN = 1.32
_SUBTITLE_FONT_PT = 11

# Matching pixel dimensions for the client's capture panels (96 px/in)
# so the PNG aspect equals the picture box and nothing stretches.
SCENARIO_PANEL_PX = (
    round(_PANEL_WIDTH_IN * 96),
    round(_PANEL_HEIGHT_IN * 96),
)

# TC-vs-Novi comparison slide: one full-width figure (the 6-panel
# rate/cum grid) under the title + subtitle.
_COMPARISON_HEIGHT_IN = 5.5
_COMPARISON_WIDTH_IN = _SLIDE_WIDTH_IN - 2 * _MARGIN_IN
COMPARISON_PANEL_PX = (
    round(_COMPARISON_WIDTH_IN * 96),
    round(_COMPARISON_HEIGHT_IN * 96),
)
# Placed size: the capture above at its own aspect, bottom kept above the
# template's footer band (top 1.75" + 5.05" = 6.8").
_COMPARISON_PLACED_HEIGHT_IN = 5.05
# Title sizes for long titles: the template's title box fits ~38
# characters on one line; longer titles wrapped into the subtitle line.
_TITLE_STEPS = ((38, None), (50, 30), (64, 24))
_TITLE_MIN_PT = 20
# Subtitles longer than one 11pt line wrap at 9pt (two lines fit above
# the panels).
_SUBTITLE_ONE_LINE_CHARS = 165


@dataclass(frozen=True)
class ScenarioSlideInput:
    """One narvi scenario's slide: client-captured map + gunbarrel."""

    title: str
    subtitle: str  # bench/category well counts, built client-side
    map_png: bytes
    gunbarrel_png: bytes


@dataclass(frozen=True)
class ComparisonSlideInput:
    """One zone's TC-vs-Novi comparison slide: the client-captured
    6-panel figure (oil/gas/water x rate/cum, TC band vs Novi median)."""

    title: str
    subtitle: str  # alignment/normalization/risking basis, built client-side
    figure_png: bytes


@dataclass(frozen=True)
class CurveSlideInput:
    """One type curve's stream slides (same panels as the slide export)."""

    type_curve_id: UUID
    # stream -> (rate_png, cum_png); must carry oil, gas, water.
    stream_pngs: dict[str, tuple[bytes, bytes]] = field(default_factory=dict)
    map_png: bytes = b""


@dataclass(frozen=True)
class CohortTableInput:
    """One curve's well table (``COHORT_HEADERS`` cells, server-built)."""

    curve_name: str
    rows: list[list[str]]


def build_deal_dossier_pptx(
    session: Session,
    scenarios: list[ScenarioSlideInput],
    curves: list[CurveSlideInput],
    comparisons: list[ComparisonSlideInput] | None = None,
    summary: list[ZoneSummary] | None = None,
    overview: ComparisonSlideInput | None = None,
    supports: list[ScenarioSlideInput] | None = None,
    cohort_tables: dict[UUID, CohortTableInput] | None = None,
) -> bytes:
    """Assemble the dossier deck from the brand template.

    Slide order: the zone summary (when given; paginated), the
    curve-assignment overview, the zone support slides, then
    scenarios (one each), then per curve its well table (when given)
    and the oil / gas /
    water stream slides, then one TC-vs-Novi comparison slide per zone
    that has one. Raises ValueError on an unknown type curve or a
    missing stream panel.
    """
    if not scenarios and not curves and not summary and not supports:
        raise ValueError("dossier needs at least one scenario or curve")

    pres = Presentation(str(TEMPLATE_PATH))
    if len(pres.slides) < 2:
        raise ValueError("template must have at least 2 slides (stream slide + well table)")

    # Template is [stream_template, wells]. Every dossier slide starts
    # as a duplicate of the stream template (appended at the end); the
    # two template slides are deleted once all content slides exist.
    zones = summary or []
    for page in range(0, len(zones), SUMMARY_ROWS_PER_SLIDE):
        _duplicate_slide(pres, source_idx=0)
        n_pages = -(-len(zones) // SUMMARY_ROWS_PER_SLIDE)
        _build_summary_slide(
            pres.slides[-1],
            zones[page : page + SUMMARY_ROWS_PER_SLIDE],
            "Zone summary"
            + (f" ({page // SUMMARY_ROWS_PER_SLIDE + 1}/{n_pages})" if n_pages > 1 else ""),
        )

    if overview is not None:
        # Full-width single figure: same layout as a comparison slide.
        _duplicate_slide(pres, source_idx=0)
        _build_comparison_slide(pres.slides[-1], overview)
    # Support slides: support map left, stick zoom right — the scenario
    # slide's two-panel layout.
    for sc in [*(supports or []), *scenarios]:
        _duplicate_slide(pres, source_idx=0)
        _build_scenario_slide(pres.slides[-1], sc)

    streams = ["oil", "gas", "water"]
    for cv in curves:
        tc = session.get(TypeCurve, cv.type_curve_id)
        if tc is None:
            raise ValueError(f"type curve {cv.type_curve_id} not found")
        # The dossier's param table declares Arps params per stream — a
        # ratio-mode stream has none. HARD refusal, same posture as the
        # Blue Ox drop builder (in-app use stays unrestricted).
        ratio_streams = ratio_mode_streams(tc.series)
        if ratio_streams:
            raise ValueError(
                f"curve {tc.name}: stream(s) {', '.join(ratio_streams)} are "
                f"ratio-mode — {RATIO_REFUSAL_NOTE}"
            )
        ct = (cohort_tables or {}).get(cv.type_curve_id)
        if ct is not None:
            _build_cohort_slides(pres, ct)
        for stream in streams:
            if stream not in cv.stream_pngs:
                raise ValueError(f"curve {tc.name}: missing {stream} panels")
            _duplicate_slide(pres, source_idx=0)
            slide = pres.slides[-1]
            rate_png, cum_png = cv.stream_pngs[stream]
            _set_title_text(slide, f"{tc.name} {_STREAM_TITLE[stream]}")
            _fill_param_table(_find_table(slide), tc, None)
            _place_chart_images(slide, rate_png, cum_png, cv.map_png)

    for cp in comparisons or []:
        _duplicate_slide(pres, source_idx=0)
        _build_comparison_slide(pres.slides[-1], cp)

    # Drop the template slides (indices 0 and 1) now that the content
    # slides are in place — delete back-to-front so indices hold.
    _delete_slide(pres, 1)
    _delete_slide(pres, 0)

    buf = io.BytesIO()
    pres.save(buf)
    return buf.getvalue()


def _build_scenario_slide(slide: Slide, sc: ScenarioSlideInput) -> None:
    """Turn a duplicated stream-template slide into a scenario slide:
    keep the title, strip the param table and template picture, place
    map (left) + gunbarrel (right) with a subtitle line between."""
    _fit_title(slide, sc.title)
    for shape in list(slide.shapes):
        is_table = isinstance(shape, GraphicFrame) and shape.has_table
        if is_table or isinstance(shape, Picture):
            shape._element.getparent().remove(shape._element)

    _add_subtitle(slide, sc.subtitle)

    slide.shapes.add_picture(
        io.BytesIO(sc.map_png),
        Inches(_MARGIN_IN),
        Inches(_PANEL_TOP_IN),
        width=Inches(_PANEL_WIDTH_IN),
        height=Inches(_PANEL_HEIGHT_IN),
    )
    slide.shapes.add_picture(
        io.BytesIO(sc.gunbarrel_png),
        Inches(_MARGIN_IN + _PANEL_WIDTH_IN + _PANEL_GAP_IN),
        Inches(_PANEL_TOP_IN),
        width=Inches(_PANEL_WIDTH_IN),
        height=Inches(_PANEL_HEIGHT_IN),
    )


def _fit_title(slide: Slide, text: str) -> None:
    """Set the title, stepping the font down for long titles so they stay
    on one line instead of wrapping into the subtitle."""
    _set_title_text(slide, text)
    size = next((pt for limit, pt in _TITLE_STEPS if len(text) <= limit), _TITLE_MIN_PT)
    if size is None:
        return
    title = _find_title_shape(slide)
    if title is None:
        return
    for para in title.text_frame.paragraphs:
        for run in para.runs:
            run.font.size = Pt(size)


def _add_subtitle(slide: Slide, text: str) -> None:
    """One line at 11pt; a longer subtitle wraps at 9pt (it used to run
    off the slide edge with wrapping off)."""
    if not text:
        return
    tb = slide.shapes.add_textbox(
        Inches(_MARGIN_IN),
        Inches(_SUBTITLE_TOP_IN),
        Inches(_SLIDE_WIDTH_IN - 2 * _MARGIN_IN),
        Inches(0.3),
    )
    tf = tb.text_frame
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    long_ = len(text) > _SUBTITLE_ONE_LINE_CHARS
    tf.word_wrap = long_
    run = tf.paragraphs[0].add_run()
    run.text = text
    run.font.size = Pt(9 if long_ else _SUBTITLE_FONT_PT)


def _build_comparison_slide(slide: Slide, cp: ComparisonSlideInput) -> None:
    """Title + subtitle + one full-width 6-panel comparison figure."""
    _fit_title(slide, cp.title)
    for shape in list(slide.shapes):
        is_table = isinstance(shape, GraphicFrame) and shape.has_table
        if is_table or isinstance(shape, Picture):
            shape._element.getparent().remove(shape._element)

    _add_subtitle(slide, cp.subtitle)

    # Placed at the captured aspect, shrunk to clear the template footer
    # band (~6.9") and centred.
    width = _COMPARISON_PLACED_HEIGHT_IN * _COMPARISON_WIDTH_IN / _COMPARISON_HEIGHT_IN
    slide.shapes.add_picture(
        io.BytesIO(cp.figure_png),
        Inches((_SLIDE_WIDTH_IN - width) / 2),
        Inches(_PANEL_TOP_IN),
        width=Inches(width),
        height=Inches(_COMPARISON_PLACED_HEIGHT_IN),
    )


# 10 rows keep a fully wrapped table (3-line QC cells) clear of the note
# band pinned above the template footer.
SUMMARY_ROWS_PER_SLIDE = 10
_SUMMARY_NOTE_TOP_IN = 6.25
# Relative column widths for SUMMARY_HEADERS (zone + curve names and the
# QC notes are the wide ones).
_SUMMARY_COL_WEIGHTS = (
    1.3,
    1.7,
    0.9,
    0.75,
    0.6,
    0.75,
    0.7,
    0.75,
    1.0,
    0.5,
    0.75,
    0.7,
    0.75,
    0.6,
    1.6,
)
_FLAG_RED = RGBColor(0xDC, 0x26, 0x26)  # type: ignore[no-untyped-call]
_HEADER_FILL = RGBColor(0xF3, 0xF4, 0xF6)  # type: ignore[no-untyped-call]


def _build_table_slide(
    slide: Slide,
    title: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    weights: Sequence[float],
    note: str,
    flag: Callable[[int, int], bool] | None = None,
) -> None:
    """Native table under the title (header row shaded, 8 pt), the
    conventions note in a fixed band above the footer. ``flag(i, j)``
    (0-based data row, column) marks a cell bold red."""
    _fit_title(slide, title)
    for shape in list(slide.shapes):
        is_table = isinstance(shape, GraphicFrame) and shape.has_table
        if is_table or isinstance(shape, Picture):
            shape._element.getparent().remove(shape._element)
    assert len(weights) == len(headers)
    width = _SLIDE_WIDTH_IN - 2 * _MARGIN_IN
    gf = slide.shapes.add_table(
        len(rows) + 1,
        len(headers),
        Inches(_MARGIN_IN),
        Inches(_SUBTITLE_TOP_IN),
        Inches(width),
        Inches(0.25 * (len(rows) + 1)),
    )
    table = gf.table
    total = sum(weights)
    for j, w in enumerate(weights):
        table.columns[j].width = Inches(width * w / total)
    for i, cells in enumerate([headers, *rows]):
        for j, text in enumerate(cells):
            cell = table.cell(i, j)
            cell.margin_left = cell.margin_right = Inches(0.04)
            cell.margin_top = cell.margin_bottom = Inches(0.02)
            tf = cell.text_frame
            tf.word_wrap = True
            run = tf.paragraphs[0].add_run()
            run.text = text
            run.font.size = Pt(8)
            if i == 0:
                run.font.bold = True
                cell.fill.solid()  # type: ignore[no-untyped-call]
                cell.fill.fore_color.rgb = _HEADER_FILL
                run.font.color.rgb = RGBColor(0x11, 0x18, 0x27)  # type: ignore[no-untyped-call]
            elif flag is not None and flag(i - 1, j):
                run.font.bold = True
                run.font.color.rgb = _FLAG_RED
    # Fixed band: table rows grow when cells wrap, so a position computed
    # from the row count lands inside the table.
    tb = slide.shapes.add_textbox(
        Inches(_MARGIN_IN), Inches(_SUMMARY_NOTE_TOP_IN), Inches(width), Inches(0.6)
    )
    tf = tb.text_frame
    tf.word_wrap = True
    run = tf.paragraphs[0].add_run()
    run.text = note
    run.font.size = Pt(9)


def _build_summary_slide(slide: Slide, zones: list[ZoneSummary], title: str) -> None:
    """One row per zone (``summary_cells``); gap-flagged TC-vs-Novi cells red."""
    flagged_cols = {
        SUMMARY_HEADERS.index("Oil TC vs Novi"): "oil",
        SUMMARY_HEADERS.index("Gas TC vs Novi"): "gas",
    }
    _build_table_slide(
        slide,
        title,
        SUMMARY_HEADERS,
        [summary_cells(z) for z in zones],
        _SUMMARY_COL_WEIGHTS,
        SUMMARY_NOTE,
        flag=lambda i, j: j in flagged_cols and zones[i].streams[flagged_cols[j]].gap_flag,
    )


_COHORT_COL_WEIGHTS = (2.2, 1.6, 0.9, 0.65, 0.6, 0.65, 0.65, 0.85, 0.8, 2.0)
# Cohort rows are one line each (names don't wrap at these widths), so a
# slide holds more of them than the summary.
COHORT_ROWS_PER_SLIDE = 16


def _di_pinned(rows: Sequence[Sequence[str]], col: int) -> Callable[[int, int], bool]:
    """Flag predicate: the pinned cell carries a Di bound."""
    return lambda i, j: j == col and "Di" in rows[i][j]


def _build_cohort_slides(pres: Any, table: CohortTableInput) -> None:
    """The curve's wells, paginated; a pinned oil Di is flagged red (b at
    its 0.9/1.2 bound is common by design and stays black)."""
    pin_col = COHORT_HEADERS.index("Oil fit pinned")
    n_pages = max(1, -(-len(table.rows) // COHORT_ROWS_PER_SLIDE))
    # Balanced pages (17 wells -> 9 + 8, not 16 + an orphan row).
    per_page = max(1, -(-len(table.rows) // n_pages))
    for page in range(n_pages):
        rows = table.rows[page * per_page : (page + 1) * per_page]
        _duplicate_slide(pres, source_idx=0)
        _build_table_slide(
            pres.slides[-1],
            f"{table.curve_name} — curve wells ({len(table.rows)})"
            + (f" {page + 1}/{n_pages}" if n_pages > 1 else ""),
            COHORT_HEADERS,
            rows,
            _COHORT_COL_WEIGHTS,
            COHORT_NOTE,
            flag=_di_pinned(rows, pin_col),
        )


def _delete_slide(pres: Any, idx: int) -> None:
    """Remove a slide from the deck. python-pptx has no delete API; the
    documented workaround is to drop the slide's relationship and its
    entry in the slide-id list."""
    slide = pres.slides[idx]
    # Find the rId for this slide part and drop it.
    for rel_id, rel in list(pres.part.rels.items()):
        if rel.target_part is slide.part:
            pres.part.drop_rel(rel_id)
            break
    sld_id_lst = pres.slides._sldIdLst
    sld_id_lst.remove(list(sld_id_lst)[idx])
