#!/usr/bin/env bash
# Convert block + section + fault shapefiles to GeoJSON for the map overlays.
#
# Uses the osgeo/gdal:alpine-small Docker image so you don't have to
# install GDAL on the host. Reprojects to EPSG:4326 (WGS84) for MapLibre.
#
# Drop the shapefile bundles (.shp + .dbf + .prj + .shx + .cpg if present)
# into infra/basemap/ first. The script expects them named blocks.shp,
# sections.shp, faults_basement.shp, and faults_snf.shp by default; pass
# arguments to override. Missing inputs are skipped with a warning, so
# it's fine to run with only a subset present.
#
# Fault sources (BEG / Horne et al., both delivered in UTM 13N):
#   faults_basement.shp — Horne et al. 2022 DB-CBP BSMT V4 basement-rooted
#     fault traces (top-Ellenburger HW intersection), doi.org/10.18738/T8/UHOUX8
#   faults_snf.shp — Horne 2022 shallow normal fault (SNF) traces
#
# Usage:
#   ./infra/basemap/convert_shapefiles.sh
#   ./infra/basemap/convert_shapefiles.sh --blocks my_blocks.shp --sections my_sections.shp
set -euo pipefail

BLOCKS_SHP="blocks.shp"
SECTIONS_SHP="sections.shp"
BASEMENT_FAULTS_SHP="faults_basement.shp"
SNF_FAULTS_SHP="faults_snf.shp"
BLOCKS_OUT="blocks_tx_nm.geojson"
SECTIONS_OUT="sections_tx_nm.geojson"
BASEMENT_FAULTS_OUT="faults_basement.geojson"
SNF_FAULTS_OUT="faults_snf.geojson"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --blocks)   BLOCKS_SHP="$2";   shift 2 ;;
    --sections) SECTIONS_SHP="$2"; shift 2 ;;
    --basement-faults) BASEMENT_FAULTS_SHP="$2"; shift 2 ;;
    --snf-faults)      SNF_FAULTS_SHP="$2";      shift 2 ;;
    --blocks-out)   BLOCKS_OUT="$2";   shift 2 ;;
    --sections-out) SECTIONS_OUT="$2"; shift 2 ;;
    --basement-faults-out) BASEMENT_FAULTS_OUT="$2"; shift 2 ;;
    --snf-faults-out)      SNF_FAULTS_OUT="$2";      shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
IMAGE="ghcr.io/osgeo/gdal:alpine-small-latest"

convert_one() {
  local input="$1"
  local output="$2"
  local label="$3"

  if [[ ! -f "${SCRIPT_DIR}/${input}" ]]; then
    echo "WARN: ${label} source not found at ${SCRIPT_DIR}/${input}"
    echo "      Skipping ${label}. Drop the .shp (and its siblings) here and re-run."
    return
  fi

  echo "Converting ${label}: ${input} -> ${output}"
  docker run --rm \
      -v "${SCRIPT_DIR}:/data" \
      "${IMAGE}" \
      ogr2ogr -f GeoJSON \
          -t_srs EPSG:4326 \
          -overwrite \
          "/data/${output}" \
          "/data/${input}"

  if [[ ! -f "${SCRIPT_DIR}/${output}" ]]; then
    echo "ERROR: ${label} conversion produced no output." >&2
    return
  fi

  # Print property keys of the first feature so the user can identify
  # the block-number / section-number column.
  echo ""
  echo "  ${label} first-feature property keys (paste these if MapView's text-field needs updating):"
  docker run --rm \
      -v "${SCRIPT_DIR}:/data" \
      "${IMAGE}" \
      sh -c "head -c 200000 /data/${output} | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); print(\"   \", list(d[\"features\"][0][\"properties\"].keys()))'" 2>/dev/null || true

  local size_mb
  size_mb="$(du -m "${SCRIPT_DIR}/${output}" | cut -f1)"
  echo "  Wrote ${size_mb} MB"
  echo ""
}

convert_one "${BLOCKS_SHP}"          "${BLOCKS_OUT}"          "Blocks"
convert_one "${SECTIONS_SHP}"        "${SECTIONS_OUT}"        "Sections"
convert_one "${BASEMENT_FAULTS_SHP}" "${BASEMENT_FAULTS_OUT}" "Basement faults"
convert_one "${SNF_FAULTS_SHP}"      "${SNF_FAULTS_OUT}"      "SNF"

echo "Done. Backend serves at:"
echo "  GET /api/basemap/blocks_tx_nm.geojson"
echo "  GET /api/basemap/sections_tx_nm.geojson"
echo "  GET /api/basemap/faults_basement.geojson"
echo "  GET /api/basemap/faults_snf.geojson"
echo ""
echo "Toggle each overlay in the map toolbar to see it."
