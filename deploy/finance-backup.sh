#!/bin/sh
set -eu

monthly=""
if [ "$(date -u +%d)" = "01" ]; then
  monthly="--monthly"
fi

docker exec finance-finance-1 \
  python -m backend.finance_app.backup snapshot \
  --data-dir /data \
  --backup-dir /backups \
  ${monthly}
