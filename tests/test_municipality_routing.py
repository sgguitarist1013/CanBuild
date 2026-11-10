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
    research = AsyncMock(return_value={})
    monkeypatch.setattr(server, "search_zoning_sources", research)
    monkeypatch.setattr(server, "queue_registry_review", lambda *args: None)

    response = TestClient(server.app).get("/api/property", params={"address": "367 Main St, Hyannis, MA"})

    assert response.status_code == 200
    result = response.json()
    assert result["address"] == "367 MAIN ST, HYANNIS, MA"
    assert result["municipality_match"] == {
        "municipality": "BARNSTABLE", "mailing_locality": "HYANNIS", "source": "Census municipal boundary",
    }
    assert any("CITY = 'BARNSTABLE'" in call.args[1].get("where", "") for call in parcel_query.await_args_list)
    assert result["parcel"]["parcel_number"] == "test-lot"
    assert atlas.await_args.args[1] == sales.await_args.args[3] == research.await_args.args[0] == "BARNSTABLE"
    assert result["registry"]["district"] == result["parcel"]["deed_verification"]["district"] == "Barnstable"


def test_conflicting_municipalities_require_a_more_specific_address(mock_census):
    mock_census([
        census_match("HYANNIS", "Barnstable Town city", "Barnstable Town"),
        census_match("CENTERVILLE", "Yarmouth town", "Yarmouth"),
    ])
    response = TestClient(server.app).get("/api/property", params={"address": "367 Main St, MA"})
    assert response.status_code == 409
    assert "multiple municipalities" in response.json()["detail"]


@pytest.mark.parametrize(("pid", "failure", "status", "detail"), [
    (None, httpx.ReadTimeout("Assessor timeout"), 503,
     "Boston assessor records are temporarily unavailable. Try the same address again shortly."),
    ("0303029000", server.boston.ParcelSelectionError("No unique assessor record"), 409,
     "The selected Boston assessor record could not be confirmed. Search again to refresh the parcel choices."),
])
def test_boston_assessor_failure_returns_actionable_error(monkeypatch, mock_census, pid, failure, status, detail):
    matched = census_match("BOSTON", "Boston city", "Boston")
    matched["matchedAddress"] = "1 LEWIS WHARF, BOSTON, MA"
    matched["addressComponents"].update(streetName="LEWIS", suffixType="WHARF")
    mock_census([matched])
    monkeypatch.setattr(server.boston, "lookup", AsyncMock(side_effect=failure))
    atlas = AsyncMock(return_value={})
    monkeypatch.setattr(server.municipal_zoning, "multifamily_screen", atlas)
    monkeypatch.setattr(server, "search_zoning_sources", AsyncMock(return_value={}))

    params = {"address": "One Lewis Wharf, Boston, MA"}
    if pid:
        params["boston_pid"] = pid
    response = TestClient(server.app).get("/api/property", params=params)

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    atlas.assert_not_awaited()


@pytest.mark.parametrize("truncated", [False, True])
def test_boston_empty_candidate_search_reports_source_completeness(monkeypatch, mock_census, truncated):
    matched = census_match("BOSTON", "Boston city", "Boston")
    matched["matchedAddress"] = "1 LEWIS WHARF, BOSTON, MA"
    matched["addressComponents"].update(streetName="LEWIS", suffixType="WHARF")
    mock_census([matched])
    spatial_page = {"features": [], "exceededTransferLimit": truncated}
    pages = [{"features": []}, spatial_page]
    if truncated:
        pages.append(spatial_page)
    monkeypatch.setattr(server.boston, "fetch", AsyncMock(side_effect=pages))
    monkeypatch.setattr(server.municipal_zoning, "multifamily_screen", AsyncMock(return_value={}))
    monkeypatch.setattr(server, "search_zoning_sources", AsyncMock(return_value={}))

    response = TestClient(server.app).get("/api/property", params={"address": "One Lewis Wharf, Boston, MA"})

    assert response.status_code == 200
    report = response.json()
    assert report["parcel"] is None
    assert report["parcel_candidates"] == []
    assert report["parcel_candidates_truncated"] is truncated


@pytest.mark.parametrize("street,suffix", [("LEWIS", "WHARF"), ("LEWIS WHARF", "")])
def test_boston_rejects_changed_street_type_before_assessor_lookup(monkeypatch, mock_census, street, suffix):
    matched = census_match("BOSTON", "Boston city", "Boston")
    matched["matchedAddress"] = "1 LEWIS WHARF, BOSTON, MA"
    matched["addressComponents"].update(streetName=street, suffixType=suffix)
    mock_census([matched])
    lookup = AsyncMock(return_value=(None, [], "", False))
    monkeypatch.setattr(server.boston, "lookup", lookup)
    monkeypatch.setattr(server.municipal_zoning, "multifamily_screen", AsyncMock(return_value={}))
    monkeypatch.setattr(server, "search_zoning_sources", AsyncMock(return_value={}))

    response = TestClient(server.app).get("/api/property", params={"address": "1 Lewis St, Boston, MA"})

    assert response.status_code == 409
    assert "different street type" in response.json()["detail"]
    lookup.assert_not_awaited()


@pytest.mark.parametrize(("address", "house", "conflict"), [
    ("106 Magazine Avenue, Cambridge, MA", 106, True),
    ("106 Magazine Ave Cambridge MA 02139", 106, True),
    ("106 Magazine Ave Cambridge", 106, True),
    ("106 Magazine St., Cambridge, MA", 106, False),
    ("106 Magazine Street Cambridge MA 02139", 106, False),
    ("95 Magazine St, Cambridge, MA", 95, False),
])
def test_cambridge_street_type_must_agree_before_parcel_lookup(monkeypatch, mock_census, address, house, conflict):
    matched = census_match("CAMBRIDGE", "Cambridge city", "Cambridge")
    matched["matchedAddress"] = f"{house} MAGAZINE ST, CAMBRIDGE, MA"
    matched["addressComponents"]["streetName"] = "MAGAZINE"
    mock_census([matched])
    lot = "102-51" if house == 106 else "101-200"
    parcel_query = AsyncMock(return_value=[{"attributes": {
        "OBJECTID": 1, "CITY": "CAMBRIDGE", "ADDR_NUM": str(house),
        "SITE_ADDR": f"{house} MAGAZINE ST", "FULL_STR": "MAGAZINE ST", "MAP_PAR_ID": lot, "LOC_ID": lot,
    }}])
    monkeypatch.setattr(parcel_engine, "query", parcel_query)
    monkeypatch.setattr(parcel_engine, "selected_geometry", AsyncMock(return_value=None))
    monkeypatch.setattr(server.municipal_zoning, "cambridge_parcel_geometry", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(server.municipal_zoning, "cambridge_lookup", AsyncMock(return_value=None))
    monkeypatch.setattr(server.municipal_zoning, "multifamily_screen", AsyncMock(return_value={}))
    monkeypatch.setattr(server.ma_sales, "search", AsyncMock(return_value={"candidates": []}))
    monkeypatch.setattr(server, "search_zoning_sources", AsyncMock(return_value={}))
    monkeypatch.setattr(server, "queue_registry_review", lambda *args: None)

    response = TestClient(server.app).get("/api/property", params={"address": address})

    if conflict:
        assert response.status_code == 409
        assert "different street type" in response.json()["detail"]
        parcel_query.assert_not_awaited()
    else:
        assert response.status_code == 200
        assert response.json()["parcel"]["parcel_number"] == lot


@pytest.mark.parametrize(("address", "pid"), [
    ("106 Magazine St Apt. 2, Cambridge, MA", None),
    ("106 Magazine St, Unit 2, Cambridge, MA", None),
    ("106 Magazine #2, Cambridge, MA", None),
    ("106 Magazine St Apartment B Cambridge MA 02139", None),
    ("One Lewis Wharf Suite 2A, Boston, MA", "0303029000"),
    ("38-34 Atlantic Ave Ste. PH, Boston, MA", "0303029000"),
])
def test_explicit_unit_search_cannot_load_a_building_report(monkeypatch, mock_census, address, pid):
    matched = census_match("CAMBRIDGE", "Cambridge city", "Cambridge")
    matched["matchedAddress"] = "106 MAGAZINE ST, CAMBRIDGE, MA"
    matched["addressComponents"]["streetName"] = "MAGAZINE"
    mock_census([matched])
    lookup = AsyncMock(return_value=(None, None, None))
    monkeypatch.setattr(server, "resolve_parcel", lookup)
    monkeypatch.setattr(server.municipal_zoning, "cambridge_lookup", AsyncMock(return_value=None))
    monkeypatch.setattr(server.municipal_zoning, "multifamily_screen", AsyncMock(return_value={}))
    monkeypatch.setattr(server, "search_zoning_sources", AsyncMock(return_value={}))

    params = {"address": address}
    if pid:
        params["boston_pid"] = pid
    response = TestClient(server.app).get("/api/property", params=params)

    assert response.status_code == 409
    assert "individual units" in response.json()["detail"]
    lookup.assert_not_awaited()


@pytest.mark.parametrize("address", [
    "100A Main St, Cambridge, MA",
    "123 Unit Circle, Cambridge, MA",
    "One Lewis Wharf, Boston, MA",
])
def test_unit_guard_leaves_building_addresses_eligible(address):
    assert not parcel_engine.has_explicit_unit(address)


@pytest.mark.parametrize("address", ["106 Magazne St, Cambridge, MA", "106 Main Street Cambridge MA 02139"])
def test_changed_street_name_cannot_confirm_a_parcel(monkeypatch, mock_census, address):
    matched = census_match("CAMBRIDGE", "Cambridge city", "Cambridge")
    matched["matchedAddress"] = "106 MAGAZINE ST, CAMBRIDGE, MA, 02139"
    matched["addressComponents"]["streetName"] = "MAGAZINE"
    mock_census([matched])
    lookup = AsyncMock(return_value=(None, None, None))
    monkeypatch.setattr(server, "resolve_parcel", lookup)
    monkeypatch.setattr(server.municipal_zoning, "cambridge_lookup", AsyncMock(return_value=None))
    monkeypatch.setattr(server.municipal_zoning, "multifamily_screen", AsyncMock(return_value={}))
    monkeypatch.setattr(server, "search_zoning_sources", AsyncMock(return_value={}))

    response = TestClient(server.app).get("/api/property", params={"address": address})

    assert response.status_code == 409
    assert "different street name" in response.json()["detail"]
    lookup.assert_not_awaited()


@pytest.mark.parametrize(("address", "name", "suffix"), [
    ("100 Mass Ave, Cambridge, MA", "MASSACHUSETTS", "AVE"),
    ("100 Mount Auburn St Cambridge MA 02138", "MT AUBURN", "ST"),
    ("100 O’Brien St Apt 2, Cambridge, MA", "OBRIEN", "ST"),
    ("8 Saint Paul St, Brookline, MA", "ST PAUL", "ST"),
    ("One Lewis Wharf, Boston, MA", "LEWIS WHARF", ""),
])
def test_street_name_guard_accepts_normalized_equivalents(address, name, suffix):
    assert not parcel_engine.street_name_conflicts(address, {
        "streetName": name, "suffixType": suffix, "postal_city": "CAMBRIDGE",
    })


@pytest.mark.parametrize(("incomplete_search", "expanded_result"), [
    ("address", "incomplete"),
    ("point", "incomplete"),
    ("nearby", "incomplete"),
    ("address", "unavailable"),
    ("address", "complete"),
])
def test_massgis_requires_a_complete_parcel_search(monkeypatch, incomplete_search, expanded_result):
    match = census_match("CAMBRIDGE", None)
    match["addressComponents"]["streetName"] = "MAGAZINE"
    match["matchedAddress"] = "106 MAGAZINE ST, CAMBRIDGE, MA"
    record = {"attributes": {
        "OBJECTID": 106, "CITY": "CAMBRIDGE", "ADDR_NUM": "106",
        "SITE_ADDR": "106 MAGAZINE ST", "FULL_STR": "MAGAZINE ST",
        "MAP_PAR_ID": "102-51", "LOC_ID": "102-51", "OWNER1": "FIXTURE OWNER",
    }}
    downstream = []
    expanded_searches = []
    retry = False

    async def respond(request):
        if str(request.url.copy_with(query=None)) == server.CENSUS:
            return httpx.Response(200, json={"result": {"addressMatches": [match]}})
        assert str(request.url.copy_with(query=None)) == parcel_engine.SERVICE
        search = {"esriGeometryPoint": "point", "esriGeometryEnvelope": "nearby"}.get(
            request.url.params.get("geometryType"), "address",
        )
        incomplete = not retry and search == incomplete_search
        if request.url.params["resultRecordCount"] == "1000":
            expanded_searches.append(search)
            if expanded_result == "unavailable":
                return httpx.Response(503)
            incomplete = expanded_result != "complete"
        return httpx.Response(200, json={
            "features": [record] if retry or incomplete_search != "nearby" or search == "nearby" else [],
            "exceededTransferLimit": incomplete,
        })

    client_class = httpx.AsyncClient
    monkeypatch.setattr(server.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    for module, method, result in [
        (parcel_engine, "selected_geometry", None),
        (server.municipal_zoning, "cambridge_lookup", None),
        (server.municipal_zoning, "multifamily_screen", {}),
        (server.ma_sales, "search", {"candidates": []}),
        (server, "search_zoning_sources", {}),
        (server.permit_research, "search", {}),
    ]:
        mock = AsyncMock(return_value=result)
        monkeypatch.setattr(module, method, mock)
        downstream.append(mock)
    monkeypatch.setattr(server, "queue_registry_review", lambda *args: None)
    client = TestClient(server.app)
    params = {"address": "106 Magazine St, Cambridge, MA"}

    response = client.get("/api/property", params=params)
    assert expanded_searches == [incomplete_search]
    if expanded_result != "complete":
        assert response.status_code == 503
        assert "incomplete" in response.json()["detail"]
        for mock in downstream:
            mock.assert_not_awaited()

        retry = True
        response = client.get("/api/property", params=params)
        assert expanded_searches == [incomplete_search]
    assert response.status_code == 200
    assert response.json()["parcel"]["parcel_number"] == "102-51"
