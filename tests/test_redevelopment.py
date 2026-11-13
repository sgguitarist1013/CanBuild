from redevelopment import analyze_redevelopment, cost_budget
from condo_analysis import reference_summary


def verified_condos(rate):
    return [
        {"project_key": str(project), "living_area_sqft": area, "distance_miles": .2,
         "bedrooms_reported": beds, "bathrooms_reported": baths, "sale_price": area * rate,
         "price_per_living_sqft": rate, "deed_verification": {"status": "verified", "consideration": area * rate}}
        for project in range(3) for beds, baths, area in ((1, 1, 700), (2, 2, 1050), (3, 2, 1400))
    ]


def test_somerville_costs_use_local_hard_cost_soft_cost_and_financing_inputs():
    base = cost_budget(4500, 4254, 3, 3, 1_650_000, 2_700_000,
                       stress=False, municipality="SOMERVILLE")
    stress = cost_budget(4500, 4254, 3, 3, 1_650_000, 2_700_000,
                         stress=True, municipality="SOMERVILLE")
    assert base["Building construction, GC overhead and profit"] == 1_125_000
    assert stress["Building construction, GC overhead and profit"] == 1_318_500
    assert base["Architecture, engineering and consultants"] == 257_562
    assert stress["Brokerage, marketing, closing, excise and concessions"] == 202_500


def test_somerville_three_unit_scenario_prices_only_matching_verified_condo_sales():
    references = [
        {"project_key": f"somerville-{i}", "living_area_sqft": area,
         "distance_miles": distance, "bedrooms_reported": 3, "bathrooms_reported": 2,
         "sale_price": area * rate, "price_per_living_sqft": rate,
         "deed_verification": {"status": "verified", "consideration": area * rate}}
        for i, (area, rate, distance) in enumerate(((1074, 754, .4), (1200, 687, .8), (1217, 809, 1.2)))
    ]
    report = analyze_redevelopment(
        {"gross_area_high_sqft": 4500, "stories": 3},
        {"building_area_sqft": 4254, "residential_area_sqft": 2722, "units": 3},
        references, current_market_range={"low": 1_500_000, "high": 1_700_000},
        municipality="SOMERVILLE",
    )
    best = report["best"]
    assert best["market_units"] == 3
    assert best["unit_mix"][0]["bedrooms"] == 3
    assert best["unit_mix"][0]["bathrooms_assumed"] == 2
    assert best["sellout_low"] is not None and best["sellout_high"] is not None
    assert best["cost_low"] is not None and best["cost_high"] is not None
    assert report["sources"][0]["url"].startswith("https://somervillema.legistar.com/")


def test_automatic_scenario_reserves_whole_affordable_units_and_ranks_after_costs():
    envelope = {"gross_area_high_sqft": 12000, "stories": 4, "inclusionary_scenario": {
        "gross_area_high_sqft": 18000, "stories": 6,
    }}
    result = analyze_redevelopment(envelope, {"assessed_value": 900000, "building_area_sqft": 3000},
                                   verified_condos(1700), current_market_range={"low": 1000000, "high": 1200000})
    assert result["acquisition_proxy"] == 1100000
    best = result["best"]
    assert best["common_area_sqft"] + best["market_area_sqft"] + best["restricted_area_sqft"] + best["unallocated_area_sqft"] == best["gross_area_sqft"]
    assert best["restricted_area_sqft"] >= .20 * (best["restricted_area_sqft"] + best["market_area_sqft"])
    assert best["restricted_units"] > 0
    assert best["surplus_high"] >= result["alternatives"][0]["surplus_high"]
    assert best["sellout_high"] - best["cost_low"] == best["surplus_high"]
    assert result["restricted_proceeds_credited"] == 0


def test_area_threshold_includes_exactly_10000_sqft_below_ten_units():
    result = analyze_redevelopment({"gross_area_high_sqft": 10000, "stories": 4}, None)
    assert result["best"]["market_units"] + result["best"]["restricted_units"] < 10
    assert result["best"]["restricted_units"] > 0
    assert result["acquisition_proxy"] is None


def test_smaller_rebuild_is_rejected_before_pricing():
    result = analyze_redevelopment({"gross_area_high_sqft": 1703, "stories": 4},
                                   {"assessed_value": 1932200, "residential_area_sqft": 2083, "units": 1})
    assert result["decision"] == "retain_and_review"
    assert "best" not in result
    assert result["rejected_options"] == [
        {"name": "Base redevelopment", "proposed_gross_area_sqft": 1703,
         "proposed_living_area_sqft": 1050, "proposed_units": 1},
    ]


def test_larger_scenario_stays_unpriced_without_verified_sales_for_each_unit_type():
    comps = verified_condos(1700)
    for comp in comps:
        if comp["bedrooms_reported"] == 2:
            comp["deed_verification"]["status"] = "pending"
    result = analyze_redevelopment({"gross_area_high_sqft": 12000, "stories": 4},
                                   {"assessed_value": 1932200, "residential_area_sqft": 2083}, comps)
    assert result["best"]["sellout_high"] is None
    assert result["best"]["cost_high"] is None
    assert result["best"]["surplus_high"] is None
    assert result["acquisition_proxy"] is None


def test_priced_rebuild_that_loses_money_before_land_is_flagged():
    result = analyze_redevelopment({"gross_area_high_sqft": 12000, "stories": 4}, {}, verified_condos(350))
    assert result["decision"] == "modeled_loss"
    assert result["best"]["sellout_high"] <= result["best"]["cost_low"]
    assert result["best"]["surplus_high"] is None


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


def test_city_assessor_sales_use_shared_parcel_and_keep_unit_bedrooms():
    import asyncio
    from datetime import date

    import httpx

    from condo_analysis import cambridge_new_build_sales

    row = {
        "pid": "1001", "address": "10 Example St", "unit": "1", "gisid": "88-29",
        "stateclasscode": "102", "saleprice": "1200000", "saledate": "2025-04-01T00:00:00",
        "book_page": "83000/155", "condition_yearbuilt": "2025",
        "interior_livingarea": "1120", "interior_bedrooms": "2",
    }

    def respond(request):
        assert "stateclasscode = '102'" in request.url.params["$where"]
        return httpx.Response(200, json=[
            row,
            dict(row, pid="1002", unit="2", book_page="83000/156"),
            dict(row, pid="1003", unit="3", book_page="83000/157", interior_bedrooms="3"),
            dict(row, pid="1004", gisid="88-30", book_page="83000/158", condition_yearbuilt="2023"),
            dict(row, pid="1005", gisid="88-31", book_page="/"),
        ])

    async def load():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await cambridge_new_build_sales(client, date(2026, 11, 7))

    result = asyncio.run(load())
    assert result["radius_miles"] is None
    assert [(c["address"], c["bedrooms_reported"]) for c in result["candidates"]] == [
        ("10 Example St Unit 1", 2), ("10 Example St Unit 3", 3),
    ]


def test_finished_condos_expand_only_distance_to_third_distinct_project():
    from redevelopment import matching_condos
    base = {"living_area_sqft": 1050, "bedrooms_reported": 2, "bathrooms_reported": 2}
    rows = [
        dict(base, project_key="one", distance_miles=.1),
        dict(base, project_key="one", distance_miles=.2),
        dict(base, project_key="two", distance_miles=.3),
        dict(base, project_key="bad-size", distance_miles=.4, living_area_sqft=1250),
        dict(base, project_key="bad-bath", distance_miles=.4, bathrooms_reported=1),
        dict(base, project_key="no-distance", distance_miles=None),
        dict(base, project_key="three", distance_miles=.55),
        dict(base, project_key="four", distance_miles=.7),
    ]
    assert [r["project_key"] for r in matching_condos(rows, 2, 2, 1050)] == ["one", "two", "three"]
