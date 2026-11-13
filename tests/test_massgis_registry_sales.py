import asyncio
from datetime import date, timedelta

import httpx
import pytest

import registry
from condo_analysis import condo_sales
from ma_sales import search


@pytest.mark.parametrize("flow", ["property", "condo", "new-build"])
@pytest.mark.parametrize("has_book_page", [True, False])
def test_unreviewed_massgis_registration_cannot_verify_comparable(flow, has_book_page, monkeypatch):
    address = "9 Example St Unit 2"
    book_page = (12345, 6)
    monkeypatch.setitem(registry.VERIFIED, ("CAMBRIDGE", registry.address_key(address), book_page), {
        "office": "Recorded Land", "consideration": 800000,
    })
    sold = date.today() - timedelta(days=10)
    attributes = {
        "SITE_ADDR": address, "CITY": "CAMBRIDGE", "LOC_ID": "candidate", "PROP_ID": "tax-account",
        "MAP_PAR_ID": "candidate", "LS_DATE": sold.strftime("%Y%m%d"), "LS_PRICE": 800000,
        "LS_BOOK": str(book_page[0]) if has_book_page else "CERT",
        "LS_PAGE": str(book_page[1]) if has_book_page else "9999",
        "REG_ID": "C-9999",
        "RES_AREA": 1000, "BLD_AREA": 2000, "YEAR_BUILT": sold.year, "USE_DESC": "Residential Condo",
    }

    def respond(request):
        assert "REG_ID" in request.url.params["outFields"].split(",")
        return httpx.Response(200, json={"features": [
            {"attributes": attributes, "centroid": {"x": -71.10, "y": 42.35}},
        ]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            if flow == "property":
                return await search(client, -71.10, 42.35, "CAMBRIDGE", {"loc_id": "subject"}, {
                    "residential_area_sqft": 1000, "use_description": "Residential Condo",
                })
            return await condo_sales(client, -71.10, 42.35, "CAMBRIDGE", new_build_only=flow == "new-build")

    result = registry.sale_report("Cambridge", asyncio.run(run()))

    assert result["candidates"] == []
    assert result["count"] == 0
    evidence = result["pending_registry_review"][0]["deed_verification"]
    assert evidence["status"] == "pending"
    assert evidence["registered_reference"] == "C-9999"
    assert "sale_price" not in result["pending_registry_review"][0]
    assert result.get("observed_price_per_sqft_low") is None
    assert result.get("median_sale_price") is None
