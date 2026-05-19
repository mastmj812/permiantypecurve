# pg_dump the Permian Postgres into infra/backup/dumps/<timestamp>.sql.gz.
# Run from the project root:  .\infra\backup\backup.ps1
#
# Schedule with Task Scheduler if you want nightlies:
#   schtasks /create /tn "PermianBackup" /tr "powershell -File C:\Users\...\backup.ps1" `
#       /sc daily /st 02:00
[CmdletBinding()]
param(
    [int]$KeepDays = 14
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DumpsDir  = Join-Path $ScriptDir "dumps"
if (-not (Test-Path $DumpsDir)) { New-Item -ItemType Directory -Path $DumpsDir | Out-Null }

$Stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$OutFile = Join-Path $DumpsDir "permian_$Stamp.sql.gz"

Write-Host "Dumping postgres -> $OutFile"

# Stream pg_dump's stdout through gzip inside the container; pipe to a host file.
# `--clean --if-exists` makes the dump restore-safe over an existing DB.
docker compose exec -T postgres `
    pg_dump --clean --if-exists -U permian -d permian | `
    gzip > $OutFile

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
