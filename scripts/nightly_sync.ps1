# Nightly warehouse -> anduin sync.
#
# Registered in Windows Task Scheduler as "AnduinNightlySync" (daily,
# after the oilgas GitHub Actions nightly lands). Pulls well headers,
# production, and the Novi PDP forecast from engineering_db's curated
# views into the local app DB via sync_permian() - a full-scope upsert,
# safe to re-run any time.
#
# Requires Docker Desktop and the anduin compose stack to be running;
# when they are not (laptop asleep at trigger time, stack down), the
# run logs a SKIP and exits 0 so Task Scheduler doesn't flag failures.
# Log: logs\nightly_sync.log (gitignored).
#
# PowerShell 5.1-safe: no &&, no ternary, ASCII only.

$repo = "C:\Users\MichaelMast\Projects\permian_type_curve"
$logDir = Join-Path $repo "logs"
$log = Join-Path $logDir "nightly_sync.log"
New-Item -ItemType Directory -Force $logDir | Out-Null

function Write-SyncLog($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line -Encoding utf8
}

Set-Location $repo

$state = docker compose ps --format "{{.Service}} {{.State}}"
if ($LASTEXITCODE -ne 0) {
    Write-SyncLog "SKIP docker compose unavailable (Docker Desktop not running?)"
    exit 0
}
if (-not ($state | Select-String -Quiet "backend\s+running")) {
    Write-SyncLog "SKIP backend container not running"
    exit 0
}

Write-SyncLog "START sync_permian"
$out = docker compose exec -T backend python -c "from app.sync.orchestrator import sync_permian; import json; print(json.dumps(sync_permian()))"
if ($LASTEXITCODE -eq 0) {
    $counts = $out | Select-Object -Last 1
    Write-SyncLog ("DONE " + $counts)
    exit 0
}
else {
    Write-SyncLog ("FAIL exit=" + $LASTEXITCODE)
    exit 1
}
