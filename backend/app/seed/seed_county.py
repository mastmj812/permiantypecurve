"""CLI entry: seed (or re-seed) a county from Enverus.

    python -m app.seed.seed_county --county Loving

Defaults to Loving County for fast first-run validation. Pass --county "" to
pull the entire basin.
"""

from __future__ import annotations

import argparse
import sys

from app.core.logging import configure_logging, get_logger
from app.sync.orchestrator import DEFAULT_BASIN, DEFAULT_COUNTY, sync_county


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed wells/production/surveys from Enverus")
    parser.add_argument("--basin", default=DEFAULT_BASIN)
    parser.add_argument(
        "--county",
        default=DEFAULT_COUNTY,
        help='County name. Use "" to pull the whole basin (slow).',
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

    county = args.county or None
    if county is None:
        log.warning("seeding_entire_basin", basin=args.basin)

    counts = sync_county(
        basin=args.basin,
        county=county or DEFAULT_COUNTY,
        pull_production=not args.no_production,
        pull_surveys=not args.no_surveys,
    )
    log.info("seed_complete", basin=args.basin, county=county, **counts)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
