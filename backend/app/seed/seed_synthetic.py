"""CLI entry: drop deterministic synthetic wells into the DB.

    python -m app.seed.seed_synthetic                       # 50 wells × Loving + Reeves
    python -m app.seed.seed_synthetic --counties Loving     # one county only
    python -m app.seed.seed_synthetic --n 200                # bigger map test (per county)
    python -m app.seed.seed_synthetic --seed 99              # different roster

Routes each county's SyntheticEnverusClient through the same ``sync_county``
orchestrator the real Prism client uses — exercises the full ingest path
(upserts, heel + wellstick recomputation, calday/prodday rates, watermarks)
end to end. Counties are seeded sequentially; ``--n`` is per-county.
"""

from __future__ import annotations

import argparse
import sys

from app.core.logging import configure_logging, get_logger
from app.seed.synthetic_client import COUNTY_BOUNDS, SyntheticEnverusClient
from app.sync.orchestrator import DEFAULT_COUNTIES, sync_county


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic wells (no Enverus needed)")
    parser.add_argument("--n", type=int, default=50, help="well count per county")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for repeatability")
    parser.add_argument("--basin", default="Permian")
    parser.add_argument(
        "--counties",
        default=",".join(DEFAULT_COUNTIES),
        help=(
            "Comma-separated county names. Known: "
            f"{', '.join(sorted(COUNTY_BOUNDS))}."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--no-production", action="store_true", help="skip the production phase"
    )
    parser.add_argument(
        "--no-surveys", action="store_true", help="skip the survey phase"
    )
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    log = get_logger("seed.synthetic")

    counties = tuple(c.strip() for c in args.counties.split(",") if c.strip())
    if not counties:
        parser.error("--counties must list at least one county")

    # Bump the seed per county so wells in Loving and Reeves don't collide
    # on the (i % n) decorative bits — each county still deterministic
    # given the base seed.
    all_counts: dict[str, dict[str, int]] = {}
    for offset, county in enumerate(counties):
        log.info(
            "seeding_synthetic",
            n=args.n,
            seed=args.seed + offset,
            basin=args.basin,
            county=county,
        )
        client = SyntheticEnverusClient(
            n_wells=args.n,
            county=county,
            basin=args.basin,
            seed=args.seed + offset,
        )
        counts = sync_county(
            basin=args.basin,
            county=county,
            pull_production=not args.no_production,
            pull_surveys=not args.no_surveys,
            client=client,
        )
        all_counts[county] = counts
    log.info("synthetic_seed_complete", counts=all_counts)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
