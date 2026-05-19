# Postgres backups

```powershell
# Manual backup (Windows)
.\infra\backup\backup.ps1            # last 14 days kept; older dumps pruned

# Manual backup (bash)
./infra/backup/backup.sh
```

Dumps land in `infra/backup/dumps/permian_YYYYMMDD-HHMMSS.sql.gz`. The
`dumps/` directory is gitignored.

## Restore

```powershell
# Stop the app so nothing's writing.
docker compose stop backend

# Drop and recreate the database (destroys current data).
docker compose exec postgres psql -U permian -d postgres -c "DROP DATABASE permian"
docker compose exec postgres psql -U permian -d postgres -c "CREATE DATABASE permian OWNER permian"

# Pipe the gzipped dump into psql.
gzip -dc infra/backup/dumps/permian_20260514-021500.sql.gz | `
    docker compose exec -T postgres psql -U permian -d permian

# Bring the backend back up.
docker compose start backend
```

The dump uses `--clean --if-exists` so you can also restore over an existing
database without dropping it first — useful for partial recovery testing.

## Schedule nightlies

### Windows (Task Scheduler)

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -File $PWD\infra\backup\backup.ps1"
$trigger = New-ScheduledTaskTrigger -Daily -At 2am
Register-ScheduledTask -TaskName "PermianBackup" -Action $action -Trigger $trigger
```

### Linux/macOS (cron)

```cron
0 2 * * *  cd /path/to/permian_type_curve && ./infra/backup/backup.sh
```
