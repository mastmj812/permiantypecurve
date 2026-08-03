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
#   .\infra\basemap\convert_shapefiles.ps1
#   .\infra\basemap\convert_shapefiles.ps1 -BlocksShp my_blocks.shp -SectionsShp my_sections.shp
[CmdletBinding()]
param(
    [string]$BlocksShp = "blocks.shp",
    [string]$SectionsShp = "sections.shp",
    [string]$BasementFaultsShp = "faults_basement.shp",
    [string]$SnfFaultsShp = "faults_snf.shp",
    [string]$BlocksOut = "blocks_tx_nm.geojson",
    [string]$SectionsOut = "sections_tx_nm.geojson",
    [string]$BasementFaultsOut = "faults_basement.geojson",
    [string]$SnfFaultsOut = "faults_snf.geojson"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir   = Resolve-Path $ScriptDir
$Image     = "ghcr.io/osgeo/gdal:alpine-small-latest"

function Convert-One {
    # NOTE: `$Input` is a reserved automatic variable in PowerShell
    # (the pipeline-input enumerator). Using it as a param name silently
    # blanks the value — use $InFile / $OutFile instead.
    param(
        [string]$InFile,
        [string]$OutFile,
        [string]$Label
    )
    $InputPath  = Join-Path $DataDir $InFile
    $OutputPath = Join-Path $DataDir $OutFile

    if (-not (Test-Path $InputPath)) {
        Write-Warning "$Label source not found: $InputPath"
        Write-Warning "  Skipping $Label. Drop the .shp (and its .dbf/.prj/.shx siblings) here, then re-run."
        return
    }

    Write-Host "Converting $Label : $InFile -> $OutFile"
    # Run ogr2ogr inside the GDAL container; mount infra/basemap as /data.
    # `-overwrite` so re-runs replace the output cleanly.
    docker run --rm `
        -v "${DataDir}:/data" `
        $Image `
        ogr2ogr -f GeoJSON `
            -t_srs EPSG:4326 `
            -overwrite `
            "/data/$OutFile" `
            "/data/$InFile"

    if (-not (Test-Path $OutputPath)) {
        Write-Error "$Label conversion produced no output."
        return
    }

    # Print the property keys from the first feature so the user can
    # identify the block-number / section-number column for the map
    # label expression in MapView.tsx.
    #
    # Parsing in PowerShell directly avoids the quoting hell of trying
    # to embed a python one-liner inside `sh -c "..."` inside `docker
    # run` inside a PowerShell here-string. Files from these shapefiles
    # are typically a few MB — ConvertFrom-Json handles them fine.
    Write-Host ""
    Write-Host "  $Label first-feature property keys (paste these to update text-field if needed):"
    try {
        $geo   = Get-Content -Raw -Path $OutputPath | ConvertFrom-Json
        $props = $geo.features[0].properties.PSObject.Properties.Name
        Write-Host ("    {0}" -f ($props -join ", "))
    } catch {
        Write-Warning "    Could not parse property keys: $($_.Exception.Message)"
        Write-Warning "    Open $OutputPath manually and look at features[0].properties."
    }

    $size_mb = (Get-Item $OutputPath).Length / 1MB
    Write-Host ("  Wrote {0:N1} MB" -f $size_mb)
    Write-Host ""
}

Convert-One -InFile $BlocksShp         -OutFile $BlocksOut         -Label "Blocks"
Convert-One -InFile $SectionsShp       -OutFile $SectionsOut       -Label "Sections"
Convert-One -InFile $BasementFaultsShp -OutFile $BasementFaultsOut -Label "Basement faults"
Convert-One -InFile $SnfFaultsShp      -OutFile $SnfFaultsOut      -Label "SNF"

Write-Host "Done. Backend will now serve them at:"
Write-Host "  GET /api/basemap/blocks_tx_nm.geojson"
Write-Host "  GET /api/basemap/sections_tx_nm.geojson"
Write-Host "  GET /api/basemap/faults_basement.geojson"
Write-Host "  GET /api/basemap/faults_snf.geojson"
Write-Host ""
Write-Host "Toggle each overlay in the map toolbar to see it."
