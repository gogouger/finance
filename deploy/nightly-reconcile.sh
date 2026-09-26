#!/bin/sh
set -eu

docker exec finance-finance-1 python -c '
import json
import os
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:8080/api/internal/nightly-reconcile",
    data=b"{}",
    headers={
        "Content-Type": "application/json",
        "X-Internal-Key": os.environ["FINANCE_INTERNAL_KEY"],
    },
    method="POST",
)
with urllib.request.urlopen(request, timeout=300) as response:
    result = json.load(response)
if not isinstance(result.get("connections"), list):
    raise SystemExit("nightly reconciliation returned an invalid result")
'
