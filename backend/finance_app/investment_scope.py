"""Ownership-scope rules for investment accounts.

Custodial accounts are visible in Finance, but belong to the child beneficiary and
must not be folded into the owner's household or retirement totals.
"""


def is_custodial_account(account: dict | None) -> bool:
    if not account:
        return False
    subtype = str(account.get("subtype") or "").strip().casefold()
    if subtype in {"utma", "ugma"} or "utma" in subtype or "ugma" in subtype:
        return True
    searchable = " ".join(
        str(account.get(field) or "")
        for field in ("name", "official_name")
    ).casefold()
    return any(
        marker in searchable
        for marker in (
            "uniform transfers to minors",
            "uniform gifts to minors",
            "custodial",
        )
    )


def ownership_scope(account: dict | None) -> str:
    return "custodial" if is_custodial_account(account) else "household"
