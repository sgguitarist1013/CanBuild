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
            dict(row, address="11 Example St", gisid="99-11", book_page="81000/100",
                 saledate=(today - timedelta(days=1300)).isoformat()),
        ])

    async def attach_distances(client, candidates, lon, lat):
        for candidate in candidates:
            candidate["distance_miles"] = .8

    monkeypatch.setattr(cambridge_sales, "attach_cambridge_distances", attach_distances)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        report = await cambridge_sales.search(client, -71.11, 42.36, {"parcel_number": "102-51"},
            {"use_description": "Three-Family Residential", "residential_area_sqft": 2083})
    assert [r["address"] for r in report["candidates"]] == ["10 Example St", "11 Example St"]
    public = registry.sale_report("CAMBRIDGE", report)
    assert not public["candidates"]
    recent, historical = public["pending_registry_review"]
    assert recent["deed_verification"]["book"] == 83000
    assert historical["older_reference"]
    assert all("sale_price" not in lead for lead in (recent, historical))
