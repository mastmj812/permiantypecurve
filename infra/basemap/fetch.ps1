# Windows-native version of fetch.sh. Same behavior: pull the pmtiles CLI
# and HTTP-range-extract a Texas+NM slice from the Protomaps daily build.
# Run from a PowerShell prompt:  .\infra\basemap\fetch.ps1
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutFile   = Join-Path $ScriptDir "permian.pmtiles"
$BinDir    = Join-Path $ScriptDir ".bin"
$PmtilesExe = Join-Path $BinDir "pmtiles.exe"

# Texas + New Mexico bbox (W,S,E,N)
$BboxW = -109.10
$BboxS =   25.80
$BboxE =  -93.50
$BboxN =   37.05
$MaxZoom = 12

function Install-Pmtiles {
    if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Path $BinDir | Out-Null }

    # Pick arch
    $arch = if ([Environment]::Is64BitOperatingSystem) {
        if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x86_64" }
    } else { "x86_64" }

    # Look up the latest go-pmtiles release dynamically and pick the matching
    # Windows asset. Avoids stale-version drift and asset-naming guesswork.
    Write-Host "Looking up latest go-pmtiles release..."
    $release = Invoke-RestMethod "https://api.github.com/repos/protomaps/go-pmtiles/releases/latest"
    $asset = $release.assets | Where-Object {
        $_.name -like "*Windows*$arch*.zip"
    } | Select-Object -First 1
    if (-not $asset) {
        throw "No Windows $arch asset in go-pmtiles release $($release.tag_name)"
    }

    $zip = Join-Path $BinDir $asset.name
    Write-Host "Downloading $($asset.name)"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $BinDir -Force
    Remove-Item $zip
}

function Find-LatestBuild {
    for ($i = 1; $i -le 7; $i++) {
        $d = (Get-Date).ToUniversalTime().AddDays(-$i).ToString("yyyyMMdd")
        $url = "https://build.protomaps.com/$d.pmtiles"
        try {
            $resp = Invoke-WebRequest -Uri $url -Method Head -UseBasicParsing -ErrorAction Stop
            if ($resp.StatusCode -eq 200) { return $url }
        } catch { continue }
    }
    throw "Could not locate a recent Protomaps daily build"
}

if ((Test-Path $OutFile) -and (-not $Force)) {
    Write-Host "PMTiles file already present at $OutFile  -- pass -Force to re-fetch."
    exit 0
}

if (-not (Test-Path $PmtilesExe)) { Install-Pmtiles }

Write-Host "Resolving latest Protomaps daily build..."
$SourceUrl = Find-LatestBuild
Write-Host "Source: $SourceUrl"
Write-Host "BBox:   $BboxW,$BboxS,$BboxE,$BboxN  (maxzoom $MaxZoom)"
Write-Host "Output: $OutFile"
Write-Host ""

& $PmtilesExe extract $SourceUrl $OutFile `
    --bbox="$BboxW,$BboxS,$BboxE,$BboxN" `
    --maxzoom=$MaxZoom

if ($LASTEXITCODE -ne 0) { throw "pmtiles extract failed (exit $LASTEXITCODE)" }

$size = (Get-Item $OutFile).Length / 1MB
Write-Host ""
Write-Host ("Done. Wrote {0:N1} MB to {1}" -f $size, $OutFile)
