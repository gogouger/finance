import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from backend.finance_app.backup import (
    create_encrypted_snapshot,
    prune_snapshots,
    verify_isolated_restore,
)
from backend.finance_app.plaid_provider import (
    HttpPlaidProvider,
    ReleaseGatedPlaidProvider,
    create_plaid_provider,
)
from backend.finance_app.release_gates import REQUIRED_RELEASE_GATES


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE private_values (value TEXT NOT NULL)")
        connection.execute("INSERT INTO private_values VALUES (?)", ("sensitive amount 12345",))


def test_encrypted_snapshots_are_integrity_checked_restorable_and_retained(tmp_path: Path):
    data_dir = tmp_path / "data"
    backup_dir = tmp_path / "backups"
    data_dir.mkdir()
    _database(data_dir / "finance.db")
    key = Fernet.generate_key().decode()
    start = datetime(2025, 1, 1, 3, 0, tzinfo=UTC)

    for index in range(35):
        create_encrypted_snapshot(
            data_dir,
            backup_dir,
            key,
            now=start + timedelta(days=index),
        )
    for index in range(14):
        year = 2024 + index // 12
        month = index % 12 + 1
        create_encrypted_snapshot(
            data_dir,
            backup_dir,
            key,
            now=datetime(year, month, 1, 3, 0, tzinfo=UTC),
            monthly=True,
        )
    prune_snapshots(backup_dir, daily=30, monthly=12)

    daily = sorted(backup_dir.glob("finance-daily-*.tar.fernet"))
    monthly = sorted(backup_dir.glob("finance-monthly-*.tar.fernet"))
    assert len(daily) == 30
    assert len(monthly) == 12
    assert b"sensitive amount 12345" not in daily[-1].read_bytes()
    assert b"SQLite format 3" not in daily[-1].read_bytes()

    restored = verify_isolated_restore(daily[-1], key, live_data_dir=data_dir)
    assert restored["archive_integrity"] == "verified"
    assert restored["sqlite_integrity"] == "ok"
    assert restored["isolated_from_live_data"] is True
    assert restored["tables"] >= 1

    corrupted = tmp_path / "corrupted.tar.fernet"
    payload = bytearray(daily[-1].read_bytes())
    payload[len(payload) // 2] ^= 1
    corrupted.write_bytes(payload)
    with pytest.raises(InvalidToken):
        verify_isolated_restore(corrupted, key, live_data_dir=data_dir)


def test_production_plaid_is_fail_closed_until_every_release_gate_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    evidence = tmp_path / "release-gates.json"
    monkeypatch.setenv("PLAID_CLIENT_ID", "configured-client")
    monkeypatch.setenv("PLAID_SECRET", "configured-secret")
    monkeypatch.setenv("PLAID_ENVIRONMENT", "production")
    monkeypatch.setenv("FINANCE_RELEASE_GATES_FILE", str(evidence))

    evidence.write_text(
        json.dumps(
            {
                "status": "blocked",
                "gates": {gate: True for gate in REQUIRED_RELEASE_GATES},
            }
        )
    )
    assert isinstance(create_plaid_provider(), ReleaseGatedPlaidProvider)

    gates = {gate: True for gate in REQUIRED_RELEASE_GATES}
    gates["zap_staging"] = False
    evidence.write_text(json.dumps({"status": "approved", "gates": gates}))
    assert isinstance(create_plaid_provider(), ReleaseGatedPlaidProvider)

    evidence.write_text(json.dumps({"status": "approved", "gates": {gate: True for gate in REQUIRED_RELEASE_GATES}}))
    assert isinstance(create_plaid_provider(), HttpPlaidProvider)


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_operational_status_is_private_and_contains_only_redacted_signals(tmp_path: Path):
    data_dir = tmp_path / "data"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "finance-daily-2026-09-25T030000Z.tar.fernet").write_bytes(b"encrypted")
    port = _unused_port()
    environment = os.environ.copy()
    environment.update(
        {
            "FINANCE_DATA_DIR": str(data_dir),
            "FINANCE_BACKUP_DIR": str(backup_dir),
            "FINANCE_ENCRYPTION_KEY": urlsafe_b64encode(b"o" * 32).decode(),
            "FINANCE_INTERNAL_KEY": "operations-internal-key",
            "PLAID_MODE": "fake",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.finance_app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=0.2):
                    break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.05)
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(f"{base_url}/api/internal/operations/status")
        assert denied.value.code == 401

        request = urllib.request.Request(
            f"{base_url}/api/internal/operations/status",
            headers={"X-Internal-Key": "operations-internal-key"},
        )
        with urllib.request.urlopen(request) as response:
            status = json.load(response)
        assert set(status) == {
            "service",
            "status",
            "sync",
            "review_queue_depth",
            "backup",
            "disk",
            "release",
        }
        assert set(status["sync"]) == {"connections_by_status", "stale_feed_count"}
        assert set(status["backup"]) == {"status", "latest_age_seconds"}
        assert set(status["disk"]) == {"used_percent", "free_bytes"}
        serialized = json.dumps(status).lower()
        for forbidden in ("amount", "balance", "merchant", "access_token", "owner", "institution"):
            assert forbidden not in serialized
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_release_artifacts_are_fail_closed_and_accessible():
    root = Path(__file__).parents[1]
    compose = (root / "deploy/compose.prod.yaml").read_text()
    assert "mem_limit: 256m" in compose
    assert "cpus: 0.35" in compose
    assert "pids_limit: 128" in compose
    assert 'max-size: "10m"' in compose
    assert 'max-file: "3"' in compose
    assert "name: finance_edge" in compose
    assert "ggouger_default" not in compose
    assert "/opt/data/finance/backups:/backups" in compose
    assert "/opt/data/finance/release-gates.json:/run/finance/release-gates.json:ro" in compose

    backup_cron = (root / "deploy/finance-backup.cron").read_text()
    restore_cron = (root / "deploy/finance-restore-test.cron").read_text()
    assert "finance-backup.sh" in backup_cron
    assert "finance-restore-test.sh" in restore_cron

    gates = json.loads((root / "deploy/release-gates.json").read_text())
    assert gates["status"] == "blocked"
    assert set(gates["gates"]) == set(REQUIRED_RELEASE_GATES)

    source = (root / "frontend/src/main.tsx").read_text()
    styles = (root / "frontend/src/styles.css").read_text()
    assert 'href="#main-content"' in source
    assert source.count('id="main-content"') == 1
    assert 'role="img"' in source
    assert "Year-by-year accessible results" in source
    assert ":focus-visible" in styles
    assert "prefers-reduced-motion: reduce" in styles

    zap = root / "deploy/zap-staging.sh"
    assert zap.is_file()
    denied = subprocess.run(
        [str(zap), "https://finance.gordongouger.com"],
        capture_output=True,
        text=True,
    )
    assert denied.returncode != 0
    assert "local staging" in denied.stderr.lower()

    evidence = (root / "deploy/RELEASE-EVIDENCE.md").read_text()
    for phrase in (
        "30 daily",
        "12 monthly",
        "OWASP",
        "Cloudflare",
        "ZAP",
        "production linking remains disabled",
    ):
        assert phrase in evidence

    example_environment = (root / ".env.example").read_text()
    assert "FINANCE_INTERNAL_KEY=" in example_environment
    assert "Do not reuse" in example_environment
