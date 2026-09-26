#!/bin/sh
set -eu

target="${1:-http://finance-staging:8080}"
mode="${2:-baseline}"
report_dir="${ZAP_REPORT_DIR:-$(pwd)/zap-report}"

case "$target" in
  http://finance-staging:*|http://127.0.0.1:*|http://localhost:*|http://host.docker.internal:*) ;;
  *)
    echo "ZAP is restricted to an explicitly named local staging target." >&2
    exit 64
    ;;
esac

case "$mode" in
  baseline) scanner="zap-baseline.py" ;;
  full) scanner="zap-full-scan.py" ;;
  *)
    echo "Mode must be baseline or full." >&2
    exit 64
    ;;
esac

mkdir -p "$report_dir"
docker run --rm \
  --network "${ZAP_DOCKER_NETWORK:-ggouger_default}" \
  --volume "$report_dir:/zap/wrk:rw" \
  ghcr.io/zaproxy/zaproxy:stable \
  "$scanner" -t "$target" -J report.json -I

python3 - "$report_dir/report.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    report = json.load(source)
high = [
    alert
    for site in report.get("site", [])
    for alert in site.get("alerts", [])
    if str(alert.get("riskdesc", "")).lower().startswith("high")
]
if high:
    print(f"ZAP found {len(high)} unresolved high-risk alerts", file=sys.stderr)
    raise SystemExit(1)
print("ZAP staging gate: no high-risk alerts")
PY
