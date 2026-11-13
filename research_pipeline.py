"""Evidence checks for each on-demand property research report.

This module does not infer missing facts or upgrade source confidence. It records
which report sections have evidence, and flags internal inconsistencies so the
UI and tests can make unsupported conclusions visible.
"""
from math import isfinite


def _check(key, state, message):
    return {"key": key, "state": state, "message": message}


def inspect_report(report):
    """Return a compact, deterministic quality summary for one property report."""
    report = report or {}
    parcel = report.get("parcel") or {}
    zoning = report.get("zoning") or {}
    valuation = report.get("current_valuation") or {}
    market = valuation.get("market_range")
    redevelopment = report.get("redevelopment") or {}
    best = redevelopment.get("best") or {}
    checks = []

    if parcel and report.get("address") and parcel.get("assessor_account_count", 1) == 1:
        checks.append(_check("parcel_identity", "supported", "A parcel record is attached to the selected address."))
    elif parcel:
        checks.append(_check("parcel_identity", "review", "Multiple assessor accounts may describe this site; confirm the development rights and parcel scope."))
    elif report.get("parcel_candidates"):
        checks.append(_check("parcel_identity", "review", "Choose the correct assessor record before relying on property-specific results."))
    else:
        checks.append(_check("parcel_identity", "unavailable", "No assessor parcel was matched."))

    if zoning.get("district"):
        historical = bool(zoning.get("historical_reference"))
        current = zoning.get("current_rules_verified") is True
        checks.append(_check("zoning", "supported" if current else "review",
            "Current district rules and applicable amendments were verified for this parcel." if current
            else "A mapped district exists, but current district rules and applicable amendments are not verified."))
    else:
        checks.append(_check("zoning", "unavailable", "A zoning district was not confirmed."))

    constraints = report.get("constraints") or []
    unavailable = [row for row in constraints if row.get("status") == "unavailable"]
    constraints_complete = report.get("constraints_screen_complete") is True
    checks.append(_check("site_constraints", "supported" if constraints_complete else "review",
        "Required site-constraint layers were checked for this parcel." if constraints_complete
        else f"{len(unavailable)} required layer(s) failed; screening is incomplete until all required layers are checked."))

    if market:
        count = market.get("comp_count")
        low, high = market.get("low"), market.get("high")
        valid_range = (type(count) is int and count >= 3 and _finite_number(low)
                       and _finite_number(high) and low > 0 and high >= low)
        checks.append(_check("as_is_comps", "supported" if valid_range else "error",
            "Market range has at least three distinct qualifying sales." if valid_range
            else "Market range failed minimum count or numeric consistency checks."))
    else:
        checks.append(_check("as_is_comps", "unavailable", "A current market range is unavailable."))

    if best:
        envelope_ok = (type(best.get("gross_area_sqft")) in (int, float)
                       and best.get("gross_area_sqft", 0) > 0
                       and (report.get("zoning_potential") or {}).get("rule_basis_verified") is True)
        checks.append(_check("buildout", "supported" if envelope_ok else "error",
            "A parcel-specific scenario is calculated from verified current zoning rules." if envelope_ok
            else "A modeled floor area is not enough; verified parcel-specific zoning rules are required."))
        market_mix = [row for row in best.get("unit_mix", []) if row.get("market_units", 0) > 0]
        pricing_rows = [row for row in market_mix if row.get("price_per_sqft_low") is not None
                        or row.get("price_per_sqft_high") is not None]
        unsupported = [row for row in pricing_rows
                       if type(row.get("comparable_projects")) is not int
                       or row.get("comparable_projects") < 3
                       or row.get("price_per_sqft_low") is None
                       or row.get("price_per_sqft_high") is None]
        has_financials = any(best.get(field) is not None for field in
                             ("sellout_low", "sellout_high", "cost_low", "cost_high", "surplus_low", "surplus_high"))
        incomplete_pricing = len(pricing_rows) != len(market_mix)
        if has_financials and (unsupported or incomplete_pricing):
            checks.append(_check("economics", "error", "Financial figures include a unit type with fewer than three priced comparable projects."))
        elif has_financials and not _financials_consistent(best):
            checks.append(_check("economics", "error", "Financial ranges or surplus arithmetic are internally inconsistent."))
        elif has_financials:
            checks.append(_check("economics", "supported", "Sellout and cost ranges reconcile to verified sales and disclosed cost assumptions."))
        else:
            checks.append(_check("economics", "unavailable", "Financial estimates are withheld because required pricing evidence is incomplete."))
    else:
        checks.append(_check("buildout", "unavailable", "A parcel-specific redevelopment scenario is unavailable."))
        checks.append(_check("economics", "unavailable", "Financial estimates require a supported building scenario and pricing evidence."))

    states = {row["state"] for row in checks}
    overall = "error" if "error" in states else "ready" if states <= {"supported"} else "review_required" if "review" in states else "partial"
    return {"status": overall, "checks": checks,
            "summary": f"{sum(row['state'] in ('review', 'error') for row in checks)} checks need review; unsupported sections remain unavailable."}


def guard_report(report):
    """Withhold suspect numeric estimates, retaining the report and audit trail."""
    quality = inspect_report(report)
    errors = {row["key"] for row in quality["checks"] if row["state"] == "error"}
    if "as_is_comps" in errors:
        valuation = report.get("current_valuation") or {}
        valuation["market_range"] = None
        valuation["market_range_withheld_reason"] = "Comp count or value-range validation failed."
    if "economics" in errors:
        redevelopment = report.get("redevelopment") or {}
        for scenario in ([redevelopment.get("best")] + redevelopment.get("alternatives", [])):
            if not scenario:
                continue
            for field in ("sellout_low", "sellout_high", "cost_low", "cost_high",
                          "surplus_low", "surplus_high", "cost_lines"):
                scenario[field] = [] if field == "cost_lines" else None
        redevelopment["acquisition_proxy"] = None
        redevelopment["financial_estimates_withheld_reason"] = "Comparable-pricing or arithmetic validation failed."
    report["research_quality"] = quality
    return report


def report_is_publishable(report):
    """Return true only when every required research stage is verified and generated."""
    quality = (report or {}).get("research_quality") or inspect_report(report)
    if quality.get("status") != "ready":
        return False
    checks = {row.get("key"): row.get("state") for row in quality.get("checks", [])}
    required = {"parcel_identity", "zoning", "site_constraints", "as_is_comps", "buildout", "economics"}
    if not required <= checks.keys() or any(checks[key] != "supported" for key in required):
        return False
    valuation = (report.get("current_valuation") or {}).get("market_range") or {}
    best = ((report.get("redevelopment") or {}).get("best") or {})
    return bool(
        valuation.get("comp_count", 0) >= 3
        and best.get("sellout_low") is not None and best.get("sellout_high") is not None
        and best.get("cost_low") is not None and best.get("cost_high") is not None
        and best.get("cost_lines")
    )


def public_report(report):
    """Keep every property-specific fact private until the report is complete."""
    if report_is_publishable(report):
        return {**report, "ready": True}
    return {
        "ready": False,
        "status": "researching",
        "message": "This search did not produce all required verified sources, so no property details are shown. Try again after the source checks are available.",
    }


def _financials_consistent(scenario):
    """Check published ranges and the surplus arithmetic without rounding drift."""
    sellout_low, sellout_high = scenario.get("sellout_low"), scenario.get("sellout_high")
    cost_low, cost_high = scenario.get("cost_low"), scenario.get("cost_high")
    for low, high in ((sellout_low, sellout_high), (cost_low, cost_high)):
        if (low is None) != (high is None):
            return False
        if low is not None and (not _finite_number(low) or not _finite_number(high)
                                or low < 0 or high < low):
            return False
    surplus_low, surplus_high = scenario.get("surplus_low"), scenario.get("surplus_high")
    if surplus_low is not None and (not _finite_number(surplus_low) or sellout_low is None or cost_high is None
                                    or abs(surplus_low - (sellout_low - cost_high)) > 1):
        return False
    if surplus_high is not None and (not _finite_number(surplus_high) or sellout_high is None or cost_low is None
                                     or abs(surplus_high - (sellout_high - cost_low)) > 1):
        return False
    return True


def _finite_number(value):
    return type(value) in (int, float) and isfinite(value)
