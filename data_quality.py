"""Keep assessor observations separate from verified property facts."""
import re


def review_building(building):
    if not building:
        return []
    raw = building.get("units")
    building["reported_units"] = raw
    use = str(building.get("use_description") or "").upper().replace("-", " ")
    inferred = next((n for word, n in (("SINGLE", 1), ("ONE", 1), ("TWO", 2), ("THREE", 3))
                     if re.search(r"\b" + word + r"\s+FAMILY\b", use)), None)
    building["use_classification_units"] = inferred
    valid = isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0 and int(raw) == raw
    conflict = inferred is not None and raw is not None and raw != inferred
    building["units"] = int(raw) if valid and not conflict else None
    building["unit_count_status"] = "conflict" if conflict else "assessor reported" if valid else "not reported"
    building["unit_count_note"] = (
        f"Assessor unit field reports {raw}; the property-use classification describes {inferred}. Legal dwelling count is unresolved."
        if conflict else "Assessor-reported count; confirm legal units against permits and occupancy records."
        if valid else "No usable dwelling-unit count reported; zero is not treated as a verified count."
    )
    return [building["unit_count_note"]] if conflict else []
