"""Blue Ox curve-drop workbook builder.

Implements Deliverable A of the Blue Ox Engineering Curve-Drop Contract
v1 (2026-07-20, checked into engineering_db as
``docs/blue_ox_curve_drop_contract.md``): one governing curve workbook
per deal — zone sheets of monthly volumes, ``meta`` / ``inventory`` /
``analog_production`` / ``curve_params`` / ``manifest`` sheets, and one
``<Zone> meta`` analog sheet per zone.

Pure function of :class:`BlueOxExportData` -> xlsx bytes; no DB, no
HTTP (callable from a future server action). DB assembly lives in
``app.api.deals.export_deal_blueox``.

Convention boundary — the two flips this module owns:

* **Percentiles.** Blue Ox files are ASCENDING (their p10 = the low
  case). anduin persists the SPE orientation (P10 = high, migration
  0021). The caller maps levels via :data:`LEVEL_TO_SPE_KEY` when
  assembling ``ZoneData.volumes`` / ``curve_params`` (our p90 fit
  feeds their ``_p10`` columns); this module validates the result is
  genuinely ascending (contract §1.1 self-check) so a half-flipped
  workbook can never leave the building.
* **NGL.** Per the 2026-07-20 amendment, Blue Ox derives NGL via their
  own yield. ``ngl_bbl`` columns are emitted all-zero at every level
  (keeping the contract's complete-triplet rule) and ``ngl_basis``
  reads ``derived_by_blue_ox_via_yield``.
"""

from __future__ import annotations

import io
import itertools
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Contract §1.1: sums must be strictly ascending across levels. Order of
# delivered percentile levels (base P50 slots between P25 and P75).
BLUEOX_LEVEL_ORDER: tuple[str, ...] = ("P10", "P25", "P50", "P75", "P90")
OPTIONAL_LEVELS: tuple[str, ...] = ("P10", "P25", "P75", "P90")

# Blue Ox ascending level -> anduin SPE fit key. Their p10 is the LOW
# case = our P90 fit (SPE P10 = high). The ONLY place this mapping may
# live — callers import it rather than re-deriving it.
LEVEL_TO_SPE_KEY: dict[str, str] = {
    "P10": "p90",
    "P25": "p75",
    "P50": "p50",
    "P75": "p25",
    "P90": "p10",
}

# Contract Principle 2: reserved sheet names (case-insensitive) + zone
# sheet-name mechanics.
RESERVED_SHEET_NAMES: frozenset[str] = frozenset(
    {"meta", "inventory", "manifest", "analog_production", "curve_params"}
)
ZONE_NAME_MAX = 26
_ZONE_FORBIDDEN_RE = re.compile(r"[:\\/?*\[\]]")

# Contract §1 filename rule: these stems are reserved on the Blue Ox
# side and must not appear anywhere in the workbook filename.
RESERVED_FILENAME_STEMS: tuple[str, ...] = ("type_curves", "areas", "pdp", "pinned")

# Contract §1.3 hard bounds on inventory laterals.
LATERAL_MIN_FT = 3_000.0
LATERAL_MAX_FT = 25_000.0

# Manifest Block A constants the contract mandates verbatim.
PERCENTILE_ORIENTATION = "ascending"
GAS_BASIS = "wellhead_unshrunk"
# Manifest `risking` tokens (2026-07-24 amendment, pending Blue Ox ack):
# `unrisked` when every curve ships at MUL 1.0; the risked token when
# any zone's volumes carry a declared geologic multiplier — the
# per-stream values are disclosed in curve_params.risk_mult.
RISKING_UNRISKED = "unrisked"
RISKING_APPLIED = "geologic_multipliers_applied"
# 2026-07-20 amendment: Blue Ox applies their own yield; the drop
# carries all-zero ngl_bbl columns.
NGL_BASIS = "derived_by_blue_ox_via_yield"

_BOLD = Font(bold=True)


class BlueOxContractError(ValueError):
    """A contract violation that must block the export (never a warning)."""


# Handoff categories on the inventory sheet. PUD/UPSIDE are the valued
# location classes (they count toward Block B gross_locations and
# lateral means); PDP rows are EXISTING producers carried for the
# downstream gunbarrel automation — displayed, never counted, and
# exempt from the planned-well lateral bounds.
INVENTORY_CATEGORIES: tuple[str, ...] = ("PDP", "PUD", "UPSIDE")


@dataclass(frozen=True)
class InventoryRow:
    """One row on the handoff inventory sheet (contract §1.3 + the
    category amendment): a planned undeveloped well (PUD/UPSIDE) or an
    existing producer shown for gunbarrel context (PDP)."""

    producing_lateral_ft: float
    drilled_lateral_ft: float
    well_name: str | None = None
    category: str = "PUD"


@dataclass(frozen=True)
class ZoneData:
    """Everything the workbook needs for one zone.

    ``volumes`` maps Blue Ox level label -> stream -> monthly gross
    wellhead volumes (bbl or Mcf per month), already percentile-flipped
    by the caller and already at the zone's normalization basis. ``P50``
    must carry ``oil`` and ``gas`` (``water`` optional); every other
    delivered level carries exactly ``oil`` and ``gas``.

    ``curve_params`` rows carry the engineer's decline params per
    stream x level with keys ``stream`` / ``level`` / ``qi`` /
    ``qi_units`` / ``qi_basis`` / ``risk_mult`` / ``b_factor`` /
    ``di`` / ``dmin`` / ``notes``. ``qi_basis`` defaults to
    ``fitted_qi`` and ``risk_mult`` to 1.0 when a row omits them.
    """

    zone_name: str
    reserve_category: str  # "PUD" | "UPSIDE"
    normalization_basis: str  # "per_1000_lateral_ft" | "per_well"
    volumes: Mapping[str, Mapping[str, Sequence[float]]]
    curve_params: Sequence[Mapping[str, Any]]
    analog_headers: Sequence[str]
    analog_rows: Sequence[Sequence[Any]]
    inventory: Sequence[InventoryRow] = field(default_factory=tuple)


@dataclass(frozen=True)
class BlueOxExportData:
    """Pure input to :func:`build_blueox_workbook`."""

    codename: str
    export_date: date
    curve_months: int
    levels: tuple[str, ...]  # delivered levels beyond P50, e.g. ("P10", "P90")
    prepared_by: str
    source_system: str
    governing_export: str
    curve_params_source: str
    zones: Sequence[ZoneData]
    production_headers: Sequence[str]
    production_rows: Sequence[Sequence[Any]]
    production_history_through: str  # "YYYY-MM"
    # api10s listed on analog sheets with no available history, declared
    # in the manifest per contract §1.5 ("never silently absent").
    history_exceptions: tuple[str, ...] = ()
    # Inventory benches deliberately excluded from this workbook (e.g.
    # carried in a sibling deal's drop), declared in the manifest —
    # Principle 4: everything declared, nothing implied. Strings like
    # "WDFD (4 wells)".
    inventory_exclusions: tuple[str, ...] = ()
    # Existing producers whose bench maps to NO zone in this workbook —
    # still written to the inventory sheet (the downstream gunbarrel
    # automation needs the whole DSU stack), with the bench code as
    # `area`. Deliberately the one place an `area` value may not match
    # a zone sheet; loaders must tolerate it on category=PDP rows only.
    pdp_context_rows: Sequence[tuple[str, InventoryRow]] = ()
    # Manifest Block A `risking` value — RISKING_UNRISKED unless any
    # zone's curve carries a geologic multiplier (2026-07-24 amendment).
    # The caller computes this from the curves it assembled.
    risking: str = RISKING_UNRISKED


def blueox_filename(codename: str, export_date: date) -> str:
    return f"{codename}_curves_{export_date.isoformat()}.xlsx"


def monthly_volumes_from_rates(
    rates: Sequence[float], curve_months: int, days_per_month: float
) -> list[float]:
    """Daily-rate array -> monthly volumes via the shared trapezoid rule.

    Month K's volume = (rate at start + rate at end) / 2 * dt, last
    available month flat-extrapolated — the SAME rule as the deal xlsx /
    CSV cum columns and ``ramp_arps.trapezoid_eur``, so the drop's Block
    B EURs stay on the one integration convention. Months past the end
    of ``rates`` zero-fill (contract §1.1 tail rule).
    """
    n = len(rates)
    out: list[float] = []
    for i in range(curve_months):
        if i >= n:
            out.append(0.0)
            continue
        a = float(rates[i])
        nxt = rates[i + 1] if i + 1 < n else None
        b = float(nxt) if nxt is not None and math.isfinite(float(nxt)) else a
        out.append((a + b) / 2.0 * days_per_month)
    return out


# ============================ validation ============================


def _validate_zone_name(name: str) -> str | None:
    """Return an error string if ``name`` breaks Principle 2, else None."""
    if not name:
        return "zone name is empty"
    if len(name) > ZONE_NAME_MAX:
        return f"zone name {name!r} exceeds {ZONE_NAME_MAX} characters"
    if _ZONE_FORBIDDEN_RE.search(name):
        return f"zone name {name!r} contains a forbidden character (: \\ / ? * [ ])"
    if name != name.strip() or name.startswith("'") or name.endswith("'"):
        return f"zone name {name!r} has leading/trailing spaces or apostrophes"
    if name.casefold() in RESERVED_SHEET_NAMES:
        return f"zone name {name!r} is a reserved sheet name"
    return None


def _delivered_levels(data: BlueOxExportData) -> list[str]:
    """All delivered levels in ascending contract order, P50 included."""
    return [
        lv for lv in BLUEOX_LEVEL_ORDER if lv == "P50" or lv in data.levels
    ]


def _validate(data: BlueOxExportData) -> None:
    errors: list[str] = []

    filename = blueox_filename(data.codename, data.export_date).lower()
    for stem in RESERVED_FILENAME_STEMS:
        # "curves" is the mandated stem; only flag the reserved ones.
        if stem in filename.replace("_curves_", "_"):
            errors.append(
                f"filename {filename!r} contains reserved stem {stem!r}"
            )
    if not data.codename or not data.codename.strip():
        errors.append("codename is empty")
    if data.curve_months < 1:
        errors.append(f"curve_months must be >= 1 (got {data.curve_months})")
    for lv in data.levels:
        if lv not in OPTIONAL_LEVELS:
            errors.append(f"unknown percentile level {lv!r}")
    if len(set(data.levels)) != len(data.levels):
        errors.append(f"duplicate percentile levels in {data.levels!r}")
    if not data.zones:
        errors.append("no zones supplied")
    if not re.fullmatch(r"\d{4}-\d{2}", data.production_history_through):
        errors.append(
            "production_history_through must be YYYY-MM "
            f"(got {data.production_history_through!r})"
        )

    seen_names: set[str] = set()
    levels = _delivered_levels(data)
    for z in data.zones:
        zerr = _validate_zone_name(z.zone_name)
        if zerr:
            errors.append(zerr)
        if z.zone_name.casefold() in seen_names:
            errors.append(f"duplicate zone name {z.zone_name!r}")
        seen_names.add(z.zone_name.casefold())

        if z.reserve_category not in ("PUD", "UPSIDE"):
            errors.append(
                f"{z.zone_name}: reserve_category must be PUD or UPSIDE "
                f"(got {z.reserve_category!r})"
            )
        if z.normalization_basis not in ("per_1000_lateral_ft", "per_well"):
            errors.append(
                f"{z.zone_name}: normalization_basis must be "
                f"per_1000_lateral_ft or per_well (got {z.normalization_basis!r})"
            )

        # Complete triplets: every delivered level needs oil + gas
        # vectors (ngl is emitted all-zero on our side). Water rides on
        # the base level only.
        for lv in levels:
            by_stream = z.volumes.get(lv)
            if by_stream is None:
                errors.append(f"{z.zone_name}: level {lv} missing entirely")
                continue
            required = ("oil", "gas")
            for stream in required:
                vec = by_stream.get(stream)
                if vec is None:
                    errors.append(
                        f"{z.zone_name}: level {lv} missing {stream} "
                        "(partial triplet is a hard failure)"
                    )
                    continue
                if len(vec) != data.curve_months:
                    errors.append(
                        f"{z.zone_name}: {stream} {lv} has {len(vec)} rows, "
                        f"expected curve_months={data.curve_months}"
                    )
                if any(v is None or not math.isfinite(float(v)) or float(v) < 0 for v in vec):
                    errors.append(
                        f"{z.zone_name}: {stream} {lv} contains negative or "
                        "non-finite values"
                    )
            extra = set(by_stream) - {"oil", "gas", "water"}
            if extra:
                errors.append(
                    f"{z.zone_name}: level {lv} has unsupported streams {sorted(extra)}"
                )
            if lv != "P50" and "water" in by_stream:
                errors.append(
                    f"{z.zone_name}: percentile water columns are not allowed "
                    f"(water at level {lv})"
                )
        water = z.volumes.get("P50", {}).get("water")
        if water is not None and len(water) != data.curve_months:
            errors.append(
                f"{z.zone_name}: water has {len(water)} rows, "
                f"expected curve_months={data.curve_months}"
            )

        # Ascending monotonicity (contract §1.1 self-check): strictly
        # increasing column sums across delivered levels per stream.
        if len(levels) > 1:
            for stream in ("oil", "gas"):
                sums = [
                    sum(float(v) for v in z.volumes.get(lv, {}).get(stream, []))
                    for lv in levels
                ]
                if any(a >= b for a, b in itertools.pairwise(sums)):
                    errors.append(
                        f"{z.zone_name}: {stream} sums are not strictly ascending "
                        f"across {levels} (got {[round(s, 1) for s in sums]}) — "
                        "check the SPE->ascending percentile flip"
                    )

        # curve_params: oil + gas P50 rows minimum, every row labelled
        # with a delivered level.
        param_keys = {(str(r.get("stream")), str(r.get("level"))) for r in z.curve_params}
        for stream in ("oil", "gas"):
            if (stream, "P50") not in param_keys:
                errors.append(f"{z.zone_name}: curve_params missing {stream} P50 row")
        for stream, lv in param_keys:
            if lv not in levels:
                errors.append(
                    f"{z.zone_name}: curve_params row {stream} {lv} is not a "
                    "delivered level"
                )

        # Analog sheet: exactly one header containing "api".
        api_cols = [h for h in z.analog_headers if "api" in str(h).lower()]
        if len(api_cols) != 1:
            errors.append(
                f"{z.zone_name}: analog sheet must have exactly one column "
                f"containing 'api' (got {api_cols!r})"
            )
        if not z.analog_rows:
            errors.append(f"{z.zone_name}: analog sheet has no wells")

        for inv in z.inventory:
            if inv.category not in INVENTORY_CATEGORIES:
                errors.append(
                    f"{z.zone_name}: inventory category {inv.category!r} not in "
                    f"{INVENTORY_CATEGORIES}"
                )
            if inv.category == "PDP":
                # Existing producers are display rows for the gunbarrel
                # automation — the planned-well bounds don't apply.
                continue
            for label, val in (
                ("producing_lateral_ft", inv.producing_lateral_ft),
                ("drilled_lateral_ft", inv.drilled_lateral_ft),
            ):
                if not (LATERAL_MIN_FT <= float(val) <= LATERAL_MAX_FT):
                    errors.append(
                        f"{z.zone_name}: {label} {val} outside "
                        f"{LATERAL_MIN_FT:.0f}-{LATERAL_MAX_FT:.0f} ft"
                    )

    # analog_production <-> analog sheets tie-out (both directions),
    # minus the declared exceptions.
    analog_apis = {
        str(row[_api_col_index(z.analog_headers)])
        for z in data.zones
        for row in z.analog_rows
    }
    prod_headers_l = [str(h).lower() for h in data.production_headers]
    for col in ("api10", "date", "oil_bbl", "gas_mcf"):
        if col not in prod_headers_l:
            errors.append(f"analog_production missing required column {col!r}")
    if "api10" in prod_headers_l:
        api_idx = prod_headers_l.index("api10")
        prod_apis = {str(r[api_idx]) for r in data.production_rows}
        missing = analog_apis - prod_apis - set(data.history_exceptions)
        if missing:
            errors.append(
                "analog wells with no production history and no declared "
                f"exception: {sorted(missing)}"
            )
        orphans = prod_apis - analog_apis
        if orphans:
            errors.append(
                f"analog_production rows for wells not on any analog sheet: "
                f"{sorted(orphans)}"
            )

    # Unzoned PDP context rows: display-only, must actually be PDP.
    for area, invr in data.pdp_context_rows:
        if invr.category != "PDP":
            errors.append(
                f"pdp_context_rows entry {invr.well_name!r} ({area}) must be "
                f"category PDP (got {invr.category!r})"
            )

    if errors:
        raise BlueOxContractError(
            "Blue Ox contract violations:\n- " + "\n- ".join(errors)
        )


def _api_col_index(headers: Sequence[str]) -> int:
    for i, h in enumerate(headers):
        if "api" in str(h).lower():
            return i
    return 0


# ============================ sheet writers ============================


def _bold_row(ws: Any, row: int, n_cols: int) -> None:
    for col in range(1, n_cols + 1):
        ws.cell(row=row, column=col).font = _BOLD


def _zone_sheet_columns(z: ZoneData, levels: Sequence[str]) -> list[tuple[str, str, str]]:
    """(header, level, stream) triples in contract column order."""
    cols: list[tuple[str, str, str]] = [
        ("oil_bbl", "P50", "oil"),
        ("gas_mcf", "P50", "gas"),
        ("ngl_bbl", "P50", "ngl"),
    ]
    if "water" in z.volumes.get("P50", {}):
        cols.append(("water_bbl", "P50", "water"))
    for lv in levels:
        if lv == "P50":
            continue
        suffix = lv.lower()  # "P10" -> "p10"
        cols.append((f"oil_bbl_{suffix}", lv, "oil"))
        cols.append((f"gas_mcf_{suffix}", lv, "gas"))
        cols.append((f"ngl_bbl_{suffix}", lv, "ngl"))
    return cols


def _write_zone_sheet(ws: Any, z: ZoneData, data: BlueOxExportData) -> None:
    levels = _delivered_levels(data)
    cols = _zone_sheet_columns(z, levels)
    ws.append([c[0] for c in cols])
    _bold_row(ws, 1, len(cols))

    vectors: list[Sequence[float]] = []
    zero = [0.0] * data.curve_months
    for _header, lv, stream in cols:
        if stream == "ngl":
            vectors.append(zero)  # amendment: Blue Ox derives NGL via yield
        else:
            vectors.append(z.volumes[lv][stream])
    for i in range(data.curve_months):
        ws.append([float(vec[i]) for vec in vectors])

    ws.freeze_panes = "A2"
    for col_idx in range(1, len(cols) + 1):
        letter = get_column_letter(col_idx)
        ws.column_dimensions[letter].number_format = "#,##0.0"
        ws.column_dimensions[letter].width = 14


def _write_meta(ws: Any, data: BlueOxExportData) -> None:
    ws.append(["area", "normalization_basis", "reserve_category"])
    _bold_row(ws, 1, 3)
    for z in data.zones:
        ws.append([z.zone_name, z.normalization_basis, z.reserve_category])
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 18


def _write_inventory(ws: Any, data: BlueOxExportData) -> None:
    # well_name is optional; include the column only when every row has
    # one (contract formatting rule: no blank cells mid-column).
    all_named = all(
        inv.well_name
        for z in data.zones for inv in z.inventory
    ) and all(inv.well_name for _area, inv in data.pdp_context_rows)
    header = ["area", "category", "producing_lateral_ft", "drilled_lateral_ft"]
    if all_named:
        header.append("well_name")
    ws.append(header)
    _bold_row(ws, 1, len(header))

    def _row(area: str, inv: InventoryRow) -> None:
        row: list[Any] = [
            area,
            inv.category,
            float(inv.producing_lateral_ft),
            float(inv.drilled_lateral_ft),
        ]
        if all_named:
            row.append(inv.well_name)
        ws.append(row)

    for z in data.zones:
        for inv in z.inventory:
            _row(z.zone_name, inv)
    # Existing producers with no zone here: area = the bench code (the
    # one sanctioned mismatch with the zone-sheet names; PDP rows only).
    for area, inv in data.pdp_context_rows:
        _row(area, inv)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 10
    for letter in ("C", "D"):
        ws.column_dimensions[letter].number_format = "#,##0"
        ws.column_dimensions[letter].width = 20
    ws.column_dimensions["E"].width = 26


def _write_analog_sheet(ws: Any, z: ZoneData) -> None:
    """Contract §1.4 shape: a ``per_well_summary`` marker in column A,
    headers on the next row, then one row per analog well."""
    ws.append(["per_well_summary"])
    ws.cell(row=1, column=1).font = _BOLD
    ws.append(list(z.analog_headers))
    _bold_row(ws, 2, len(z.analog_headers))
    for row in z.analog_rows:
        ws.append(list(row))
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 26
    for col_idx in range(3, len(z.analog_headers) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 14


def _write_analog_production(ws: Any, data: BlueOxExportData) -> None:
    ws.append(list(data.production_headers))
    _bold_row(ws, 1, len(data.production_headers))
    for row in data.production_rows:
        ws.append(list(row))
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 12
    for col_idx in range(3, len(data.production_headers) + 1):
        letter = get_column_letter(col_idx)
        ws.column_dimensions[letter].number_format = "#,##0.0"
        ws.column_dimensions[letter].width = 12


_CURVE_PARAM_COLS: tuple[str, ...] = (
    "area", "stream", "level", "qi", "qi_units", "qi_basis",
    "b_factor", "di", "di_convention", "dmin", "risk_mult", "notes",
)
QI_BASIS = "fitted_qi"
DI_CONVENTION = "nominal_annual"


def _write_curve_params(ws: Any, data: BlueOxExportData) -> None:
    ws.append(list(_CURVE_PARAM_COLS))
    _bold_row(ws, 1, len(_CURVE_PARAM_COLS))
    level_order = {lv: i for i, lv in enumerate(BLUEOX_LEVEL_ORDER)}
    for z in data.zones:
        rows = sorted(
            z.curve_params,
            key=lambda r: (str(r.get("stream")), level_order.get(str(r.get("level")), 99)),
        )
        for r in rows:
            ws.append([
                z.zone_name,
                r.get("stream"),
                r.get("level"),
                r.get("qi"),
                r.get("qi_units"),
                # Per-row basis (2026-07-24 risking amendment): a risked
                # row declares fitted_qi_risked + its multiplier; rows
                # from callers predating the amendment fall back to the
                # unrisked constants.
                r.get("qi_basis", QI_BASIS),
                r.get("b_factor"),
                r.get("di"),
                DI_CONVENTION,
                r.get("dmin"),
                r.get("risk_mult", 1.0),
                r.get("notes"),
            ])
    ws.column_dimensions["A"].width = 28
    for letter in ("D", "G", "H", "J", "K"):
        ws.column_dimensions[letter].number_format = "0.000"
    for col_idx in range(2, len(_CURVE_PARAM_COLS) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 16
    ws.column_dimensions[get_column_letter(len(_CURVE_PARAM_COLS))].width = 48


def _write_manifest(ws: Any, data: BlueOxExportData) -> None:
    block_a: list[tuple[str, Any]] = [
        ("deal_codename", data.codename),
        ("export_date", data.export_date.isoformat()),
        ("source_system", data.source_system),
        ("governing_export", data.governing_export),
        ("percentile_orientation", PERCENTILE_ORIENTATION),
        ("gas_basis", GAS_BASIS),
        ("ngl_basis", NGL_BASIS),
        ("risking", data.risking),
        ("curve_months", data.curve_months),
        ("production_history_through", data.production_history_through),
        ("curve_params_source", data.curve_params_source),
        ("prepared_by", data.prepared_by),
    ]
    if data.history_exceptions:
        block_a.append((
            "analog_history_exceptions",
            ", ".join(data.history_exceptions) + " (no monthly history in source)",
        ))
    if data.inventory_exclusions:
        block_a.append((
            "inventory_benches_excluded",
            "; ".join(data.inventory_exclusions),
        ))
    block_a.append((
        "inventory_category_basis",
        "PDP = existing producer (context only, not counted); "
        "PUD = pdp_count_3mi >= 3; UPSIDE = pdp_count_3mi <= 2 or unscored; "
        "narvi user overrides win",
    ))
    for key, value in block_a:
        ws.append([key, value])
        ws.cell(row=ws.max_row, column=1).font = _BOLD

    ws.append([])
    ws.append([])

    # Block B — reconciliation targets, computed from the SAME vectors /
    # inventory rows the sheets were written from (contract: "compute
    # Block B from the final sheets, not from the source system").
    header = [
        "area", "eur_oil_bbl", "eur_gas_mcf", "eur_ngl_bbl",
        "gross_locations", "avg_producing_lateral_ft", "avg_drilled_lateral_ft",
    ]
    ws.append(header)
    _bold_row(ws, ws.max_row, len(header))
    for z in data.zones:
        # PDP rows are display-only context — the valued location count
        # and lateral means cover PUD/UPSIDE rows exclusively.
        counted = [i for i in z.inventory if i.category != "PDP"]
        n_inv = len(counted)
        avg_prod = (
            sum(float(i.producing_lateral_ft) for i in counted) / n_inv
            if n_inv else 0.0
        )
        avg_drill = (
            sum(float(i.drilled_lateral_ft) for i in counted) / n_inv
            if n_inv else 0.0
        )
        ws.append([
            z.zone_name,
            sum(float(v) for v in z.volumes["P50"]["oil"]),
            sum(float(v) for v in z.volumes["P50"]["gas"]),
            0.0,  # ngl_bbl delivered all-zero (amendment)
            n_inv,
            avg_prod,
            avg_drill,
        ])
    ws.column_dimensions["A"].width = 34
    for col_idx in range(2, len(header) + 1):
        letter = get_column_letter(col_idx)
        ws.column_dimensions[letter].number_format = "#,##0.0"
        ws.column_dimensions[letter].width = 20
    ws.column_dimensions["B"].width = 44  # Block A values share column B


# ============================ entry point ============================


def build_blueox_workbook(data: BlueOxExportData) -> bytes:
    """Validate against the contract and emit the workbook bytes.

    Raises :class:`BlueOxContractError` (with every violation listed)
    rather than emitting a workbook that would bounce off the Blue Ox
    acceptance gate.
    """
    _validate(data)

    wb = Workbook()
    wb.remove(wb.active)

    for z in data.zones:
        _write_zone_sheet(wb.create_sheet(z.zone_name), z, data)
    _write_meta(wb.create_sheet("meta"), data)
    _write_inventory(wb.create_sheet("inventory"), data)
    for z in data.zones:
        _write_analog_sheet(wb.create_sheet(f"{z.zone_name} meta"), z)
    _write_analog_production(wb.create_sheet("analog_production"), data)
    _write_curve_params(wb.create_sheet("curve_params"), data)
    _write_manifest(wb.create_sheet("manifest"), data)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
