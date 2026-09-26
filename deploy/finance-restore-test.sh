#!/bin/sh
set -eu

docker exec finance-finance-1 python -c '
import json
import os
from pathlib import Path
from backend.finance_app.backup import verify_isolated_restore

backups = sorted(Path("/backups").glob("finance-monthly-*.tar.fernet"))
if not backups:
    backups = sorted(Path("/backups").glob("finance-daily-*.tar.fernet"))
if not backups:
    raise SystemExit("no encrypted Finance snapshot is available")
result = verify_isolated_restore(
    backups[-1], os.environ["FINANCE_BACKUP_KEY"], live_data_dir=Path("/data")
)
if result["archive_integrity"] != "verified" or result["sqlite_integrity"] != "ok":
    raise SystemExit("isolated restore verification failed")
print(json.dumps(result, sort_keys=True))
'
