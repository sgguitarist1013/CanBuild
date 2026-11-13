from datetime import date
from math import cos, log, pi, radians, tan

import httpx
import pytest
from shapely.geometry import LineString, box

from somerville import buildout, detached_triple_decker_fit, sale_candidates, sales


SUBJECT = {"use_description": "Multiple Houses on one parcel", "residential_area_sqft": 2722,
           "bedrooms_reported": 5, "bathrooms_reported": 2, "year_built": 1910}
PARCEL = {"parcel_number": "17-A-6", "gis_area_sqft_approx": 4996}


def feature(pid, dx, **fields):
    record = {"MBL": pid, "TaxParMBL": pid, "PCC": "1040", "SALE_VALID": "Q", "SALE_PRICE": 1300000,
              "SALE_DATE": "2026-09-08", "LIVING_AREA": 2600, "BEDROOM": 5, "BATH": 2,
              "AddNum": "14", "Street": "TEST ST"}
    lon, lat = -71.125 + dx, 42.4
    half_lon, half_lat = .00013, .000097
    return {"attributes": {**record, **fields}, "centroid": {"x": lon, "y": lat},
            "geometry": {"rings": [[[lon-half_lon,lat-half_lat],[lon+half_lon,lat-half_lat],
                                      [lon+half_lon,lat+half_lat],[lon-half_lon,lat+half_lat],
                                      [lon-half_lon,lat-half_lat]]]}}


def test_nr_screen_uses_real_lot_area_and_distinguishes_unit_options_from_yield():
    result = buildout(PARCEL, SUBJECT)
    assert result["lot_area_sqft"] == 4996
    assert "1–3" in result["district_unit_range"]
    assert "one to three homes" in result["summary"]
    assert result["existing_units"] is None
    assert any("multiple houses" in check for check in result["checks"])
    assert any("demolition" in check for check in result["checks"])


def test_nr_triple_decker_screen_applies_adopted_setbacks_and_lot_coverage():
    radius_ft = 6_378_137 * 3.280839895
    lat = 42.4
    lon = -71.125
    delta_lon = 20 / (radius_ft * cos(radians(lat))) * 180 / 3.141592653589793
    frontage_lon = lon + delta_lon
    frontage_lat = lat + 2 / radius_ft * 180 / 3.141592653589793
    x = radius_ft * radians(lon) * cos(radians(lat))
    y = radius_ft * log(tan(pi / 4 + radians(lat) / 2)) * cos(radians(lat))
    parcel = {"gis_area_sqft_approx": 5400, "polygon_area_sqft_approx": 5400}
    result = detached_triple_decker_fit(parcel, box(x, y, x + 60, y + 90), {"lon": frontage_lon, "lat": frontage_lat})
    assert result["status"] == "dimensional_screen_pass"
    assert result["lot_frontage_ft_approx"] == 60
    assert result["lot_depth_ft_approx"] == 90
    assert result["max_principal_footprint_sqft"] == 1500
    assert result["gross_area_high_sqft"] == 4500


def test_named_street_centerline_resolves_frontage_when_address_point_is_inside_lot():
    radius_ft = 6_378_137 * 3.280839895
    lat = 42.4
    lon = -71.125
    x = radius_ft * radians(lon) * cos(radians(lat))
    y = radius_ft * log(tan(pi / 4 + radians(lat) / 2)) * cos(radians(lat))
    parcel = {"gis_area_sqft_approx": 5400, "polygon_area_sqft_approx": 5400}
    road = {"status": "matched", "street_name": "EXAMPLE STREET",
            "geometry": LineString([(x - 10, y - 26), (x + 70, y - 26)])}
    result = detached_triple_decker_fit(
        parcel, box(x, y, x + 60, y + 90), {"lon": lon, "lat": lat}, road,
    )
    assert result["status"] == "dimensional_screen_pass"
    assert result["frontage_match"] == "matched named municipal road centerline"
    assert result["frontage_source"] == "EXAMPLE STREET"


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
