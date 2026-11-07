import httpx
import pytest

import permit_research as permits


@pytest.mark.anyio
async def test_boston_permits_use_selected_assessor_parcel():
    async def handler(request):
        assert "WHERE parcel_id = 303029000" in request.url.params["sql"]
        return httpx.Response(200, json={"success": True, "result": {"records": [
            {"permitnumber": "E1", "parcel_id": "303029000", "permittypedescr": "Electrical", "issued_date": "2025-01-01"},
            {"permitnumber": "COO1", "parcel_id": "303029000", "permittypedescr": "Certificate of Occupancy", "issued_date": "2015-02-11"},
            {"permitnumber": "COO2", "parcel_id": "303029001", "permittypedescr": "Certificate of Occupancy"},
        ]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await permits.search(client, "Boston", "1 Lewis Wharf", "0303029000")
        invalid = await permits.search(client, "Boston", "1 Lewis Wharf", "1 OR 1=1")
    assert result["status"] == "found"
    assert [r["permit_id"] for r in result["records"]] == ["COO1", "E1"]
    assert invalid["status"] == "needs_identity"


@pytest.mark.anyio
async def test_cambridge_permits_confirm_map_lot_and_limit_links_to_official_host():
    async def handler(request):
        assert request.url.params["$where"] == "mbl='101-200'"
        return httpx.Response(200, json=[
            {"id": "7", "mbl": "101-200", "permit_type": "Roof", "specific_url": {"url": "https://unrelated.example/permit"}},
            {"id": "8", "mbl": "101-201", "permit_type": "Demolition"},
        ])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await permits.search(client, "Cambridge", "95 Magazine St", "101-200")
    assert [r["permit_id"] for r in result["records"]] == ["7"]
    assert result["records"][0]["source_url"] == permits.CAMBRIDGE_DATA


@pytest.mark.anyio
async def test_somerville_historical_permits_exclude_other_house_numbers_and_street_types():
    async def handler(request):
        assert request.url.params["$where"] == "upper(address) like '9 GORHAM%'"
        return httpx.Response(200, json=[
            {"address": "9 Gorham St", "id": "ET20-000006", "work": "Emergency sewer repair"},
            {"address": "9 Gorham Pl", "id": "wrong-street"},
            {"address": "90 Gorham St", "id": "wrong-number"},
        ])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await permits.search(client, "Somerville", "9 GORHAM STREET, Somerville, MA", "17-A-6")
    assert [r["permit_id"] for r in result["records"]] == ["ET20-000006"]
    assert "Historical" in result["coverage_note"]
