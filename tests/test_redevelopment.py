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
