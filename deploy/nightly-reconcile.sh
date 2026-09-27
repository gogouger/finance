#!/usr/bin/env bash
set -euo pipefail

# Webhooks remain the fast path. This bounded daily reconciliation repairs
# missed provider events without exposing the internal key on the host CLI.
# This job runs as root from /etc/cron.d. Keep the lock out of the sticky /tmp
# directory: Linux protected_regular can reject a root open when an earlier
# manual run created the file as another user.
exec 9>/run/lock/finance-nightly-reconcile.lock
flock -n 9 || exit 0

docker exec -i finance-finance-1 python - <<'PY'
import json
import os
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:8080/api/internal/nightly-reconcile",
    headers={"X-Internal-Key": os.environ["FINANCE_INTERNAL_KEY"]},
    method="POST",
)
with urllib.request.urlopen(request, timeout=300) as response:
    result = json.load(response)
print(
    "finance reconciliation complete: "
    f"connections={len(result.get('connections', []))} "
    f"asset_valuations={sum(len(items) for items in result.get('asset_valuations', {}).values())} "
    f"expired_raw_replies_purged={result.get('raw_email_replies_purged', 0)}"
)
PY
