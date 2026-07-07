"""Dry-run the bulk-refit guard: report forecast rows a refit would refuse.

    # Whole table:
    python -m app.cli.refit_guard_check

    # Scoped to an api10 list (CSV or repeated --api10s):
    python -m app.cli.refit_guard_check --api10s 42301343020000,42301372360000

Prints the ``(api10, stream)`` rows in the ambiguous ``manual_override=True,
locked=False`` state — the set a bulk refit would silently overwrite. Exit 0
with "0 rows at risk" means a bulk refit is safe (the guard in
``app.forecasting.orchestrator.forecast_wells`` will let it through). A
non-zero count lists the rows that must be triaged (lock the keepers, clear
manual_override on the rest) first. READ-ONLY — issues no writes.
"""

from __future__ import annotations

import argparse
import sys

from app.db.session import SessionLocal
from app.forecasting.orchestrator import bulk_refit_dry_run


def _parse_api10s(raw: list[str] | None) -> list[str] | None:
    if not raw:
        return None
    out: list[str] = []
    for chunk in raw:
        for piece in chunk.split(","):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report forecast rows a bulk refit would refuse (dry-run)."
    )
    parser.add_argument(
        "--api10s",
        action="append",
        default=None,
        help="Comma-separated api10 list (or repeated --api10s) to scope the "
        "check. Omit to check every forecast row.",
    )
    args = parser.parse_args(argv)

    api10s = _parse_api10s(args.api10s)
    scope_desc = f"{len(api10s)} api10s" if api10s is not None else "all wells"

    with SessionLocal() as session:
        at_risk = bulk_refit_dry_run(session, api10s)

    if not at_risk:
        print(f"Scope: {scope_desc}. 0 rows at risk — bulk refit is safe.")
        return 0

    print(
        f"Scope: {scope_desc}. {len(at_risk)} row(s) at risk "
        f"(manual_override=True, locked=False) — bulk refit is BLOCKED:"
    )
    for api10, stream in at_risk:
        print(f"  {api10}\t{stream}")
    print(
        "Triage these (lock the keepers, clear manual_override on the rest) "
        "before a bulk refit will run."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
