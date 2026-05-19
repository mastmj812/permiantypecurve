"""One-off diagnostic: probe what filter values the Enverus `wells`
dataset actually accepts for Loving County, TX.

Each layer adds one filter so we can see exactly which one zeros out
the result. Run inside the backend container:

    docker compose exec backend python /app/scripts/probe_enverus_wells.py
"""

from __future__ import annotations

from app.config import settings
from enverus_developer_api import DeveloperAPIv3


def count_and_sample(it, cap: int = 5):
    n = 0
    sample = None
    for row in it:
        if sample is None:
            sample = row
        n += 1
        if n >= cap:
            break
    return n, sample


def main() -> None:
    sdk = DeveloperAPIv3(secret_key=settings.enverus_api_key_prism)

    print("--- 1. county=LOVING + deleteddate=null ---")
    c, s = count_and_sample(sdk.query("wells", county="LOVING", deleteddate="null"))
    print(f"  hits in first 5: {c}")
    if s:
        for key in (
            "ENVBasin", "ENVPlay", "ENVRegion", "Country",
            "StateProvince", "County", "API_UWI_14_Unformatted",
            "ENVOperator",
        ):
            print(f"  {key} = {s.get(key)!r}")
    else:
        print("  NO ROWS — county filter alone returns nothing.")

    print()
    print("--- 2. + ENVBasin=PERMIAN ---")
    c, _ = count_and_sample(sdk.query(
        "wells", county="LOVING", ENVBasin="PERMIAN", deleteddate="null",
    ))
    print(f"  hits in first 5: {c}")

    print()
    print("--- 3. + Country=US ---")
    c, _ = count_and_sample(sdk.query(
        "wells", county="LOVING", ENVBasin="PERMIAN",
        Country="US", deleteddate="null",
    ))
    print(f"  hits in first 5: {c}")

    print()
    print("--- 4. county only, no deleteddate filter ---")
    c, _ = count_and_sample(sdk.query("wells", county="LOVING"))
    print(f"  hits in first 5: {c}")


if __name__ == "__main__":
    main()
