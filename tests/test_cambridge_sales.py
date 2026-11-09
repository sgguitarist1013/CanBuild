from datetime import date, timedelta

import httpx
import pytest

import cambridge_sales
import registry


@pytest.mark.anyio
async def test_cambridge_city_feed_keeps_similar_sales_as_unpriced_deed_leads(monkeypatch):
    today = date.today()
    row = {
        "address": "10 Example St", "gisid": "99-10", "stateclasscode": "105",
        "saleprice": "1600000", "saledate": (today - timedelta(days=500)).isoformat(),
        "book_page": "83000/12", "interior_livingarea": "2100",
        "interior_bedrooms": "4", "interior_fullbaths": "3", "interior_halfbaths": "1",
    }

    def respond(request):
        assert "stateclasscode = '105'" in request.url.params["$where"]
        assert "between 1771 and 2395" in request.url.params["$where"]
        return httpx.Response(200, json=[
            dict(row, gisid="102-51"),
            dict(row, gisid="99-1", interior_livingarea=None),
            row,
            dict(row, gisid="99-2", interior_livingarea="2400"),
            dict(row, gisid="99-3", book_page="/"),
            dict(row, address="12 Example St", gisid="99-12", book_page="83000/15", interior_bedrooms="6"),
            dict(row, address="13 Example St", gisid="99-13", book_page="83000/16", interior_halfbaths=None),
            dict(row, address="11 Example St", gisid="99-11", book_page="81000/100",
                 saledate=(today - timedelta(days=1300)).isoformat()),
        ])

    async def attach_distances(client, candidates, lon, lat):
        for candidate in candidates:
            candidate["distance_miles"] = .8

    monkeypatch.setattr(cambridge_sales, "attach_cambridge_distances", attach_distances)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        report = await cambridge_sales.search(client, -71.11, 42.36, {"parcel_number": "102-51"},
            {"use_description": "Three-Family Residential", "residential_area_sqft": 2083,
             "bedrooms_reported": 3, "bathrooms_reported": 3})
    assert [r["layout_match"] for r in report["candidates"]] == ["similar", "different", "unknown", "similar"]
    assert report["candidates"][0]["bathrooms_reported"] == 3.5
    public = registry.sale_report("CAMBRIDGE", report)
    assert not public["candidates"]
    recent, different, unknown, historical = public["pending_registry_review"]
    assert recent["deed_verification"]["book"] == 83000
    assert recent["bedrooms_reported"] == 4
    assert different["layout_match"] == "different"
    assert unknown["layout_match"] == "unknown"
    assert historical["older_reference"]
    assert all("sale_price" not in lead for lead in public["pending_registry_review"])


@pytest.mark.anyio
@pytest.mark.parametrize(("lot", "address", "bedrooms"), [
    ("101-200", "95 Magazine St", 4),
    ("102-51", "106 Magazine St", 3),
])
async def test_subject_layout_requires_exact_parcel_and_street(lot, address, bedrooms):
    row = {"gisid": lot, "address": address, "interior_bedrooms": str(bedrooms),
           "interior_fullbaths": "3", "interior_halfbaths": "0"}
    response_rows = [row]

    def respond(request):
        assert request.url.params["$where"] == f"gisid = '{lot}' AND (unit IS NULL OR unit = '')"
        return httpx.Response(200, json=response_rows)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        parcel = {"parcel_number": lot, "assessor_site_address": address.upper().replace(" ST", " STREET")}
        layout = await cambridge_sales.subject_layout(client, parcel)
        assert layout == {"bedrooms_reported": bedrooms, "bathrooms_reported": 3,
                          "assessor_layout_source_url": cambridge_sales.CAMBRIDGE_ASSESSOR}
        assert await cambridge_sales.subject_layout(client, dict(parcel, assessor_site_address="999 Magazine St")) == {}
        assert await cambridge_sales.subject_layout(client, dict(parcel, assessor_site_address=f"{address} Unit 1")) == {}
        response_rows.append(dict(row))
        assert await cambridge_sales.subject_layout(client, parcel) == {}


def test_room_mismatches_do_not_stop_registry_shortlist(monkeypatch):
    rows = [
        {"address": str(i), "sale_date": "2025-01-01", "distance_miles": .1, "layout_match": "different"}
        for i in range(3)
    ]
    nearby = {"address": "similar", "sale_date": "2025-01-01", "distance_miles": .3, "layout_match": "similar"}
    monkeypatch.setattr(registry, "sale_deed", lambda city, row: {
        "status": "pending" if row["address"] == "similar" else "verified",
    })
    assert nearby in registry.shortlist("CAMBRIDGE", rows + [nearby])
