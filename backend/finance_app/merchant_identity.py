import re


def canonical_merchant(value: str | None) -> str:
    """Return a display identity without fuzzy acronym matching.

    REI Co-op (the retailer) and Resource Exchange International (the nonprofit)
    share the letters REI, but are deliberately recognized by disjoint patterns.
    """

    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    upper = cleaned.upper()
    if (
        upper == "REI"
        or upper.startswith("REI #")
        or upper.startswith("REI.COM")
        or upper.startswith("REI CO-OP")
        or upper.startswith("REI COOP")
    ):
        return "REI Co-op"
    if upper in {"RESOURCE EXCHANGE", "RESOURCE EXCHANGE INTERNATIONAL"} or upper.startswith(
        "WEB AUTHORIZED PMT RESOURCE EXCHANG"
    ):
        return "Resource Exchange International"
    return cleaned
