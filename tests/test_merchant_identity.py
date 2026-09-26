from backend.finance_app.merchant_identity import canonical_merchant


def test_rei_coop_retail_patterns_are_one_merchant():
    assert canonical_merchant("REI") == "REI Co-op"
    assert canonical_merchant("REI #61 GREENWOOD VIL.") == "REI Co-op"
    assert canonical_merchant("REI.COM 800-426-4840") == "REI Co-op"
    assert canonical_merchant("REI COOP") == "REI Co-op"


def test_resource_exchange_is_not_conflated_with_rei_coop():
    assert canonical_merchant("Resource Exchange") == "Resource Exchange International"
    assert canonical_merchant("RESOURCE EXCHANGE INTERNATIONAL") == "Resource Exchange International"
    assert canonical_merchant("Web Authorized Pmt Resource Exchang") == "Resource Exchange International"
    assert canonical_merchant("REI") != canonical_merchant("Resource Exchange")


def test_unrelated_names_containing_rei_letters_are_unchanged():
    assert canonical_merchant("Reigning Coffee") == "Reigning Coffee"
