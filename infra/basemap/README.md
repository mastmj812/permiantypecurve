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

## PLSS overlay (optional)

The BLM PLSS sections / townships GeoJSON belongs at
`plss_tx_nm.geojson` in this directory. Sourced from
https://www.blm.gov/services/geospatial/GISData/cadastral .

## Block and section overlays (Permian abstracts)

Drop your block + section shapefiles into this directory and run the
converter. It uses `ogr2ogr` inside a tiny GDAL Docker image (no host
GDAL install needed) and reprojects to WGS84 for MapLibre.

```bash
# expects ./blocks.shp and ./sections.shp by default
.\infra\basemap\convert_shapefiles.ps1                # Windows
./infra/basemap/convert_shapefiles.sh                  # macOS / Linux

# or with custom paths:
./infra/basemap/convert_shapefiles.sh --blocks my_blocks.shp --sections my_sections.shp
```

The script prints the property keys of the first feature in each output.
If they don't include a recognized label key (`BLOCK_NO`, `BLOCK`,
`SECTION_NO`, `SECTION`, `SEC`), update the `text-field` expression in
`frontend/src/components/MapView.tsx` accordingly.

Blocks render at zoom ≥ 8; sections at zoom ≥ 11. Toggle each with the
checkboxes in the map's top toolbar.

## Fault overlays (BEG / Horne et al.)

Two line overlays, same converter, no zoom gate:

- `faults_basement.shp` → `faults_basement.geojson` — basement-rooted
  fault traces of the Delaware Basin + Central Basin Platform (Horne
  et al. 2022 V4, top-Ellenburger hanging-wall intersection). Source:
  Texas Data Repository, doi.org/10.18738/T8/UHOUX8.
- `faults_snf.shp` → `faults_snf.geojson` — shallow normal fault (SNF)
  traces (Horne 2022). Delivered as `Horne_2022_SNF_Traces.zip`.

Both ship in UTM zone 13N (WGS84 datum); the converter reprojects to
EPSG:4326. Toggle with "Bsmt faults" / "SNF" in the map toolbar.
