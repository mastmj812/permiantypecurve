"""#eng-updates drop notice for a Blue Ox curve drop — pure helpers.

Steven's posted rule for #eng-updates: one message, codename first (the
ArcMap / Deal Folder name). Shape of record (blueox-curve-drop skill,
step 10)::

    <Codename> new drop file: <filename>[, supersedes <prior>]. <what changed>

``summarize_export`` snapshots what a workbook carried at export time;
``draft_body`` turns (current, previously-notified) snapshots into the
message after the codename; ``block_reason`` is the posting gate. No
DB, no HTTP — the endpoint in app.api.deals owns both.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.exports.blueox import BlueOxExportData


def summarize_export(data: BlueOxExportData) -> dict[str, Any]:
    """JSON-safe snapshot of the zones/locations/risking a drop carries."""
    zones: list[dict[str, Any]] = []
    for z in data.zones:
        muls = {
            str(r["stream"]): float(r.get("risk_mult", 1.0))
            for r in z.curve_params
            if r.get("level") == "P50" and r.get("stream") in ("oil", "gas")
        }
        zones.append(
            {
                "zone": z.zone_name,
                "category": z.reserve_category,
                # Same rule as the manifest's Block B gross_locations: PDP rows
                # are existing producers carried for gunbarrel context.
                "locations": sum(1 for inv in z.inventory if inv.category != "PDP"),
                "risk_mult": muls,
            }
        )
    return {
        "zones": zones,
        "locations": sum(z["locations"] for z in zones),
        "risking": data.risking,
    }


def _mult_text(muls: Mapping[str, float]) -> str | None:
    oil = muls.get("oil", 1.0)
    gas = muls.get("gas", 1.0)
    if oil == 1.0 and gas == 1.0:
        return None
    if oil == gas:
        return f"×{oil:.2f} oil/gas"
    return f"×{oil:.2f} oil / ×{gas:.2f} gas"


def draft_body(
    filename: str,
    summary: Mapping[str, Any],
    previous: tuple[str, Mapping[str, Any]] | None = None,
) -> str:
    """Message text AFTER the codename. ``previous`` = (filename, summary)
    of the last NOTIFIED drop on the deal — what Steven currently holds."""
    zones = list(summary.get("zones", []))
    names = [z["zone"] for z in zones]
    head = f"new drop file: {filename}"
    if previous is not None and previous[0] != filename:
        head += f", supersedes {previous[0]}"
    parts = [head + "."]

    locs = f"{len(names)} zones ({', '.join(names)}), {summary.get('locations', 0)} PUD/UPSIDE locations"
    if previous is not None:
        prev_locs = previous[1].get("locations")
        if prev_locs is not None and prev_locs != summary.get("locations"):
            locs += f" (was {prev_locs})"
    parts.append(locs + ".")

    if previous is not None:
        prev_names = [z["zone"] for z in previous[1].get("zones", [])]
        added = [n for n in names if n not in prev_names]
        dropped = [n for n in prev_names if n not in names]
        if added and dropped:
            parts.append(f"Zones added: {', '.join(added)}; dropped: {', '.join(dropped)}.")
        elif added:
            parts.append(f"Zones added: {', '.join(added)}.")
        elif dropped:
            parts.append(f"Zones dropped: {', '.join(dropped)}.")

    risked = [
        f"{z['zone']} {txt}"
        for z in zones
        if (txt := _mult_text(z.get("risk_mult", {}))) is not None
    ]
    if not risked:
        parts.append("Unrisked.")
    elif len(risked) == len(zones):
        parts.append(f"Risked: {', '.join(risked)}.")
    else:
        parts.append(f"Risked: {', '.join(risked)}; other zones unrisked.")
    return " ".join(parts)


def block_reason(
    *,
    allow_stale: bool,
    notified_at: datetime | None,
) -> str | None:
    """Why the latest export may not be posted (None = OK to post)."""
    if allow_stale:
        return (
            "this export was built with allow_stale (narvi changed after the config "
            "was pinned) — re-save the config to re-pin, re-export, then notify"
        )
    if notified_at is not None:
        return f"already posted to #eng-updates {notified_at:%Y-%m-%d %H:%M} UTC"
    return None


def compose_message(codename: str, body: str) -> str:
    return f"{codename.strip()} {body.strip()}"
