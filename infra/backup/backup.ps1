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
    [string]$Container = "permian-postgres"
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

# Prune dumps older than $KeepDays.
$Cutoff = (Get-Date).AddDays(-$KeepDays)
Get-ChildItem $DumpsDir -Filter "permian_*.sql.gz" |
    Where-Object { $_.LastWriteTime -lt $Cutoff } |
    ForEach-Object {
        Write-Host "Pruning $($_.Name)"
        Remove-Item $_.FullName
    }
