"""CLI entry: seed (or re-seed) one or more counties from Enverus.

    python -m app.seed.seed_county                            # Loving + Reeves
    python -m app.seed.seed_county --counties Loving,Reeves   # explicit
    python -m app.seed.seed_county --county Reeves            # one-off

Defaults to the project's canonical multi-county scope
(``DEFAULT_COUNTIES`` in ``app.sync.orchestrator``). The legacy singular
``--county`` flag still works for one-off pulls — if both are passed,
``--counties`` wins.
"""

from __future__ import annotations

import argparse
import sys

from app.core.logging import configure_logging, get_logger
from app.sync.orchestrator import (
    DEFAULT_BASIN,
    DEFAULT_COUNTIES,
    DEFAULT_COUNTY,
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
        help="Skip the directional survey pull",
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

    log.info("seed_start", basin=args.basin, counties=list(counties))
    counts = sync_counties(
        basin=args.basin,
        counties=counties,
        pull_production=not args.no_production,
        pull_surveys=not args.no_surveys,
    )
    log.info("seed_complete", basin=args.basin, counties=list(counties), counts=counts)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
