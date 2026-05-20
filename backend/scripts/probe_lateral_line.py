"""Probe the Enverus `wells` dataset for the `LateralLine` field.

Throwaway diagnostic — answers three questions:
  1. Does the field exist on the wells layer for our county scope?
  2. What format is the value (WKT? WKB hex? GeoJSON? lon/lat pairs?).
  3. What's the coverage — every well, or a fraction?

Run inside the backend container:
    docker compose exec backend python /app/scripts/probe_lateral_line.py
"""

from __future__ import annotations

from app.config import settings
from enverus_developer_api import DeveloperAPIv3


def main() -> None:
    sdk = DeveloperAPIv3(secret_key=settings.enverus_api_key_prism)

    it = sdk.query(
        "wells",
        county="LOVING",
        deleteddate="null",
    )
    rows: list[dict] = []
    for r in it:
        rows.append(r)
        if len(rows) >= 1000:
            break
    print(f"Pulled {len(rows)} Loving wells (no status filter).")

    have_field = sum(1 for r in rows if "LateralLine" in r)
    populated = sum(
        1 for r in rows if r.get("LateralLine") not in (None, "", [])
    )
    print(f"  rows with the 'LateralLine' key present: {have_field}")
    print(f"  rows where the value is non-empty:       {populated}")

    # Restrict to wells with a real lateral length recorded — i.e. drilled
    # & surveyed horizontals — and re-check populated rate. That's the
    # cohort our survey pipeline actually produces wellsticks for.
    with_lateral = [
        r for r in rows
        if r.get("LateralLength_FT") not in (None, "", 0)
    ]
    populated_lat = sum(
        1 for r in with_lateral if r.get("LateralLine") not in (None, "", [])
    )
    print(
        f"  of {len(with_lateral)} with LateralLength_FT set, "
        f"{populated_lat} ({100 * populated_lat / max(len(with_lateral), 1):.0f}%) have LateralLine."
    )

    sample = next(
        (r["LateralLine"] for r in rows if r.get("LateralLine")), None
    )
    if sample is None:
        print("  no populated sample to show.")
        return

    print()
    print("--- sample LateralLine value ---")
    print(f"  python type: {type(sample).__name__}")
    text = str(sample)
    print(f"  length: {len(text)} chars")
    print(f"  head:   {text[:200]}")
    if len(text) > 200:
        print(f"  tail:   {text[-100:]}")

    # Also dump a couple of adjacent columns so we can compare to what
    # our survey ingest produces and decide whether we trust this value.
    for key in ("API_UWI_14_Unformatted", "Latitude", "Longitude",
                "Latitude_BH", "Longitude_BH",
                "LateralLength_FT", "ENVWellboreStatus"):
        print(f"  {key} = {next(iter(rows[0].get(key, '') for _ in [0]))!r}")


if __name__ == "__main__":
    main()
