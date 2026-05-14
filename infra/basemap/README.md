# Basemap

Self-hosted Protomaps PMTiles for Texas + New Mexico. No third-party
service, no API keys.

## First-time setup

From the repo root:

```bash
# macOS / Linux / Git Bash
./infra/basemap/fetch.sh

# Windows PowerShell
.\infra\basemap\fetch.ps1
```

The script:
1. Downloads the `pmtiles` CLI (`go-pmtiles`) into `.bin/`.
2. Locates the most recent Protomaps daily planet build.
3. HTTP-range-extracts a Texas+NM bbox (no full-planet download).
4. Writes `permian.pmtiles` to this directory (~150–250 MB).

Re-run with `--force` (bash) or `-Force` (PowerShell) to refresh.

## PLSS overlay (optional, deferred)

The BLM PLSS sections / townships GeoJSON belongs at
`plss_tx_nm.geojson` in this directory. Sourced from
https://www.blm.gov/services/geospatial/GISData/cadastral . Wired into the
map page in step 3.
