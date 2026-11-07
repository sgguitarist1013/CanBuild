from redevelopment import analyze_redevelopment
from condo_analysis import reference_summary


def test_automatic_scenario_reserves_whole_affordable_units_and_ranks_after_costs():
    envelope = {"gross_area_high_sqft": 12000, "stories": 4, "inclusionary_scenario": {
        "gross_area_high_sqft": 18000, "stories": 6,
    }}
    result = analyze_redevelopment(envelope, {"assessed_value": 900000, "building_area_sqft": 3000})
    best = result["best"]
    assert best["common_area_sqft"] + best["market_area_sqft"] + best["restricted_area_sqft"] + best["unallocated_area_sqft"] == best["gross_area_sqft"]
    assert best["restricted_area_sqft"] >= .20 * (best["restricted_area_sqft"] + best["market_area_sqft"])
    assert best["restricted_units"] > 0
    assert best["surplus_high"] >= result["alternatives"][0]["surplus_high"]
    assert best["sellout_high"] - best["cost_low"] == best["surplus_high"]
    assert result["restricted_proceeds_credited"] == 0


def test_area_threshold_can_trigger_affordability_below_ten_units():
    result = analyze_redevelopment({"gross_area_high_sqft": 11000, "stories": 4}, None)
    assert result["best"]["market_units"] + result["best"]["restricted_units"] < 10
    assert result["best"]["restricted_units"] > 0
    assert result["acquisition_proxy"] is None


def test_new_build_sales_screen_excludes_repeat_projects_and_later_resales():
    sample = {"sale_price": 900000, "price_per_living_sqft": 900, "sale_date": "2026-08-05",
              "year_built_reported": 2025, "project_key": "a"}
    rows = [
        dict(sample, record_reference="A"),
        dict(sample, record_reference="B"),
        dict(sample, project_key="b", year_built_reported=2021, record_reference="C"),
        dict(sample, project_key="c", record_reference=None),
    ]
    result = reference_summary(rows, "https://example.org", new_build_only=True, radius_miles=3)
    assert [r["record_reference"] for r in result["candidates"]] == ["A"]
