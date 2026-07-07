# pg_dump the Permian Postgres into infra/backup/dumps/<timestamp>.sql.gz.
# Run from anywhere:  powershell -File infra\backup\backup.ps1
#
# Schedule with Task Scheduler for nightlies:
#   schtasks /create /tn "PermianBackup" /tr "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\MichaelMast\Projects\permian_type_curve\infra\backup\backup.ps1" /sc daily /st 02:00
#
# IMPLEMENTATION NOTE: the dump is written and gzip-verified INSIDE the
# container, then copied out with `docker cp`. Do NOT pipe pg_dump's
# stdout through PowerShell (`docker exec ... | gzip > file`): Windows
# PowerShell 5.1 re-encodes pipeline data as text, silently corrupting
# the gzip stream — the original version of this script produced dumps
# that could never have been restored.
[CmdletBinding()]
param(
    [int]$KeepDays = 14,
    [string]$Container = "permian-postgres",
    # Offsite mirror (OneDrive-synced folder). Each verified dump is copied
    # here on the SAME run that creates it, so a stolen laptop / dead SSD still
    # leaves a cloud copy. Set to "" to skip. Env override: BACKUP_OFFSITE_DIR.
    [string]$OffsiteDir = $(if ($env:BACKUP_OFFSITE_DIR) { $env:BACKUP_OFFSITE_DIR } else { "C:\Users\MichaelMast\Blue Ox Resources\Engineering - General\Backup\permian" })
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DumpsDir  = Join-Path $ScriptDir "dumps"
if (-not (Test-Path $DumpsDir)) { New-Item -ItemType Directory -Path $DumpsDir | Out-Null }

$Stamp    = Get-Date -Format "yyyyMMdd-HHmmss"
$FileName = "permian_$Stamp.sql.gz"
$OutFile  = Join-Path $DumpsDir $FileName
$TmpPath  = "/tmp/$FileName"

Write-Host "Dumping postgres (inside $Container) -> $OutFile"

# Dump + gzip + integrity-check entirely inside the container.
# `--clean --if-exists` makes the dump restore-safe over an existing DB.
docker exec $Container sh -c "pg_dump --clean --if-exists -U permian -d permian | gzip > $TmpPath && gunzip -t $TmpPath"
if ($LASTEXITCODE -ne 0) {
    Write-Error "pg_dump / gzip verification failed inside the container"
    exit 1
}

docker cp "${Container}:$TmpPath" $OutFile
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker cp failed: $TmpPath -> $OutFile"
    exit 1
}
docker exec $Container rm -f $TmpPath

if (-not (Test-Path $OutFile) -or (Get-Item $OutFile).Length -lt 1024) {
    Write-Error "Backup looks empty/failed: $OutFile"
    exit 1
}
$size_mb = (Get-Item $OutFile).Length / 1MB
Write-Host ("Wrote {0:N1} MB" -f $size_mb)

$Cutoff = (Get-Date).AddDays(-$KeepDays)

# --- Offsite mirror (OneDrive) ---
# Copy the just-verified dump to the OneDrive-synced folder on THIS run, so the
# offsite copy is never more than one backup behind the local one. A failure
# here is fatal (exit 1) on purpose: a silently-broken offsite sync is exactly
# the gap this closes, so it must surface via the task result / healthcheck.
if ($OffsiteDir -ne "") {
    if (-not (Test-Path $OffsiteDir)) { New-Item -ItemType Directory -Path $OffsiteDir -Force | Out-Null }
    $OffsiteFile = Join-Path $OffsiteDir $FileName
    Write-Host "Mirroring offsite -> $OffsiteFile"
    Copy-Item -Path $OutFile -Destination $OffsiteFile -Force
    # Size-match confirms the write into the synced folder completed. (OneDrive
    # then uploads asynchronously; verify the cloud copy out-of-band periodically
    # and don't trust a Files-On-Demand placeholder.)
    if (-not (Test-Path $OffsiteFile) -or (Get-Item $OffsiteFile).Length -ne (Get-Item $OutFile).Length) {
        Write-Error "Offsite mirror failed or size mismatch: $OffsiteFile"
        exit 1
    }
    Write-Host "Offsite mirror OK"
    # Prune offsite dumps older than $KeepDays too, so the cloud copy tracks local.
    Get-ChildItem $OffsiteDir -Filter "permian_*.sql.gz" |
        Where-Object { $_.LastWriteTime -lt $Cutoff } |
        ForEach-Object { Write-Host "Pruning offsite $($_.Name)"; Remove-Item $_.FullName }
} else {
    Write-Warning "OffsiteDir is empty - skipping offsite mirror (LOCAL DUMP ONLY)."
}

# Prune local dumps older than $KeepDays.
Get-ChildItem $DumpsDir -Filter "permian_*.sql.gz" |
    Where-Object { $_.LastWriteTime -lt $Cutoff } |
    ForEach-Object {
        Write-Host "Pruning $($_.Name)"
        Remove-Item $_.FullName
    }
