import argparse
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import struct
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet


FORMAT_HEADER = b"FINANCE-BACKUP-V1\n"
CHUNK_SIZE = 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _encrypt_file(source: Path, destination: Path, key: str) -> None:
    cipher = Fernet(key.encode())
    with source.open("rb") as plain, destination.open("wb") as encrypted:
        encrypted.write(FORMAT_HEADER)
        while chunk := plain.read(CHUNK_SIZE):
            token = cipher.encrypt(chunk)
            encrypted.write(struct.pack(">Q", len(token)))
            encrypted.write(token)


def _decrypt_file(source: Path, destination: Path, key: str) -> None:
    cipher = Fernet(key.encode())
    with source.open("rb") as encrypted, destination.open("wb") as plain:
        if encrypted.read(len(FORMAT_HEADER)) != FORMAT_HEADER:
            raise ValueError("unsupported encrypted backup format")
        while length_bytes := encrypted.read(8):
            if len(length_bytes) != 8:
                raise ValueError("truncated encrypted backup record")
            length = struct.unpack(">Q", length_bytes)[0]
            token = encrypted.read(length)
            if len(token) != length:
                raise ValueError("truncated encrypted backup payload")
            plain.write(cipher.decrypt(token))


def create_encrypted_snapshot(
    data_dir: Path,
    backup_dir: Path,
    key: str,
    *,
    now: datetime | None = None,
    monthly: bool = False,
) -> dict:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    database_path = data_dir / "finance.db"
    if not database_path.is_file():
        raise FileNotFoundError("finance database does not exist")
    stamp = timestamp.strftime("%Y-%m-%dT%H%M%SZ")
    daily_path = backup_dir / f"finance-daily-{stamp}.tar.fernet"
    with tempfile.TemporaryDirectory(prefix=".backup-work-", dir=data_dir) as work:
        work_dir = Path(work)
        snapshot_db = work_dir / "finance.db"
        with sqlite3.connect(database_path) as source, sqlite3.connect(snapshot_db) as target:
            source.backup(target)
        manifest = {
            "format": "finance-encrypted-snapshot",
            "version": 1,
            "created_at": timestamp.isoformat(),
            "files": {"finance.db": {"sha256": _sha256(snapshot_db)}},
        }
        manifest_path = work_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True))
        archive_path = work_dir / "snapshot.tar"
        with tarfile.open(archive_path, "w") as archive:
            archive.add(snapshot_db, arcname="finance.db")
            archive.add(manifest_path, arcname="manifest.json")
        temporary_output = backup_dir / f".{daily_path.name}.{uuid4().hex}.tmp"
        _encrypt_file(archive_path, temporary_output, key)
        temporary_output.replace(daily_path)
    paths = [daily_path]
    if monthly:
        monthly_path = backup_dir / f"finance-monthly-{timestamp.strftime('%Y-%m')}.tar.fernet"
        temporary_monthly = backup_dir / f".{monthly_path.name}.{uuid4().hex}.tmp"
        shutil.copy2(daily_path, temporary_monthly)
        temporary_monthly.replace(monthly_path)
        paths.append(monthly_path)
    return {"created": [str(path) for path in paths], "manifest": manifest}


def prune_snapshots(backup_dir: Path, *, daily: int = 30, monthly: int = 12) -> dict:
    removed: list[str] = []
    for pattern, keep in (
        ("finance-daily-*.tar.fernet", daily),
        ("finance-monthly-*.tar.fernet", monthly),
    ):
        snapshots = sorted(backup_dir.glob(pattern), reverse=True)
        for path in snapshots[keep:]:
            path.unlink()
            removed.append(path.name)
    return {"removed": removed, "daily_retained": daily, "monthly_retained": monthly}


def verify_isolated_restore(
    snapshot: Path, key: str, *, live_data_dir: Path
) -> dict:
    with tempfile.TemporaryDirectory(prefix="finance-restore-test-") as restore:
        restore_dir = Path(restore).resolve()
        live_dir = live_data_dir.resolve()
        isolated = live_dir not in (restore_dir, *restore_dir.parents) and restore_dir not in live_dir.parents
        archive_path = restore_dir / "snapshot.tar"
        _decrypt_file(snapshot, archive_path, key)
        with tarfile.open(archive_path, "r") as archive:
            names = set(archive.getnames())
            if names != {"finance.db", "manifest.json"}:
                raise ValueError("backup archive contains unexpected files")
            archive.extractall(restore_dir, filter="data")
        manifest = json.loads((restore_dir / "manifest.json").read_text())
        database_path = restore_dir / "finance.db"
        if not hmac_compare(
            _sha256(database_path), manifest["files"]["finance.db"]["sha256"]
        ):
            raise ValueError("backup manifest checksum mismatch")
        with sqlite3.connect(database_path) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            tables = connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
            ).fetchone()[0]
        if integrity != "ok":
            raise ValueError("restored SQLite integrity check failed")
        return {
            "archive_integrity": "verified",
            "sqlite_integrity": integrity,
            "isolated_from_live_data": isolated,
            "tables": tables,
            "snapshot": snapshot.name,
        }


def hmac_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or verify Finance backups")
    parser.add_argument("command", choices=("snapshot", "restore-test"))
    parser.add_argument("--data-dir", type=Path, default=Path("/data"))
    parser.add_argument("--backup-dir", type=Path, default=Path("/backups"))
    parser.add_argument("--key")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--monthly", action="store_true")
    arguments = parser.parse_args()
    key = arguments.key or os.environ.get("FINANCE_BACKUP_KEY")
    if not key:
        parser.error("FINANCE_BACKUP_KEY or --key is required")
    if arguments.command == "snapshot":
        result = create_encrypted_snapshot(
            arguments.data_dir,
            arguments.backup_dir,
            key,
            monthly=arguments.monthly,
        )
        result["retention"] = prune_snapshots(arguments.backup_dir)
    else:
        if arguments.snapshot is None:
            parser.error("restore-test requires --snapshot")
        result = verify_isolated_restore(
            arguments.snapshot, key, live_data_dir=arguments.data_dir
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
