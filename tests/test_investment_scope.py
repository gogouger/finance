from backend.finance_app.investment_scope import is_custodial_account, ownership_scope


def test_utma_and_ugma_subtypes_are_custodial():
    assert is_custodial_account({"subtype": "utma"})
    assert is_custodial_account({"subtype": "UGMA"})
    assert ownership_scope({"subtype": "utma"}) == "custodial"


def test_provider_names_are_a_safe_fallback_for_custodial_accounts():
    assert is_custodial_account({"name": "Uniform Transfers to Minors (UTMA)"})
    assert is_custodial_account({"official_name": "Custodial Brokerage"})


def test_regular_brokerage_and_retirement_accounts_are_household():
    assert not is_custodial_account({"name": "Brokerage", "subtype": "brokerage"})
    assert not is_custodial_account({"name": "401(k)", "subtype": "401k"})
    assert ownership_scope({"subtype": "401k"}) == "household"
