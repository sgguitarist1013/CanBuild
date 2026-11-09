import asyncio
from datetime import date

import httpx
from shapely.geometry import box

from municipal_zoning import (
    CAMBRIDGE_DEMOLITION, cambridge_approval_screen, cambridge_constraints, cambridge_parcel_geometry,
)


def test_city_approval_screen_uses_polygon_and_keeps_source_failure_distinct():
    queries = []

    def respond(request):
        queries.append(request.url)
        assert request.url.params["geometryType"] == "esriGeometryPolygon"
        assert request.url.params["returnGeometry"] == "false"
        if "Zoning_Overlay_Districts" in request.url.path:
            return httpx.Response(200, json={"features": [{"attributes": {"Name": "Example overlay"}}]})
        if "Historic_Districts" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(200, json={"features": []})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await cambridge_constraints(client, box(0, 0, 40, 100), 42.36)

    constraints = asyncio.run(check())
    assert len(queries) == 4
    assert [item["status"] for item in constraints] == [
        "mapped intersection", "unavailable", "no mapped intersection", "no mapped intersection",
    ]
    approval = cambridge_approval_screen(constraints, {"year_built": 1910}, date(2026, 11, 9))
    assert approval["pricing_on_hold"]
    assert len(approval["checks"]) == 3
    assert approval["checks"][0]["url"] == CAMBRIDGE_DEMOLITION
    assert "Example overlay" in approval["checks"][1]["text"]
    assert "could not be checked" in approval["checks"][2]["text"]


def test_cambridge_demolition_review_flags_age_boundary_or_missing_year():
    today = date(2026, 11, 9)
    assert cambridge_approval_screen([], {"year_built": 1976}, today)["pricing_on_hold"]
    assert not cambridge_approval_screen([], {"year_built": 1977}, today)["pricing_on_hold"]
    assert cambridge_approval_screen([], {"building_area_sqft": 2000}, today)["pricing_on_hold"]



def test_cambridge_zoning_geometry_requires_one_exact_map_and_lot():
    feature = {"attributes": {"ML": "101-200"},
               "geometry": {"rings": [[[0, 0], [0, 10], [20, 10], [20, 0], [0, 0]]]}}

    async def check(features):
        def respond(request):
            assert request.url.params["where"] == "ML = '101-200'"
            return httpx.Response(200, json={"features": features})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await cambridge_parcel_geometry(client, "101-200", 42.36)

    assert asyncio.run(check([feature])).area > 0
    assert asyncio.run(check([feature, feature])) is None
    assert asyncio.run(check([{**feature, "attributes": {"ML": "101-99"}}])) is None
