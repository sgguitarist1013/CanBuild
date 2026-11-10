import asyncio
from datetime import date
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box

import app as server
from municipal_zoning import (
    CAMBRIDGE_DEMOLITION, CAMBRIDGE_NEIGHBOR_NOTICE, cambridge_approval_screen, cambridge_constraints, cambridge_mapped_frontage,
    cambridge_parcel_geometry,
)


@pytest.mark.parametrize(("stories", "height", "show"), [(3, 45, False), (4, 35, False), (4, 45, True)])
def test_cambridge_neighbor_meeting_requires_both_thresholds_without_a_pricing_hold(stories, height, show):
    review = cambridge_approval_screen([], {}, c1_envelope={"stories": stories, "height_ft": height})
    assert not review["pricing_on_hold"]
    assert bool(review["checks"]) is show
    if show:
        assert review["checks"][0]["url"] == CAMBRIDGE_NEIGHBOR_NOTICE
        assert "If neither applies" in review["checks"][0]["text"]


@pytest.mark.parametrize(("house", "lot", "year", "area", "frontage", "living", "sales_state"), [
    (95, "101-200", 1873, 7450, 70, 2702, "held"),
    (106, "102-51", 1886, 1108, 19.07, 2083, "held"),
    # Exercise the pricing path with a synthetic newer building. The actual parcel remains subject to review.
    (95, "101-200", 2020, 7450, 70, 2702, "empty"),
    (95, "101-200", 2020, 7450, 70, 2702, "unavailable"),
    (106, "102-51", 2020, 1108, 19.07, 2083, "empty"),
])
def test_magazine_property_report_checks_approvals_before_replacement_pricing(
    monkeypatch, house, lot, year, area, frontage, living, sales_state,
):
    street = f"{house} Magazine St"
    address = f"{street}, Cambridge, MA"
    geocode = {"matchedAddress": address, "coordinates": {"x": -71.11, "y": 42.36},
               "addressComponents": {"city": "Cambridge", "state": "MA"}}
    client_class = httpx.AsyncClient

    def respond(request):
        if str(request.url.copy_with(query=None)) == server.CENSUS:
            return httpx.Response(200, json={"result": {"addressMatches": [geocode]}})
        assert any(f"/{layer}/FeatureServer/0/query" in request.url.path
                   for _, layer, _, _ in server.municipal_zoning.CAMBRIDGE_APPROVAL_LAYERS)
        return httpx.Response(200, json={"features": []})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **kwargs: client_class(transport=transport, **kwargs))
    parcel = {"parcel_number": lot, "assessor_site_address": street, "gis_area_sqft_approx": area}
    building = {"year_built": year, "residential_area_sqft": living, "units": 3}
    monkeypatch.setattr(server, "resolve_parcel", AsyncMock(return_value=(parcel, building, None)))
    # Use a reproducible rectangle. The live parcel shape and a surveyed building plan require separate review.
    geometry = box(0, 0, frontage, area / frontage)
    monkeypatch.setattr(server.municipal_zoning, "cambridge_parcel_geometry", AsyncMock(return_value=geometry))
    monkeypatch.setattr(server.municipal_zoning, "cambridge_lookup", AsyncMock(return_value={"district": "C-1"}))
    monkeypatch.setattr(server.municipal_zoning, "multifamily_screen", AsyncMock(return_value={}))
    monkeypatch.setattr(server.ma_sales, "search", AsyncMock(return_value={"candidates": []}))
    monkeypatch.setattr(server, "search_zoning_sources", AsyncMock(return_value={}))
    monkeypatch.setattr(server, "queue_registry_review", lambda *args: None)
    pricing = AsyncMock(return_value={"candidates": []})
    if sales_state == "unavailable":
        pricing.side_effect = httpx.ConnectError("Condo sales temporarily unavailable")
    monkeypatch.setattr(server, "cambridge_new_build_sales", pricing)

    response = TestClient(server.app).get("/api/property", params={"address": address})
    assert response.status_code == 200
    report = response.json()
    assert report["parcel"]["parcel_number"] == lot
    assert report["cambridge_mapped_frontage"]["frontage_ft"] == frontage
    assert report["zoning_potential"]["stories"] == 4
    assert [item["status"] for item in report["constraints"]] == ["no mapped intersection"] * 4
    review = report["cambridge_approvals"]
    held = sales_state == "held"
    assert review["pricing_on_hold"] is held
    expected_checks = [CAMBRIDGE_DEMOLITION, CAMBRIDGE_NEIGHBOR_NOTICE] if held else [CAMBRIDGE_NEIGHBOR_NOTICE]
    assert [check["url"] for check in review["checks"]] == expected_checks
    if held or house == 106:
        pricing.assert_not_awaited()
    else:
        pricing.assert_awaited_once()
    if house == 95:
        best = report["redevelopment"]["best"]
        assert best["conditional"]
        assert all(best[field] is None for field in ("sellout_low", "sellout_high", "surplus_low", "surplus_high"))
        if held:
            assert best["cost_low"] is best["cost_high"] is None
            assert not best["cost_lines"]
    else:
        assert report["redevelopment"]["decision"] == "retain_and_review"
        assert "best" not in report["redevelopment"]
        assert "inclusionary_scenario" not in report["zoning_potential"]


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


def test_cambridge_mapped_frontage_requires_reviewed_map_lot_and_exact_assessor_address():
    for lot, address, feet, block in (
        ("101-200", "95 MAGAZINE ST", 70.0, 101),
        ("102-51", "106 Magazine Street", 19.07, 102),
    ):
        parcel = {"parcel_number": lot, "assessor_site_address": address}
        assert cambridge_mapped_frontage(parcel) == {
            "street": "Magazine Street", "frontage_ft": feet,
            "source_url": f"https://gis.cambridgema.gov/blockmaps/Block_{block}.pdf",
        }
        assert cambridge_mapped_frontage({**parcel, "assessor_site_address": address + " Unit 1"}) is None
        assert cambridge_mapped_frontage({**parcel, "parcel_number": "102-50"}) is None


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
