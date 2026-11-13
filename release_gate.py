"""Strict acceptance contract for PlotDocket's publishable property reports."""

REQUIRED_CHECKS = {
    "parcel_identity", "zoning", "site_constraints", "as_is_comps", "buildout", "economics",
}


def validate_complete_report(report):
    """Raise AssertionError unless a report has complete, supported evidence."""
    assert isinstance(report, dict), "Property API did not return a report object"
    assert report.get("ready") is True, "Property research is incomplete; report was withheld"
    assert "9 GORHAM" in str(report.get("address", "")).upper(), "Acceptance report is for the wrong address"
    assert (report.get("parcel") or {}).get("parcel_number"), "Acceptance report has no matched parcel"
    quality = report.get("research_quality") or {}
    assert quality.get("status") == "ready", "Research quality status is not ready"
    checks = {row.get("key"): row.get("state") for row in quality.get("checks", [])}
    missing = REQUIRED_CHECKS - checks.keys()
    assert not missing, f"Missing required research checks: {sorted(missing)}"
    unsupported = {key: checks[key] for key in REQUIRED_CHECKS if checks[key] != "supported"}
    assert not unsupported, f"Research checks are not supported: {unsupported}"

    market = (report.get("current_valuation") or {}).get("market_range") or {}
    assert type(market.get("comp_count")) is int and market["comp_count"] >= 3, "Current market range needs at least three qualifying sales"
    assert type(market.get("low")) in (int, float) and market["low"] > 0, "Current market range has no valid low value"
    assert type(market.get("high")) in (int, float) and market["high"] >= market["low"], "Current market range has no valid high value"
    candidates = (report.get("sales_research") or {}).get("candidates") or []
    verified_addresses = {
        str(row.get("address", "")).strip().upper()
        for row in candidates
        if (row.get("deed_verification") or {}).get("status") == "verified"
    }
    market_addresses = {str(address).strip().upper() for address in market.get("comp_addresses", [])}
    assert len(verified_addresses) >= 3 and len(market_addresses) >= 3, "Market range does not have three deed-verified sales"
    assert market_addresses <= verified_addresses, "Market range includes a sale without deed verification"
    potential = report.get("zoning_potential") or {}
    assert potential.get("rule_basis_verified") is True, "Buildout is not tied to verified parcel zoning rules"
    best = ((report.get("redevelopment") or {}).get("best") or {})
    for field in ("sellout_low", "sellout_high", "cost_low", "cost_high"):
        assert type(best.get(field)) in (int, float) and best[field] >= 0, f"Missing valid {field}"
    assert isinstance(best.get("cost_lines"), list) and best["cost_lines"], "Development cost breakdown is missing"
    assert isinstance(report.get("constraints"), list), "Site-constraint results are missing"
    assert report.get("constraints_screen_complete") is True, "Required site-constraint layers are incomplete"
    return report
