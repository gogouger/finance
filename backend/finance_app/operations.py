import hmac
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

from .release_gates import release_gate_status


router = APIRouter()


def _require_internal(value: str | None) -> None:
    expected = os.environ.get("FINANCE_INTERNAL_KEY")
    if not expected or value is None or not hmac.compare_digest(expected, value):
        raise HTTPException(status_code=401, detail="invalid internal credential")


def _backup_status() -> dict:
    backup_dir = Path(os.environ.get("FINANCE_BACKUP_DIR", "/backups"))
    snapshots = list(backup_dir.glob("finance-*.tar.fernet")) if backup_dir.exists() else []
    if not snapshots:
        return {"status": "missing", "latest_age_seconds": None}
    latest = max(path.stat().st_mtime for path in snapshots)
    age = max(0, int(datetime.now(UTC).timestamp() - latest))
    return {"status": "current" if age <= 36 * 60 * 60 else "stale", "latest_age_seconds": age}


def _stale_feeds(request: Request, connections: list[dict]) -> int:
    stale = 0
    now = datetime.now(UTC)
    for connection in connections:
        freshness = request.app.state.storage.get_sync_state(connection["id"]).get(
            "freshness", {}
        )
        timestamps = []
        for value in freshness.values():
            try:
                timestamps.append(datetime.fromisoformat(value))
            except (TypeError, ValueError):
                continue
        if not timestamps or (now - min(timestamps)).total_seconds() > 36 * 60 * 60:
            stale += 1
    return stale


@router.get("/api/internal/operations/status")
def operational_status(
    request: Request, x_internal_key: str | None = Header(default=None)
) -> dict:
    _require_internal(x_internal_key)
    connections = request.app.state.storage.list_all_connections()
    by_status: dict[str, int] = {}
    for connection in connections:
        status = connection.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
    owners = {connection["owner"] for connection in connections}
    review_queue_depth = sum(
        item.get("status") == "proposed"
        for owner in owners
        for item in request.app.state.storage.list_classification_suggestions(owner)
    )
    data_dir = Path(os.environ.get("FINANCE_DATA_DIR", "/data"))
    usage = shutil.disk_usage(data_dir)
    release = release_gate_status()
    return {
        "service": "finance",
        "status": "ok",
        "sync": {
            "connections_by_status": by_status,
            "stale_feed_count": _stale_feeds(request, connections),
        },
        "review_queue_depth": review_queue_depth,
        "backup": _backup_status(),
        "disk": {
            "used_percent": round(100 * usage.used / usage.total, 1),
            "free_bytes": usage.free,
        },
        "release": {
            "status": release["status"],
            "production_plaid_linking": release["production_plaid_linking"],
        },
    }
