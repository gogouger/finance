import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet


class EncryptedStorage:
    def __init__(self, data_dir: Path, encryption_key: str):
        self._data_dir = data_dir
        self._database_path = data_dir / "finance.db"
        self._cipher = Fernet(encryption_key.encode())
        self._index_key = b""
        self.installation_fingerprint = ""

    def initialize(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._data_dir, 0o700)

        with sqlite3.connect(self._database_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    encrypted_value BLOB NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT encrypted_value FROM app_metadata WHERE key = ?",
                ("installation_secret",),
            ).fetchone()
            if row is None:
                secret = secrets.token_bytes(32)
                connection.execute(
                    "INSERT INTO app_metadata (key, encrypted_value) VALUES (?, ?)",
                    ("installation_secret", self._cipher.encrypt(secret)),
                )
            else:
                secret = self._cipher.decrypt(row[0])
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scenarios (
                    id TEXT PRIMARY KEY,
                    owner_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scenario_shares (
                    token_index TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    encrypted_payload BLOB NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    FOREIGN KEY (scenario_id) REFERENCES scenarios(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS connections (
                    id TEXT PRIMARY KEY,
                    owner_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    owner_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    occurred_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS financial_records (
                    kind TEXT NOT NULL,
                    connection_id TEXT NOT NULL,
                    source_index TEXT NOT NULL,
                    owner_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    removed INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (kind, connection_id, source_index)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_states (
                    connection_id TEXT PRIMARY KEY,
                    encrypted_record BLOB NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transaction_adjustments (
                    owner_index TEXT NOT NULL,
                    transaction_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (owner_index, transaction_index)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_record_versions (
                    version_index TEXT PRIMARY KEY,
                    owner_index TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    observed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS webhook_receipts (fingerprint TEXT PRIMARY KEY, received_at TEXT NOT NULL)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transaction_classifications (
                    owner_index TEXT NOT NULL,
                    transaction_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (owner_index, transaction_index)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS classification_rules (
                    id TEXT PRIMARY KEY,
                    owner_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS classification_suggestions (
                    id TEXT PRIMARY KEY,
                    owner_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recurring_obligations (
                    id TEXT NOT NULL,
                    owner_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (id, owner_index)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_review_tokens (
                    token_index TEXT PRIMARY KEY,
                    owner_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    expires_at TEXT NOT NULL,
                    reply_received_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_replies (
                    id TEXT PRIMARY KEY,
                    owner_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    received_at TEXT NOT NULL,
                    raw_expires_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS erasure_intents (
                    id TEXT PRIMARY KEY,
                    owner_index TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_grants (
                    id TEXT PRIMARY KEY,
                    owner_index TEXT NOT NULL,
                    encrypted_record BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

        os.chmod(self._database_path, 0o600)
        self._index_key = secret
        self.installation_fingerprint = hashlib.sha256(secret).hexdigest()[:12]

    def _index(self, value: str) -> str:
        return hmac.new(self._index_key, value.encode(), hashlib.sha256).hexdigest()

    def _encrypt(self, value: dict[str, Any]) -> bytes:
        return self._cipher.encrypt(
            json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        )

    def _decrypt(self, value: bytes) -> dict[str, Any]:
        return json.loads(self._cipher.decrypt(value))

    def save_scenario(
        self,
        owner: str,
        record: dict[str, Any],
        *,
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        stored = {**record, "id": scenario_id or str(uuid4())}
        created_at = datetime.now(UTC).isoformat()
        stored["created_at"] = created_at
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO scenarios VALUES (?, ?, ?, ?)",
                (
                    stored["id"],
                    self._index(owner),
                    self._encrypt(stored),
                    created_at,
                ),
            )
        return stored

    def get_scenario(self, owner: str, scenario_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT encrypted_record FROM scenarios WHERE id = ? AND owner_index = ?",
                (scenario_id, self._index(owner)),
            ).fetchone()
        return None if row is None else self._decrypt(row[0])

    def list_scenarios(self, owner: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT encrypted_record FROM scenarios WHERE owner_index = ? ORDER BY created_at",
                (self._index(owner),),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def create_share(
        self,
        scenario_id: str,
        payload: dict[str, Any],
        expires_at: datetime,
    ) -> str:
        token = secrets.token_urlsafe(32)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO scenario_shares VALUES (?, ?, ?, ?, NULL)",
                (
                    self._index(token),
                    scenario_id,
                    self._encrypt(payload),
                    expires_at.isoformat(),
                ),
            )
        return token

    def get_share(self, token: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT encrypted_payload, expires_at, revoked_at FROM scenario_shares WHERE token_index = ?",
                (self._index(token),),
            ).fetchone()
        if row is None or row[2] is not None:
            return None
        if datetime.fromisoformat(row[1]) <= datetime.now(UTC):
            return None
        return self._decrypt(row[0])

    def revoke_share(self, owner: str, token: str) -> bool:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """
                SELECT s.owner_index
                FROM scenario_shares sh
                JOIN scenarios s ON s.id = sh.scenario_id
                WHERE sh.token_index = ?
                """,
                (self._index(token),),
            ).fetchone()
            if row is None or not hmac.compare_digest(row[0], self._index(owner)):
                return False
            connection.execute(
                "UPDATE scenario_shares SET revoked_at = ? WHERE token_index = ?",
                (datetime.now(UTC).isoformat(), self._index(token)),
            )
        return True

    def save_connection(
        self, owner: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        stored = {**record, "owner": owner, "id": str(uuid4()), "created_at": datetime.now(UTC).isoformat()}
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO connections VALUES (?, ?, ?, ?)",
                (
                    stored["id"],
                    self._index(owner),
                    self._encrypt(stored),
                    stored["created_at"],
                ),
            )
        return stored

    def get_connection(self, owner: str, connection_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                "SELECT encrypted_record FROM connections WHERE id = ? AND owner_index = ?",
                (connection_id, self._index(owner)),
            ).fetchone()
        return None if row is None else self._decrypt(row[0])

    def list_connections(self, owner: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT encrypted_record FROM connections WHERE owner_index = ? ORDER BY created_at",
                (self._index(owner),),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def replace_connection(self, owner: str, record: dict[str, Any]) -> None:
        with sqlite3.connect(self._database_path) as connection:
            updated = connection.execute(
                "UPDATE connections SET encrypted_record = ? WHERE id = ? AND owner_index = ?",
                (self._encrypt(record), record["id"], self._index(owner)),
            )
        if updated.rowcount != 1:
            raise KeyError("connection not found")

    def append_audit(self, owner: str, record: dict[str, Any]) -> None:
        audit_id = str(uuid4())
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO audit_events VALUES (?, ?, ?, ?)",
                (
                    audit_id,
                    self._index(owner),
                    self._encrypt({**record, "id": audit_id}),
                    record["occurred_at"],
                ),
            )

    def list_audits(self, owner: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT encrypted_record FROM audit_events WHERE owner_index = ? ORDER BY occurred_at, id",
                (self._index(owner),),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def find_connection_by_item(self, item_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT encrypted_record FROM connections").fetchall()
        for row in rows:
            record = self._decrypt(row[0])
            if record.get("item_id") == item_id:
                return record
        return None

    def list_all_connections(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute("SELECT encrypted_record FROM connections").fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def upsert_financial_record(self, owner: str, connection_id: str, kind: str, source_id: str, record: dict[str, Any], *, removed: bool = False) -> None:
        now = datetime.now(UTC).isoformat()
        version = {
            "connection_id": connection_id,
            "kind": kind,
            "source_id": source_id,
            "removed": removed,
            "provider_record": record,
            "observed_at": now,
        }
        version_identity = json.dumps(
            {key: value for key, value in version.items() if key != "observed_at"},
            separators=(",", ":"),
            sort_keys=True,
        )
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """INSERT OR IGNORE INTO provider_record_versions
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    self._index(version_identity),
                    self._index(owner),
                    kind,
                    self._index(source_id),
                    self._encrypt(version),
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO financial_records VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, connection_id, source_index) DO UPDATE SET
                encrypted_record=excluded.encrypted_record, removed=excluded.removed, updated_at=excluded.updated_at""",
                (kind, connection_id, self._index(source_id), self._index(owner), self._encrypt(record), int(removed), now),
            )

    def list_financial_records(self, owner: str, kind: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT encrypted_record, removed FROM financial_records WHERE owner_index = ? AND kind = ? ORDER BY updated_at",
                (self._index(owner), kind),
            ).fetchall()
        return [{**self._decrypt(row[0]), "removed": bool(row[1])} for row in rows]

    def list_financial_records_for_export(self, owner: str) -> list[dict[str, Any]]:
        owner_index = self._index(owner)
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """SELECT kind, connection_id, source_index, encrypted_record,
                removed, updated_at FROM financial_records
                WHERE owner_index = ? ORDER BY kind, connection_id, updated_at""",
                (owner_index,),
            ).fetchall()
            version_rows = connection.execute(
                """SELECT encrypted_record FROM provider_record_versions
                WHERE owner_index = ? ORDER BY observed_at""",
                (owner_index,),
            ).fetchall()
        source_ids = {
            self._index(version["source_id"]): version["source_id"]
            for (encrypted,) in version_rows
            for version in [self._decrypt(encrypted)]
        }
        return [
            {
                "kind": kind,
                "connection_id": connection_id,
                "source_id": source_ids.get(source_index, source_index),
                "record": self._decrypt(encrypted_record),
                "removed": bool(removed),
                "updated_at": updated_at,
            }
            for kind, connection_id, source_index, encrypted_record, removed, updated_at in rows
        ]

    def list_provider_record_versions(
        self, owner: str, kind: str
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """SELECT encrypted_record FROM provider_record_versions
                WHERE owner_index = ? AND kind = ?
                ORDER BY observed_at, version_index""",
                (self._index(owner), kind),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def save_transaction_adjustment(
        self, owner: str, transaction_id: str, record: dict[str, Any]
    ) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """INSERT INTO transaction_adjustments VALUES (?, ?, ?, ?)
                ON CONFLICT(owner_index, transaction_index) DO UPDATE SET
                encrypted_record=excluded.encrypted_record, updated_at=excluded.updated_at""",
                (
                    self._index(owner),
                    self._index(transaction_id),
                    self._encrypt(record),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def list_transaction_adjustments(self, owner: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT encrypted_record FROM transaction_adjustments WHERE owner_index = ?",
                (self._index(owner),),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def get_sync_state(self, connection_id: str) -> dict[str, Any]:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute("SELECT encrypted_record FROM sync_states WHERE connection_id = ?", (connection_id,)).fetchone()
        return {} if row is None else self._decrypt(row[0])

    def save_sync_state(self, connection_id: str, state: dict[str, Any]) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO sync_states VALUES (?, ?) ON CONFLICT(connection_id) DO UPDATE SET encrypted_record=excluded.encrypted_record",
                (connection_id, self._encrypt(state)),
            )

    def record_webhook_once(self, fingerprint: str) -> bool:
        try:
            with sqlite3.connect(self._database_path) as connection:
                connection.execute("INSERT INTO webhook_receipts VALUES (?, ?)", (fingerprint, datetime.now(UTC).isoformat()))
            return True
        except sqlite3.IntegrityError:
            return False

    def save_transaction_classification(
        self, owner: str, transaction_id: str, record: dict[str, Any]
    ) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """INSERT INTO transaction_classifications VALUES (?, ?, ?, ?)
                ON CONFLICT(owner_index, transaction_index) DO UPDATE SET
                encrypted_record=excluded.encrypted_record, updated_at=excluded.updated_at""",
                (
                    self._index(owner),
                    self._index(transaction_id),
                    self._encrypt(record),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def list_transaction_classifications(self, owner: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT encrypted_record FROM transaction_classifications WHERE owner_index = ? ORDER BY updated_at",
                (self._index(owner),),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def save_classification_rule(self, owner: str, record: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO classification_rules VALUES (?, ?, ?, ?)",
                (record["id"], self._index(owner), self._encrypt(record), now),
            )

    def list_classification_rules(self, owner: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """SELECT encrypted_record FROM classification_rules
                WHERE owner_index = ? ORDER BY created_at, id""",
                (self._index(owner),),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def save_classification_suggestion(
        self, owner: str, record: dict[str, Any]
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO classification_suggestions VALUES (?, ?, ?, ?)",
                (record["id"], self._index(owner), self._encrypt(record), now),
            )

    def list_classification_suggestions(self, owner: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """SELECT encrypted_record FROM classification_suggestions
                WHERE owner_index = ? ORDER BY created_at, id""",
                (self._index(owner),),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def replace_classification_suggestion(
        self, owner: str, record: dict[str, Any]
    ) -> None:
        with sqlite3.connect(self._database_path) as connection:
            updated = connection.execute(
                """UPDATE classification_suggestions
                SET encrypted_record = ? WHERE id = ? AND owner_index = ?""",
                (self._encrypt(record), record["id"], self._index(owner)),
            )
        if updated.rowcount != 1:
            raise KeyError("suggestion not found")

    def save_recurring_obligation(self, owner: str, record: dict[str, Any]) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """INSERT INTO recurring_obligations VALUES (?, ?, ?, ?)
                ON CONFLICT(id, owner_index) DO UPDATE SET
                encrypted_record=excluded.encrypted_record,
                updated_at=excluded.updated_at""",
                (
                    record["id"],
                    self._index(owner),
                    self._encrypt(record),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def list_recurring_obligations(self, owner: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """SELECT encrypted_record FROM recurring_obligations
                WHERE owner_index = ? ORDER BY updated_at, id""",
                (self._index(owner),),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def create_email_review(
        self, owner: str, record: dict[str, Any], expires_at: datetime
    ) -> str:
        token = secrets.token_urlsafe(32)
        stored = {**record, "owner": owner, "expires_at": expires_at.isoformat()}
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO email_review_tokens VALUES (?, ?, ?, ?, NULL)",
                (
                    self._index(token),
                    self._index(owner),
                    self._encrypt(stored),
                    expires_at.isoformat(),
                ),
            )
        return token

    def get_email_review(
        self, token: str, owner: str | None = None
    ) -> dict[str, Any] | None:
        query = (
            "SELECT encrypted_record, expires_at, reply_received_at "
            "FROM email_review_tokens WHERE token_index = ?"
        )
        parameters: tuple[str, ...] = (self._index(token),)
        if owner is not None:
            query += " AND owner_index = ?"
            parameters = (*parameters, self._index(owner))
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(query, parameters).fetchone()
        if row is None:
            return None
        return {
            **self._decrypt(row[0]),
            "expired": datetime.fromisoformat(row[1]) <= datetime.now(UTC),
            "reply_received_at": row[2],
        }

    def claim_email_reply_token(self, token: str, received_at: str) -> bool:
        with sqlite3.connect(self._database_path) as connection:
            updated = connection.execute(
                """UPDATE email_review_tokens SET reply_received_at = ?
                WHERE token_index = ? AND reply_received_at IS NULL
                AND expires_at > ?""",
                (received_at, self._index(token), received_at),
            )
        return updated.rowcount == 1

    def save_email_reply(self, owner: str, record: dict[str, Any]) -> None:
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO email_replies VALUES (?, ?, ?, ?, ?)",
                (
                    record["id"],
                    self._index(owner),
                    self._encrypt(record),
                    record["received_at"],
                    record["raw_expires_at"],
                ),
            )

    def list_email_replies(self, owner: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """SELECT encrypted_record FROM email_replies
                WHERE owner_index = ? ORDER BY received_at, id""",
                (self._index(owner),),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def purge_expired_email_reply_raw(self, as_of: datetime) -> int:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """SELECT id, encrypted_record FROM email_replies
                WHERE raw_expires_at <= ?""",
                (as_of.isoformat(),),
            ).fetchall()
            purged = 0
            for reply_id, encrypted in rows:
                record = self._decrypt(encrypted)
                if "raw_message" not in record:
                    continue
                record.pop("raw_message")
                record["raw_purged_at"] = as_of.isoformat()
                connection.execute(
                    "UPDATE email_replies SET encrypted_record = ? WHERE id = ?",
                    (self._encrypt(record), reply_id),
                )
                purged += 1
        return purged

    def create_erasure_intent(self, owner: str) -> dict[str, str]:
        intent_id = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                "INSERT INTO erasure_intents VALUES (?, ?, ?, NULL)",
                (intent_id, self._index(owner), expires_at.isoformat()),
            )
        return {"id": intent_id, "expires_at": expires_at.isoformat()}

    def erasure_intent_is_valid(self, owner: str, intent_id: str) -> bool:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """SELECT expires_at, consumed_at FROM erasure_intents
                WHERE id = ? AND owner_index = ?""",
                (intent_id, self._index(owner)),
            ).fetchone()
        return bool(
            row
            and row[1] is None
            and datetime.fromisoformat(row[0]) > datetime.now(UTC)
        )

    def erase_owner_data(self, owner: str, intent_id: str) -> None:
        owner_index = self._index(owner)
        with sqlite3.connect(self._database_path) as connection:
            valid = connection.execute(
                """SELECT expires_at, consumed_at FROM erasure_intents
                WHERE id = ? AND owner_index = ?""",
                (intent_id, owner_index),
            ).fetchone()
            if (
                valid is None
                or valid[1] is not None
                or datetime.fromisoformat(valid[0]) <= datetime.now(UTC)
            ):
                raise KeyError("erasure intent is invalid or expired")
            connection_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM connections WHERE owner_index = ?",
                    (owner_index,),
                ).fetchall()
            ]
            scenario_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT id FROM scenarios WHERE owner_index = ?",
                    (owner_index,),
                ).fetchall()
            ]
            if scenario_ids:
                placeholders = ",".join("?" for _ in scenario_ids)
                connection.execute(
                    f"DELETE FROM scenario_shares WHERE scenario_id IN ({placeholders})",
                    scenario_ids,
                )
            if connection_ids:
                placeholders = ",".join("?" for _ in connection_ids)
                connection.execute(
                    f"DELETE FROM sync_states WHERE connection_id IN ({placeholders})",
                    connection_ids,
                )
            for table in (
                "scenarios",
                "connections",
                "audit_events",
                "financial_records",
                "transaction_adjustments",
                "provider_record_versions",
                "transaction_classifications",
                "classification_rules",
                "classification_suggestions",
                "recurring_obligations",
                "email_review_tokens",
                "email_replies",
                "mcp_grants",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE owner_index = ?", (owner_index,)
                )
            # Finance is a single-household service, and webhook receipt hashes can
            # correlate to the erased household's provider events.
            connection.execute("DELETE FROM webhook_receipts")
            connection.execute(
                "DELETE FROM erasure_intents WHERE owner_index = ?", (owner_index,)
            )

    def save_mcp_grant(self, owner: str, record: dict[str, Any]) -> None:
        stored = {**record, "owner": owner}
        with sqlite3.connect(self._database_path) as connection:
            connection.execute(
                """INSERT INTO mcp_grants VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET encrypted_record=excluded.encrypted_record""",
                (
                    stored["id"],
                    self._index(owner),
                    self._encrypt(stored),
                    stored["created_at"],
                ),
            )

    def get_mcp_grant(self, owner: str, grant_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self._database_path) as connection:
            row = connection.execute(
                """SELECT encrypted_record FROM mcp_grants
                WHERE id = ? AND owner_index = ?""",
                (grant_id, self._index(owner)),
            ).fetchone()
        return None if row is None else self._decrypt(row[0])

    def list_mcp_grants(self, owner: str) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                """SELECT encrypted_record FROM mcp_grants
                WHERE owner_index = ? ORDER BY created_at, id""",
                (self._index(owner),),
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]

    def list_all_mcp_grants(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self._database_path) as connection:
            rows = connection.execute(
                "SELECT encrypted_record FROM mcp_grants ORDER BY created_at, id"
            ).fetchall()
        return [self._decrypt(row[0]) for row in rows]
