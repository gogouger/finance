import json
import os
from pathlib import Path


REQUIRED_RELEASE_GATES = (
    "encrypted_backups",
    "isolated_restore",
    "resource_budget",
    "redacted_observability",
    "accessibility",
    "cloudflare_origin_controls",
    "owasp_dependency_secret_review",
    "zap_staging",
)


def release_gate_status(path: str | Path | None = None) -> dict:
    gate_path = Path(
        path
        or os.environ.get(
            "FINANCE_RELEASE_GATES_FILE", "/run/finance/release-gates.json"
        )
    )
    try:
        document = json.loads(gate_path.read_text())
    except (OSError, ValueError, TypeError):
        document = {}
    configured = document.get("gates", {})
    gates = {name: configured.get(name) is True for name in REQUIRED_RELEASE_GATES}
    passed = document.get("status") == "approved" and all(gates.values())
    return {
        "status": "approved" if passed else "blocked",
        "production_plaid_linking": "enabled" if passed else "disabled",
        "gates": gates,
    }


def production_release_gates_pass(path: str | Path | None = None) -> bool:
    return release_gate_status(path)["status"] == "approved"
