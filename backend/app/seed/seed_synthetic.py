"""CLI entry: drop deterministic synthetic wells into the DB.

    python -m app.seed.seed_synthetic               # 50 wells, seed=42, Loving
    python -m app.seed.seed_synthetic --n 200       # bigger map test
    python -m app.seed.seed_synthetic --seed 99     # different roster

Routes the SyntheticEnverusClient through the same `sync_county` orchestrator
that the real Prism client uses — so this exercises the entire ingest path
(upserts, heel + wellstick recomputation, calday/prodday rates, watermarks).
"""

from __future__ import annotations

import argparse
import sys

from app.core.logging import configure_logging, get_logger
from app.seed.synthetic_client import SyntheticEnverusClient
from app.sync.orchestrator import sync_county


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic wells (no Enverus needed)")
    parser.add_argument("--n", type=int, default=50, help="well count")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for repeatability")
    parser.add_argument("--basin", default="Permian")
    parser.add_argument("--county", default="Loving")
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
    log.info(
        "seeding_synthetic",
        n=args.n,
        seed=args.seed,
        basin=args.basin,
        county=args.county,
    )

    client = SyntheticEnverusClient(
        n_wells=args.n, county=args.county, basin=args.basin, seed=args.seed
    )
    counts = sync_county(
        basin=args.basin,
        county=args.county,
        pull_production=not args.no_production,
        pull_surveys=not args.no_surveys,
        client=client,
    )
    log.info("synthetic_seed_complete", **counts)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
