"""CLI entry: seed (or re-seed) one or more counties from Enverus.

    python -m app.seed.seed_county                            # Loving + Reeves
    python -m app.seed.seed_county --counties Loving,Reeves   # explicit
    python -m app.seed.seed_county --county Reeves            # one-off

Defaults to the project's canonical multi-county scope
(``DEFAULT_COUNTIES`` in ``app.sync.orchestrator``). The legacy singular
``--county`` flag still works for one-off pulls — if both are passed,
``--counties`` wins.

Phase 2 of the LateralLine migration retired the survey-fetch phase —
``--no-surveys`` is preserved as a no-op so old shell history doesn't
break, but the orchestrator no longer pulls surveys for any well.
Wellstick now comes from ``LateralLine`` on the wells endpoint
(~99% horizontal coverage) with a surface→BH straight-line fallback.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from app.core.logging import configure_logging, get_logger
from app.sync.orchestrator import (
    DEFAULT_BASIN,
    DEFAULT_COUNTIES,
    DEFAULT_COUNTY,
    DEFAULT_FIRST_PROD_AFTER,
    DEFAULT_MIN_LATERAL_FT,
    sync_counties,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed wells/production/surveys from Enverus")
    parser.add_argument("--basin", default=DEFAULT_BASIN)
    parser.add_argument(
        "--counties",
        default=None,
        help=(
            "Comma-separated county names. Defaults to "
            f"{','.join(DEFAULT_COUNTIES)}."
        ),
    )
    parser.add_argument(
        "--county",
        default=None,
        help=(
            f"Legacy singular form. Use --counties for the multi-county scope. "
            f"Passing an empty string {DEFAULT_COUNTY!r} legacy fallback no longer "
            "supported — use --counties \"\" if you really want the whole basin."
        ),
    )
    parser.add_argument(
        "--no-production",
        action="store_true",
        help="Skip the monthly production pull",
    )
    parser.add_argument(
        "--no-surveys",
        action="store_true",
        help="(no-op — survey phase retired in Phase 2 of LateralLine migration)",
    )
    parser.add_argument(
        "--first-prod-after",
        default=DEFAULT_FIRST_PROD_AFTER.isoformat() if DEFAULT_FIRST_PROD_AFTER else None,
        help=(
            "ISO date (YYYY-MM-DD). Only pull wells whose FirstProdDate is "
            f"after this (default {DEFAULT_FIRST_PROD_AFTER.isoformat() if DEFAULT_FIRST_PROD_AFTER else 'unset'} "
            "— excludes pre-modern conventional verticals). Pass an empty "
            "string to disable."
        ),
    )
    parser.add_argument(
        "--min-lateral-ft",
        type=float,
        default=DEFAULT_MIN_LATERAL_FT,
        help=(
            "Minimum LateralLength_FT to include. Default unset; pass "
            "e.g. 2000 to drop the rare modern vertical that slips past "
            "the date filter."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    log = get_logger("seed")

    # Precedence: --counties (multi) > --county (legacy single) > DEFAULT_COUNTIES.
    if args.counties is not None:
        counties = tuple(c.strip() for c in args.counties.split(",") if c.strip())
    elif args.county:
        counties = (args.county,)
    else:
        counties = DEFAULT_COUNTIES

    if not counties:
        log.warning("seeding_entire_basin_not_supported")
        parser.error("at least one county required; pass --counties Loving,Reeves")

    # Empty string disables the date floor; otherwise parse ISO.
    first_prod_after: date | None
    if args.first_prod_after in (None, ""):
        first_prod_after = None
    else:
        try:
            first_prod_after = date.fromisoformat(args.first_prod_after)
        except ValueError:
            parser.error(
                f"--first-prod-after must be ISO date (YYYY-MM-DD); got "
                f"{args.first_prod_after!r}"
            )

    log.info(
        "seed_start",
        basin=args.basin,
        counties=list(counties),
        first_prod_after=first_prod_after.isoformat() if first_prod_after else None,
        min_lateral_ft=args.min_lateral_ft,
    )
    counts = sync_counties(
        basin=args.basin,
        counties=counties,
        pull_production=not args.no_production,
        first_prod_after=first_prod_after,
        min_lateral_ft=args.min_lateral_ft,
    )
    log.info("seed_complete", basin=args.basin, counties=list(counties), counts=counts)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
