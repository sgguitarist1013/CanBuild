from research_pipeline import guard_report, inspect_report


def test_unmatched_property_keeps_all_property_specific_sections_unavailable():
    quality = inspect_report({"parcel": None, "parcel_candidates": [], "zoning": None})
    checks = {check["key"]: check for check in quality["checks"]}
    assert checks["parcel_identity"]["state"] == "unavailable"
    assert checks["zoning"]["state"] == "unavailable"
    assert checks["buildout"]["state"] == "unavailable"
    assert checks["economics"]["state"] == "unavailable"


def test_ambiguous_parcel_and_historical_zoning_are_explicit_review_items():
    quality = inspect_report({
        "parcel": None, "address": "95 Main St", "parcel_candidates": [{"parcel_number": "1"}],
        "zoning": {"district": "R-1", "historical_reference": "old map"},
        "constraints": [{"name": "Historic", "status": "unavailable"}],
    })
    checks = {check["key"]: check for check in quality["checks"]}
    assert checks["parcel_identity"]["state"] == "review"
    assert checks["zoning"]["state"] == "review"
    assert checks["site_constraints"]["state"] == "review"
    assert quality["status"] == "review_required"


def test_market_range_must_have_three_sales_and_valid_bounds():
    quality = inspect_report({"current_valuation": {"market_range": {
        "low": 1_200_000, "high": 1_000_000, "comp_count": 2,
    }}})
    check = next(row for row in quality["checks"] if row["key"] == "as_is_comps")
    assert check["state"] == "error"


def test_priced_buildout_flags_cohorts_without_three_comparable_projects():
    quality = inspect_report({"redevelopment": {"best": {
        "gross_area_sqft": 12000, "sellout_low": 1_000_000,
        "unit_mix": [{"market_units": 3, "price_per_sqft_low": 700,
                      "price_per_sqft_high": 900, "comparable_projects": 2}],
    }}})
    checks = {check["key"]: check for check in quality["checks"]}
    assert checks["buildout"]["state"] == "error"
    assert checks["economics"]["state"] == "error"
    assert quality["status"] == "error"


def test_valid_scenario_still_carries_local_review_and_cost_assumption_limits():
    quality = inspect_report({
        "parcel": {"parcel_number": "1"}, "address": "95 Main St",
        "zoning": {"district": "R-1"}, "constraints": [],
        "constraints_screen_complete": True,
        "zoning_potential": {"rule_basis_verified": True},
        "redevelopment": {"best": {"gross_area_sqft": 12000,
            "sellout_low": 1_000_000, "sellout_high": 1_200_000,
            "cost_low": 900_000, "cost_high": 1_100_000,
            "surplus_low": -100_000, "surplus_high": 300_000,
            "unit_mix": [{"market_units": 3, "price_per_sqft_low": 700,
                          "price_per_sqft_high": 900, "comparable_projects": 3}] }},
    })
    checks = {check["key"]: check for check in quality["checks"]}
    assert checks["parcel_identity"]["state"] == "supported"
    assert checks["economics"]["state"] == "supported"


def test_incomplete_public_report_contains_no_property_facts_or_partial_estimates():
    from research_pipeline import public_report

    report = {"address": "229 MAIN ST, MONSON, MA", "parcel": {"owner": "PRIVATE OWNER"},
              "current_valuation": {"market_range": None}, "research_quality": {"status": "partial"}}
    assert public_report(report) == {
        "ready": False,
        "status": "researching",
        "message": "This search did not produce all required verified sources, so no property details are shown. Try again after the source checks are available.",
    }


def test_report_is_released_only_after_every_required_stage_is_supported():
    from research_pipeline import public_report

    complete = {
        "address": "95 Example St, Test, MA", "parcel": {"parcel_number": "1"},
        "zoning": {"district": "R-1", "current_rules_verified": True},
        "constraints_screen_complete": True,
        "zoning_potential": {"rule_basis_verified": True},
        "current_valuation": {"market_range": {"low": 1_000_000, "high": 1_200_000, "comp_count": 3}},
        "redevelopment": {"best": {
            "gross_area_sqft": 12000, "sellout_low": 2_000_000, "sellout_high": 2_400_000,
            "cost_low": 1_500_000, "cost_high": 1_800_000,
            "surplus_low": 200_000, "surplus_high": 900_000,
            "cost_lines": [{"name": "Construction", "upside": 1_000_000, "downside": 1_200_000}],
            "unit_mix": [{"market_units": 3, "price_per_sqft_low": 1000,
                          "price_per_sqft_high": 1200, "comparable_projects": 3}],
        }},
    }
    released = public_report(guard_report(complete))
    assert released["ready"] is True
    assert released["address"] == complete["address"]

    complete["zoning"]["current_rules_verified"] = False
    withheld = public_report(guard_report(complete))
    assert withheld["ready"] is False
    assert "address" not in withheld


def test_surplus_must_reconcile_to_sellout_and_cost_ranges():
    quality = inspect_report({"redevelopment": {"best": {
        "gross_area_sqft": 12000, "sellout_low": 1_000_000, "sellout_high": 1_200_000,
        "cost_low": 900_000, "cost_high": 1_100_000,
        "surplus_low": 250_000, "surplus_high": 300_000,
        "unit_mix": [{"market_units": 1, "price_per_sqft_low": 700,
                      "price_per_sqft_high": 900, "comparable_projects": 3}],
    }}})
    economics = next(check for check in quality["checks"] if check["key"] == "economics")
    assert economics["state"] == "error"


def test_guard_withholds_inconsistent_market_range_and_financial_values():
    report = {
        "current_valuation": {"market_range": {"low": 2_000_000, "high": 1_000_000, "comp_count": 1}},
        "redevelopment": {"best": {
            "gross_area_sqft": 12000, "sellout_low": 1_000_000, "sellout_high": 1_200_000,
            "cost_low": 900_000, "cost_high": 1_100_000,
            "surplus_low": 250_000, "surplus_high": 300_000, "cost_lines": [{"name": "Costs"}],
            "unit_mix": [{"market_units": 1, "price_per_sqft_low": 700,
                          "price_per_sqft_high": 900, "comparable_projects": 3}],
        }},
    }
    guarded = guard_report(report)
    assert guarded["current_valuation"]["market_range"] is None
    assert guarded["current_valuation"]["market_range_withheld_reason"]
    for field in ("sellout_low", "sellout_high", "cost_low", "cost_high", "surplus_low", "surplus_high"):
        assert guarded["redevelopment"]["best"][field] is None
    assert guarded["redevelopment"]["best"]["gross_area_sqft"] == 12000
    assert guarded["research_quality"]["status"] == "error"


def test_malformed_nonfinite_and_string_financial_values_are_withheld_safely():
    quality = inspect_report({"redevelopment": {"best": {
        "gross_area_sqft": 12000, "sellout_low": 1_000_000, "sellout_high": 1_200_000,
        "cost_low": 900_000, "cost_high": float("nan"), "surplus_low": "unknown",
        "unit_mix": [{"market_units": 1, "price_per_sqft_low": 700,
                      "price_per_sqft_high": 900, "comparable_projects": 3}],
    }}})
    economics = next(check for check in quality["checks"] if check["key"] == "economics")
    assert economics["state"] == "error"
