from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

import app as server
import parcel_engine


def census_match(locality, subdivision, basename=None):
    return {
        "matchedAddress": f"367 MAIN ST, {locality}, MA",
        "addressComponents": {
            "city": locality, "state": "MA", "streetName": "MAIN", "suffixType": "ST",
        },
        "coordinates": {"x": -70.286, "y": 41.652},
        "geographies": {"County Subdivisions": [
            {"STATE": "25", "NAME": subdivision, "BASENAME": basename},
        ]} if subdivision else {},
    }


@pytest.mark.parametrize(("locality", "subdivision", "basename", "expected"), [
    ("HYANNIS", "Barnstable Town city", "Barnstable Town", "BARNSTABLE"),
    ("DORCHESTER", "Boston city", "Boston", "BOSTON"),
    ("NORTH CHELMSFORD", "Chelmsford town", None, "CHELMSFORD"),
    ("CHESTNUT HILL", "Newton city", "Newton", "NEWTON"),
    ("MANCHESTER", "Manchester-by-the-Sea town", "Manchester-by-the-Sea", "MANCHESTER-BY-THE-SEA"),
])
def test_county_subdivision_precedes_mailing_locality(locality, subdivision, basename, expected):
    assert server.municipality_for_match(census_match(locality, subdivision, basename)) == (
        expected, "Census municipal boundary",
    )


def test_missing_boundary_only_falls_back_to_recognized_municipality():
    assert server.municipality_for_match(census_match("PITTSFIELD", None)) == (
        "PITTSFIELD", "Census address locality",
    )
    assert server.municipality_for_match(census_match("HYANNIS", None)) == ("", None)


@pytest.fixture
def mock_census(monkeypatch):
    def configure(matches):
        async def respond(request):
            assert str(request.url.copy_with(query=None)) == server.CENSUS
            assert request.url.params["layers"] == "County Subdivisions"
            return httpx.Response(200, json={"result": {"addressMatches": matches}})

        client_class = httpx.AsyncClient

        def make_client(**kwargs):
            return client_class(transport=httpx.MockTransport(respond), **kwargs)

        monkeypatch.setattr(server.httpx, "AsyncClient", make_client)

    return configure


def test_hyannis_uses_barnstable_parcel_sales_zoning_and_deed_route(monkeypatch, mock_census):
    mock_census([census_match("HYANNIS", "Barnstable Town city", "Barnstable Town")])
    record = {"attributes": {
        "OBJECTID": 1, "CITY": "BARNSTABLE", "ADDR_NUM": "367", "SITE_ADDR": "367 MAIN STREET (HYANNIS), HYANNIS",
        "FULL_STR": "MAIN STREET (HYANNIS)", "MAP_PAR_ID": "test-lot", "LOC_ID": "test-lot", "FY": 2026,
    }}
    other_village = {"attributes": {
        **record["attributes"], "OBJECTID": 2, "MAP_PAR_ID": "other-lot", "LOC_ID": "other-lot",
        "SITE_ADDR": "367 MAIN STREET (CENTERVILLE), CENTERVILLE", "FULL_STR": "MAIN STREET (CENTERVILLE)",
    }}
    parcel_query = AsyncMock(return_value=[record, other_village])
    monkeypatch.setattr(parcel_engine, "query", parcel_query)
    monkeypatch.setattr(parcel_engine, "selected_geometry", AsyncMock(return_value=None))
    atlas = AsyncMock(return_value={})
    monkeypatch.setattr(server.municipal_zoning, "multifamily_screen", atlas)
    sales = AsyncMock(return_value={"candidates": []})
    monkeypatch.setattr(server.ma_sales, "search", sales)
    monkeypatch.setattr(server, "new_build_sales_research", AsyncMock(return_value={"candidates": []}))
    research = AsyncMock(return_value={})
    monkeypatch.setattr(server, "search_zoning_sources", research)
    monkeypatch.setattr(server, "queue_registry_review", lambda *args: None)

    response = TestClient(server.app).get("/api/property", params={"address": "367 Main St, Hyannis, MA"})

    assert response.status_code == 200
    result = response.json()
    assert result["ready"] is False
    assert "address" not in result and "parcel" not in result
    assert any("CITY = 'BARNSTABLE'" in call.args[1].get("where", "") for call in parcel_query.await_args_list)
    assert atlas.await_args.args[1] == sales.await_args.args[3] == research.await_args.args[0] == "BARNSTABLE"
    assert server.registry.district_for("BARNSTABLE")["district"] == "Barnstable"


def test_conflicting_municipalities_require_a_more_specific_address(mock_census):
    mock_census([
        census_match("HYANNIS", "Barnstable Town city", "Barnstable Town"),
        census_match("CENTERVILLE", "Yarmouth town", "Yarmouth"),
    ])
    response = TestClient(server.app).get("/api/property", params={"address": "367 Main St, MA"})
    assert response.status_code == 409
    assert "multiple municipalities" in response.json()["detail"]
