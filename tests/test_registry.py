import sqlite3

import pytest
import registry


def test_registry_maps_pilots_and_covers_all_towns():
    from municipal_zoning import MUNICIPALITIES
    assert set(registry.DISTRICTS) == set(MUNICIPALITIES)
    assert registry.district_for("Somerville")["district"] == "Middlesex South"
    assert registry.district_for("Boston")["district"] == "Suffolk"


def test_registry_match_requires_exact_address_reference_and_consideration():
    book_page = registry.reference_pair("Book 80505, page 0062")
    result = registry.check("Somerville", "9 GORHAM STREET, Somerville, MA", book_page, 1650000)
    assert result["status"] == "verified"
    assert result["recorded_date"] == "2022-07-29"
    assert registry.check("Somerville", "9 Gorham St Unit 1", book_page)["status"] == "pending"
    assert registry.check("Cambridge", "9 Gorham St", book_page)["status"] == "pending"
    assert registry.check("Somerville", "9 Gorham St", book_page, 1500000)["status"] == "conflict"
    land_court = registry.check("Somerville", "62 Chandler St", registry.reference_pair("01631/16"), 2150000)
    assert land_court["office"] == "Registered Land (Land Court)"


@pytest.mark.parametrize(("address", "reference", "price"), [
    ("9 Gorham St", (80505, 62), 1650000),
    ("62 Chandler St", (1631, 16), 2150000),
])
def test_unreviewed_registered_reference_stays_pending(address, reference, price):
    deed = registry.check("Somerville", address, reference, price, "C-9999")

    assert deed["status"] == "pending"
    assert deed["registered_reference"] == "C-9999"
    assert "consideration" not in deed


def test_registration_reference_matches_only_reviewed_land_court_record(monkeypatch):
    address = "62 Chandler St"
    reference = (1631, 16)
    key = ("SOMERVILLE", registry.address_key(address), reference)
    monkeypatch.setitem(registry.VERIFIED, key, {**registry.VERIFIED[key], "registered_reference": "C-123"})
    assert registry.check("Somerville", address, reference, 2150000, " C-123 ")["status"] == "verified"
    assert registry.check("Somerville", address, reference, 2150000, "C-124")["status"] == "pending"
    assert registry.check("Somerville", address, reference, 2000000, "C-123")["status"] == "conflict"


@pytest.mark.parametrize("address", [
    "9 Gorham St, Unit 1, Somerville, MA",
    "9 Gorham St, Apt. 1",
    "9 Gorham St, #1, Somerville, MA",
    "9 Gorham St, Suite 1, Somerville, MA",
])
def test_comma_delimited_unit_does_not_inherit_building_deed(address):
    assert registry.check("Somerville", address, (80505, 62), 1650000)["status"] == "pending"


def test_exact_unit_deed_still_matches_across_address_formats(monkeypatch):
    address = "9 Gorham St, Unit 1, Somerville, MA"
    deed = {"address": address, "consideration": 500000}
    monkeypatch.setattr(registry, "VERIFIED", {("SOMERVILLE", registry.address_key(address), (10, 20)): deed})
    assert registry.check("Somerville", "9 Gorham Street#1", (10, 20), 500000)["status"] == "verified"
    assert registry.check("Somerville", "9 Gorham Street #2", (10, 20), 500000)["status"] == "pending"
    assert registry.check("Somerville", "9 Gorham Street", (10, 20), 500000)["status"] == "pending"
    assert registry.check("Somerville", "9 Gorham Street Apt 1, Unit 2", (10, 20), 500000)["status"] == "pending"


def test_pending_deeds_cannot_influence_sale_ranges():
    report = {"candidates": [
        {"address": "9 Jay St", "record_reference": "84631/516", "sale_price": 1430000, "price_per_living_sqft": 600},
        {"address": "9 Jay St, Unit 1", "record_reference": "84631/516", "sale_price": 1430000, "price_per_living_sqft": 900},
        {"address": "1 Unchecked St", "record_reference": "3/4", "sale_price": 2500000, "price_per_living_sqft": 2000},
    ], "observed_price_per_sqft_low": 600, "observed_price_per_sqft_high": 2000, "median_sale_price": 1965000}
    result = registry.sale_report("Somerville", report)
    assert [r["address"] for r in result["candidates"]] == ["9 Jay St"]
    assert result["pending_registry_review"] == [{
        "address": "9 Jay St, Unit 1", "deed_verification": registry.check("Somerville", "9 Jay St, Unit 1", (84631, 516)),
    }, {
        "address": "1 Unchecked St", "deed_verification": registry.check("Somerville", "1 Unchecked St", (3, 4)),
    }]
    assert result["observed_price_per_sqft_low"] is None and result["observed_price_per_sqft_high"] is None
    assert result["median_sale_price"] is None


def test_registry_queue_deduplicates_and_resolves_seeded_matches(tmp_path):
    def connect():
        db = sqlite3.connect(tmp_path / "review.db")
        db.row_factory = sqlite3.Row
        return db
    leads = [{"address": "1 Unchecked St", "deed_verification": registry.check("Somerville", "1 Unchecked St", (3, 4))},
             {"address": "9 Jay St", "deed_verification": {**registry.check("Somerville", "9 Jay St", (84631, 516)), "status": "pending"}}]
    leads.append({"address": "9 Gorham St", "deed_verification": {
        "status": "pending", "book": 80505, "page": 62, "registered_reference": "C-9999",
    }})
    registry.enqueue(connect, "Somerville", leads)
    registry.enqueue(connect, "Somerville", leads)
    pending = registry.review_queue(connect, 40)
    assert [row["address"] for row in pending] == ["9 Gorham St", "1 Unchecked St"]
    assert pending[0]["deed_verification"]["registered_reference"] == "C-9999"


@pytest.mark.parametrize(("registered_reference", "expected"), [(None, "verified"), ("C-9999", "pending")])
def test_property_endpoint_combines_local_zoning_research_and_registry_check(monkeypatch, registered_reference, expected):
    from unittest.mock import AsyncMock
    import httpx
    from fastapi.testclient import TestClient
    import app as server

    response = httpx.Response(200, request=httpx.Request("GET", server.CENSUS), json={
        "result": {"addressMatches": [{
            "matchedAddress": "9 GORHAM ST, SOMERVILLE, MA",
            "addressComponents": {"city": "Somerville", "state": "MA"},
            "coordinates": {"x": -71.125, "y": 42.4},
        }]},
    })

    class CensusClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            assert url == server.CENSUS
            return response

    parcel = {"parcel_number": "17-A-6", "assessor_site_address": "9 GORHAM ST",
              "last_transfer": {"book": "80505", "page": "0062", "price_reported": 1650000,
                                "registered_reference": registered_reference}}
    monkeypatch.setattr(server.httpx, "AsyncClient", CensusClient)
    monkeypatch.setattr(server, "resolve_parcel", AsyncMock(return_value=(parcel, {"use_description": "Multiple houses on one parcel"}, None)))
    monkeypatch.setattr(server.municipal_zoning, "somerville_parcel", AsyncMock(return_value=None))
    monkeypatch.setattr(server.municipal_zoning, "lookup", AsyncMock(return_value={"zoning": None, "review": None, "constraints": []}))
    monkeypatch.setattr(server.municipal_zoning, "multifamily_screen", AsyncMock(return_value={}))
    monkeypatch.setattr(server.somerville, "subject_building", AsyncMock(return_value=None))
    monkeypatch.setattr(server.somerville, "sales", AsyncMock(return_value={"candidates": []}))
    monkeypatch.setattr(server, "search_zoning_sources", AsyncMock(return_value={"status": "Sources located"}))
    response = TestClient(server.app).get("/api/property", params={"address": "9 Gorham St, Somerville, MA"})
    assert response.status_code == 200
    assert response.json()["on_demand_zoning_research"]["status"] == "Sources located"
    assert response.json()["parcel"]["deed_verification"]["status"] == expected
