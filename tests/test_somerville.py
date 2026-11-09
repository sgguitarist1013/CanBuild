from datetime import date

import httpx
import pytest

from somerville import buildout, sale_candidates, sales


SUBJECT = {"use_description": "Multiple Houses on one parcel", "residential_area_sqft": 2722,
           "bedrooms_reported": 5, "bathrooms_reported": 2, "year_built": 1910}
PARCEL = {"parcel_number": "17-A-6", "gis_area_sqft_approx": 4996}


def feature(pid, dx, **fields):
    record = {"MBL": pid, "TaxParMBL": pid, "PCC": "1040", "SALE_VALID": "Q", "SALE_PRICE": 1300000,
              "SALE_DATE": "2026-09-08", "LIVING_AREA": 2600, "BEDROOM": 5, "BATH": 2,
              "AddNum": "14", "Street": "TEST ST"}
    return {"attributes": {**record, **fields}, "centroid": {"x": -71.125 + dx, "y": 42.4}}


def test_nr_screen_uses_real_lot_area_and_distinguishes_unit_options_from_yield():
    result = buildout(PARCEL, SUBJECT)
    assert result["lot_area_sqft"] == 4996
    assert "1–3" in result["district_unit_range"]
    assert result["existing_units"] is None
    assert any("multiple houses" in check for check in result["checks"])
    assert any("demolition" in check for check in result["checks"])


def test_comp_match_preserves_size_rooms_and_distinguishes_assessor_class():
    records = sale_candidates([
        feature("17-A-6", .001), feature("size", .001, LIVING_AREA=3200),
        feature("beds", .001, BEDROOM=3), feature("baths", .001, BATH=4),
        feature("missing", .001, BATH=None), feature("invalid", .001, SALE_VALID="U"),
        feature("exact", .002, PCC="1090"), feature("similar", .003),
    ], -71.125, 42.4, PARCEL, SUBJECT, date(2026, 11, 7))
    assert [r["parcel_number"] for r in records] == ["exact", "similar"]
    assert records[0]["comparison_label"] == "Same assessor class"
    assert "different assessor class" in records[1]["comparison_label"]


@pytest.mark.anyio
@pytest.mark.parametrize("initial,expected_queries", [(3, 1), (2, 2)])
async def test_sales_expand_only_until_three_matches(initial, expected_queries):
    close = [feature(str(i), .001 * i) for i in range(1, initial+1)]
    farther = [feature(str(i+10), .015 + .004*i) for i in range(3)]
    queries = []
    def handler(request):
        queries.append(request.url)
        has_geometry = "geometry" in request.url.params
        return httpx.Response(200, json={"features": close if has_geometry else [*close, *farther]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await sales(client, -71.125, 42.4, PARCEL, SUBJECT)
    assert len(queries) == expected_queries
    assert len(result["candidates"]) == 3
    assert result["expanded"] == (expected_queries == 2)
    assert (result["radius_miles"] > .5) == (expected_queries == 2)
