"""Somerville Neighborhood Residence screening and assessor sale references."""
from datetime import date, timedelta
from math import asin, ceil, cos, floor, radians, sin, sqrt

import httpx

import boston
from ma_sales import candidate_point, use_group
from municipal_zoning import SOMERVILLE

GIS = SOMERVILLE + "/ZoningByParcel/MapServer/0"
NR_TYPES = "https://online.encodeplus.com/regs/somerville-ma/doc-view.aspx?pn=0&ajax=0&secid=470"
NR_PURPOSE = "https://online.encodeplus.com/regs/somerville-ma/doc-view.aspx?pn=0&ajax=0&secid=467"
NR_STANDARDS = "https://online.encodeplus.com/regs/somerville-ma/doc-view.aspx?pn=0&ajax=0&secid=475"
DEMOLITION = "https://www.somervillema.gov/citizenserve"
CLASSES = {"1010": "Single-family", "1040": "Two-family", "1050": "Three-family",
           "1090": "Multiple houses on one parcel"}
CODES = {"single family": "1010", "two family": "1040", "three family": "1050"}
SIZE_TOLERANCE = .15
INITIAL_RADIUS = .5
MINIMUM_COMPS = 3


def assessor_class(building):
    use = str((building or {}).get("use_description") or "").lower()
    if "multiple houses" in use or "multi hses" in use:
        return "1090"
    return CODES.get(use_group(use))


def parcel_area_sqft(feature):
    """Calculate a screening lot area from the selected parcel polygon geometry."""
    try:
        from shapely.ops import transform
        from municipal_zoning import esri_polygon
        polygon = esri_polygon(feature.get("geometry"))
        if polygon.is_empty or polygon.geom_type not in {"Polygon", "MultiPolygon"}:
            return None
        latitude = radians(polygon.centroid.y)
        radius_m = 6_371_008.8
        projected = transform(lambda x, y, z=None: (radius_m * radians(x) * cos(latitude),
                                                     radius_m * radians(y)), polygon)
        area = projected.area * 10.7639104167
        return round(area) if area > 0 else None
    except (TypeError, ValueError, AttributeError):
        return None


def buildout(parcel, building):
    building = building or {}
    multi_house = assessor_class(building) == "1090"
    year = building.get("year_built")
    checks = ["Confirm measured frontage, setbacks, lot coverage, and building placement.",
              "Check overlays, current amendments, and any affordability requirements for the selected building type."]
    if multi_house:
        checks.insert(0, "The assessor reports multiple houses. Verify the legal building and dwelling count and any nonconforming rights before assuming what can be retained or replaced.")
    if isinstance(year, int) and 0 < year <= date.today().year - 75:
        checks.append("The reported construction year triggers an age check for demolition review. Confirm the building age and review process with Somerville before assuming demolition.")
    return {
        "title": "Neighborhood Residence buildout screen",
        "summary": "Screen a detached house, duplex, or detached triple-decker. Somerville’s NR code describes one to three homes in the principal residential building, depending on the building type.",
        "district_unit_range": "1–3 principal-building homes",
        "lot_area_sqft": parcel.get("gis_area_sqft_approx"),
        "existing_units": building.get("units"),
        "checks": checks,
        "accessory_option": "The district also lists a backyard cottage as an accessory building type. Check accessory dwelling eligibility and site fit separately.",
        "limit": "The unit range describes base district building types. Parcel fit, net new units, and buildable floor area need a site and permit review.",
        "sources": [{"name": "Somerville §3.1.3 · NR purpose", "url": NR_PURPOSE},
                    {"name": "Somerville §3.1.6 · NR building types", "url": NR_TYPES},
                    {"name": "Somerville §3.1.17 · Development standards", "url": NR_STANDARDS},
                    {"name": "City permit and demolition review", "url": DEMOLITION}],
    }


async def query(client, params):
    response = await client.get(GIS + "/query", params={"f": "json", **params}, timeout=httpx.Timeout(12, connect=4))
    response.raise_for_status()
    data = response.json()
    if data.get("error") or not isinstance(data.get("features"), list):
        raise ValueError("Somerville GIS query unavailable")
    return data


async def subject_building(client, parcel_number):
    code = str(parcel_number).replace("'", "''")
    data = await query(client, {"where": f"MBL = '{code}' AND PolyType = 'PARCEL'",
        "outFields": "MBL,TaxParMBL,PCC,LIVING_AREA,GROSS_AREA,YEAR,STORIES,BEDROOM,BATH",
        "returnGeometry": "false", "resultRecordCount": 2})
    rows = [row["attributes"] for row in data["features"] if row.get("attributes", {}).get("MBL") == parcel_number]
    if len(rows) != 1 or rows[0].get("TaxParMBL") != parcel_number or data.get("exceededTransferLimit"):
        return None
    row = rows[0]
    return {"residential_area_sqft": row.get("LIVING_AREA"), "building_area_sqft": row.get("GROSS_AREA"),
            "use_description": CLASSES.get(row.get("PCC")), "year_built": row.get("YEAR"), "stories": row.get("STORIES"),
            "bedrooms_reported": row.get("BEDROOM"), "bathrooms_reported": row.get("BATH")}


def sale_candidates(features, lon, lat, parcel, building, today):
    subject_class = assessor_class(building)
    living = building.get("residential_area_sqft")
    subject_area = living or building.get("building_area_sqft")
    area_field = "LIVING_AREA" if living else "GROSS_AREA"
    area_basis = "assessor living area" if living else "assessor gross building area"
    subject_beds, subject_baths = building["bedrooms_reported"], building["bathrooms_reported"]
    rows = {}
    for feature in features:
        row = feature.get("attributes") or {}
        pid, code = row.get("MBL"), row.get("PCC")
        if not pid or pid != row.get("TaxParMBL") or pid == parcel.get("parcel_number") or row.get("SALE_VALID") != "Q":
            continue
        sold, price, size = boston.sale_date(row.get("SALE_DATE")), row.get("SALE_PRICE"), row.get(area_field)
        beds, baths = row.get("BEDROOM"), row.get("BATH")
        if (not sold or not 0 <= (today - sold).days <= 1096 or not row.get("AddNum") or not row.get("Street")
                or not isinstance(price, (int, float)) or price < 10000
                or not isinstance(size, (int, float)) or abs(size - subject_area) > SIZE_TOLERANCE * subject_area
                or not isinstance(beds, (int, float)) or beds <= 0 or abs(beds - subject_beds) > 1
                or not isinstance(baths, (int, float)) or baths <= 0 or abs(baths - subject_baths) > 1):
            continue
        point = candidate_point(feature)
        if not point:
            continue
        dx, dy = radians(point[0] - lon), radians(point[1] - lat)
        hav = sin(dy / 2)**2 + cos(radians(lat)) * cos(radians(point[1])) * sin(dx / 2)**2
        distance = 2 * 3958.8 * asin(sqrt(min(1, hav)))
        lot_area = parcel_area_sqft(feature)
        subject_lot = parcel.get("gis_area_sqft_approx")
        if isinstance(subject_lot, (int, float)) and subject_lot > 0:
            if not isinstance(lot_area, (int, float)) or abs(lot_area-subject_lot) > .30*subject_lot:
                continue
        record = {"address": f"{row['AddNum']} {row['Street']}", "sale_date": sold.isoformat(), "sale_price": price,
            "parcel_number": pid, "use_description": CLASSES[code], "comparison_area_sqft": size,
            "comparison_area_basis": area_basis, "price_per_comparison_sqft": round(price / size),
            "distance_miles": distance, "size_difference_pct": round((size - subject_area) / subject_area * 100),
            "bedrooms_reported": beds, "bathrooms_reported": baths,
            "lot_area_sqft": lot_area, "record_reference": row.get("BOOK_PAGE"),
            "source_url": GIS, "source_name": "Somerville municipal GIS sales", "arm_length_status": "City sale flag Q",
            "comparison_label": "Same assessor class" if code == subject_class else "Similar residential sale · different assessor class"}
        if pid not in rows or rows[pid]["sale_date"] < record["sale_date"]:
            rows[pid] = record
    return sorted(rows.values(), key=lambda item: (item["distance_miles"], abs(item["size_difference_pct"])))


async def sales(client, lon, lat, parcel, building):
    building = building or {}
    code = assessor_class(building)
    living = building.get("residential_area_sqft")
    area = living or building.get("building_area_sqft")
    beds, baths = building.get("bedrooms_reported"), building.get("bathrooms_reported")
    if not code or any(not isinstance(value, (int, float)) or value <= 0 for value in (area, beds, baths)):
        return {"status": "Size-matched sales require reported area, bedrooms and bathrooms", "candidates": [], "source_url": GIS}
    today = date.today()
    cutoff = (today - timedelta(days=1096)).isoformat()
    codes = "'1010','1040','1050','1090'"
    area_field = "LIVING_AREA" if living else "GROSS_AREA"
    low, high = ceil(area * (1 - SIZE_TOLERANCE)), floor(area * (1 + SIZE_TOLERANCE))
    params = {
        "where": (f"SALE_VALID = 'Q' AND SALE_PRICE >= 10000 AND SALE_DATE >= timestamp '{cutoff} 00:00:00'"
                  f" AND PCC IN ({codes}) AND {area_field} BETWEEN {low} AND {high}"
                  f" AND BEDROOM BETWEEN {max(1, beds-1)} AND {beds+1} AND BATH BETWEEN {max(1, baths-1)} AND {baths+1}"),
        "outSR": 4326,
        "outFields": "MBL,TaxParMBL,AddNum,Street,PCC,SALE_VALID,SALE_PRICE,SALE_DATE,LIVING_AREA,GROSS_AREA,BEDROOM,BATH,BOOK_PAGE",
        "returnGeometry": "true", "geometryPrecision": 6, "resultRecordCount": 500, "orderByFields": "SALE_DATE DESC",
    }
    # A bounding envelope contains the initial radius. Keep only true centroid distances.
    data = await query(client, {**params, "geometry": f"{lon-.011},{lat-.008},{lon+.011},{lat+.008}",
        "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects"})
    records = sale_candidates(data["features"], lon, lat, parcel, building, today)
    matches = [record for record in records if record["distance_miles"] <= INITIAL_RADIUS]
    expanded = len(matches) < MINIMUM_COMPS
    if expanded:
        data = await query(client, params)
        matches = sale_candidates(data["features"], lon, lat, parcel, building, today)[:MINIMUM_COMPS]
    else:
        matches = matches[:4]
    radius = max(INITIAL_RADIUS, matches[-1]["distance_miles"]) if matches else INITIAL_RADIUS
    for record in matches:
        record["distance_miles"] = round(record["distance_miles"], 3)
    exact_count = sum(record["comparison_label"] == "Same assessor class" for record in matches)
    return {"status": f"{len(matches)} local sale reference(s) matched on size, beds and baths" if matches else "No local sales matched the size and room criteria",
            "candidates": matches, "count": len(matches), "same_class_count": exact_count, "source_url": GIS,
            "expanded": expanded, "radius_miles": round(radius, 3), "truncated": bool(data.get("exceededTransferLimit")),
            "method": "Somerville only. Within 15% of subject living or gross area using the same measurement, within one bedroom and one bathroom, lot within 30% when parcel geometry is available, and city sale flag Q in the last three years. Start at 0.5 mile and, if needed, return the three nearest matches in the municipality. Exact assessor class is not required.",
            "limitations": "Assessor counts, recorded sales and building areas require verification. These references do not establish market value or completed-project sellout."}
