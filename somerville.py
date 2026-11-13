"""Somerville Neighborhood Residence screening and assessor sale references."""
from datetime import date, timedelta
from math import acos, asin, ceil, cos, degrees, floor, log, pi, radians, sin, sqrt, tan

import httpx

import boston
from ma_sales import candidate_point, use_group
from parcel_engine import street_tokens
from municipal_zoning import SOMERVILLE

GIS = SOMERVILLE + "/ZoningByParcel/MapServer/0"
MASSGIS_ROADS = "https://services1.arcgis.com/hGdibHYSPO59RG1h/ArcGIS/rest/services/MassGIS_Roads_Hosted_Feature_Layer/FeatureServer/0"
NR_TYPES = "https://online.encodeplus.com/regs/somerville-ma/doc-view.aspx?pn=0&ajax=0&secid=470"
NR_PURPOSE = "https://online.encodeplus.com/regs/somerville-ma/doc-view.aspx?pn=0&ajax=0&secid=467"
NR_STANDARDS = "https://online.encodeplus.com/regs/somerville-ma/doc-view.aspx?pn=0&ajax=0&secid=475"
TRIPLE_DECKER_ORDINANCE = "https://somervillema.legistar.com/LegislationDetail.aspx?GUID=EA73CB27-5E5D-41C9-B862-53E1E2FA3F43&ID=6366045"
TRIPLE_DECKER_DIMENSIONAL_EXAMPLE = "https://s3.amazonaws.com/somervillema-live/s3fs-public/2026-03/17-Hudson-Somerville-MAAB-Variance-Submittal-1-28-26-1.pdf"
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


async def street_frontage(client, polygon, latitude, street_line):
    """Fetch the named state road centerline near this parcel for frontage matching."""
    if polygon is None or polygon.is_empty or not isinstance(latitude, (int, float)) or not street_line:
        return {"status": "unavailable", "reason": "Parcel, latitude, or requested street is missing."}
    requested_street = str(street_line).split(maxsplit=1)
    if len(requested_street) != 2:
        return {"status": "unavailable", "reason": "Requested street name could not be parsed."}
    standard_name = " ".join(street_tokens(requested_street[1]))
    safe_name = standard_name.replace("'", "''")
    scale = 3.280839895 * cos(radians(latitude))
    # The parcel is local ground feet; the MassGIS roads service is EPSG:3857 meters.
    margin_m = 100 / scale
    xmin, ymin, xmax, ymax = polygon.bounds
    bounds = (xmin / scale - margin_m, ymin / scale - margin_m,
              xmax / scale + margin_m, ymax / scale + margin_m)
    response = await client.get(MASSGIS_ROADS + "/query", params={
        "f": "json", "where": f"UPPER(ST_NAME_STD) = '{safe_name}'",
        "geometry": ",".join(str(value) for value in bounds),
        "geometryType": "esriGeometryEnvelope", "inSR": 3857, "outSR": 3857,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "OBJECTID,ST_NAME_STD,ROAD_CLASS,MUNI_L,MUNI_R",
        "returnGeometry": "true", "resultRecordCount": 100,
    }, timeout=httpx.Timeout(8, connect=3))
    response.raise_for_status()
    data = response.json()
    if (data.get("error") or data.get("exceededTransferLimit")
            or not isinstance(data.get("features"), list)):
        return {"status": "unavailable", "reason": "MassGIS road response was incomplete."}
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    roads = []
    for feature in data["features"]:
        attrs = feature.get("attributes") or {}
        if (" ".join(street_tokens(attrs.get("ST_NAME_STD"))) != standard_name
                or not {attrs.get("MUNI_L"), attrs.get("MUNI_R")} & {"SOMERVILLE"}):
            continue
        for path in (feature.get("geometry") or {}).get("paths", []):
            if len(path) >= 2:
                roads.append(LineString([(x * scale, y * scale) for x, y in path]))
    if not roads:
        return {"status": "unavailable", "reason": "No matching Somerville road centerline was returned."}
    return {"status": "matched", "street_name": standard_name,
            "geometry": unary_union(roads), "source_url": MASSGIS_ROADS}


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


def detached_triple_decker_fit(parcel, polygon, coordinates, frontage_road=None):
    """Calculate a conservative GIS fit for the adopted NR triple-decker envelope."""
    if polygon is None or polygon.is_empty or not coordinates:
        return {"status": "unavailable", "reason": "Selected parcel polygon or address point is missing."}
    rectangle = polygon.minimum_rotated_rectangle
    vertices = list(rectangle.exterior.coords)
    edges = []
    for start, end in zip(vertices, vertices[1:]):
        from shapely.geometry import LineString
        line = LineString([start, end])
        edges.append((line.length, line))
    if len(edges) != 4 or min(length for length, _ in edges) <= 0:
        return {"status": "unavailable", "reason": "Parcel polygon does not support a stable dimension screen."}

    # Parcel geometry is projected in local feet. Convert the geocoded point to
    # the same local projection and use its nearest rectangle edge as frontage.
    from shapely.geometry import Point
    lon, lat = coordinates.get("lon"), coordinates.get("lat")
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        return {"status": "unavailable", "reason": "Geocoded parcel point is missing."}
    # Parcel geometry is transformed from EPSG:3857, whose sphere is 6,378,137 m.
    radius_ft = 6_378_137 * 3.280839895
    latitude = radians(lat)
    scale = cos(latitude)
    point = Point(
        radius_ft * radians(lon) * scale,
        radius_ft * log(tan(pi / 4 + latitude / 2)) * scale,
    )
    sorted_edges = sorted(edges, key=lambda row: point.distance(row[1]))
    frontage_ft, front_line = sorted_edges[0]
    # A rectangle's frontage is the length of the nearest edge; lot depth is
    # the perpendicular edge length. Do not sort these by size: corner lots
    # often have a shorter frontage than depth.
    other_dimensions = [length for length, _ in edges if abs(length - frontage_ft) > 1]
    if not other_dimensions:
        return {"status": "unavailable", "reason": "Parcel dimensions are too irregular to infer frontage from the address point."}
    depth_ft = other_dimensions[0]
    frontage_distance = point.distance(front_line)
    next_distance = point.distance(sorted_edges[1][1])
    frontage_status = "address point near one parcel edge" if frontage_distance <= 20 and next_distance - frontage_distance >= 8 else "frontage edge ambiguous"
    frontage_source = "address point"
    if isinstance(frontage_road, dict) and frontage_road.get("status") == "matched":
        from shapely.geometry import LineString
        road = frontage_road.get("geometry")
        road_segments = []
        for part in ([road] if road and road.geom_type == "LineString" else list(road.geoms) if road else []):
            coords = list(part.coords)
            road_segments.extend(LineString([a, b]) for a, b in zip(coords, coords[1:]) if a != b)
        frontage_candidates = []
        for index, (length, edge) in enumerate(edges):
            a, b = list(edge.coords)
            ev = (b[0] - a[0], b[1] - a[1])
            en = sqrt(ev[0] ** 2 + ev[1] ** 2)
            for segment in road_segments:
                c, d = list(segment.coords)
                rv = (d[0] - c[0], d[1] - c[1])
                rn = sqrt(rv[0] ** 2 + rv[1] ** 2)
                if not en or not rn:
                    continue
                alignment = abs((ev[0] * rv[0] + ev[1] * rv[1]) / (en * rn))
                angle = degrees(acos(min(1.0, alignment)))
                if angle > 15:
                    continue
                distance = edge.distance(segment)
                overlap = edge.intersection(segment.buffer(40)).length
                frontage_candidates.append((distance, -overlap, index, length, angle, overlap))
        frontage_candidates.sort()
        if frontage_candidates:
            best = frontage_candidates[0]
            second_edge = next((candidate for candidate in frontage_candidates[1:] if candidate[2] != best[2]), None)
            if (best[0] <= 80 and best[5] >= min(20, best[3] * .4)
                    and (second_edge is None or second_edge[0] - best[0] >= 10)):
                frontage_distance, _, edge_index, frontage_ft, road_angle, road_overlap = best
                front_line = edges[edge_index][1]
                depth_ft = next((length for i, (length, _) in enumerate(edges)
                                 if abs(length - frontage_ft) > 1), depth_ft)
                frontage_status = "matched named municipal road centerline"
                frontage_source = frontage_road.get("street_name") or frontage_source
                next_distance = second_edge[0] if second_edge else None
            else:
                frontage_status = "named road centerline does not identify one parcel edge"

    # Assume access from the front lot line and apply the triple-decker side
    # setback sum (12 ft), 10 ft front setback, and 20 ft rear setback. This
    # intentionally excludes accessory buildings and does not infer a survey.
    max_width = min(30.0, frontage_ft - 12.0)
    max_depth = min(50.0, depth_ft - 30.0)
    min_width, min_depth = 24.0, 36.0
    lot_area = parcel.get("polygon_area_sqft_approx") or parcel.get("gis_area_sqft_approx")
    coverage_cap = lot_area * .60 if isinstance(lot_area, (int, float)) else None
    footprint = min(max_width * max_depth, coverage_cap) if coverage_cap is not None else max_width * max_depth
    if frontage_status not in {"address point near one parcel edge", "matched named municipal road centerline"}:
        return {"status": "review", "reason": "The geocoder point does not establish which parcel edge is the street frontage.",
                "lot_frontage_ft_approx": round(frontage_ft, 1), "lot_depth_ft_approx": round(depth_ft, 1),
                "frontage_match": frontage_status,
                "frontage_source": frontage_source,
                "nearest_edge_distance_ft": round(frontage_distance, 1),
                "next_edge_distance_ft": round(next_distance, 1) if next_distance is not None else None}
    if max_width < min_width or max_depth < min_depth or footprint < min_width * min_depth:
        return {"status": "no_fit_under_screened_standards", "reason": "The mapped parcel dimensions do not fit the minimum detached triple-decker massing under the screened setbacks.",
                "lot_frontage_ft_approx": round(frontage_ft, 1), "lot_depth_ft_approx": round(depth_ft, 1),
                "frontage_match": frontage_status}
    gross_max = round(footprint * 3)
    gross_min = min(gross_max, round(min_width * min_depth * 3))
    return {"status": "dimensional_screen_pass", "lot_frontage_ft_approx": round(frontage_ft, 1),
            "lot_depth_ft_approx": round(depth_ft, 1), "frontage_match": frontage_status,
            "frontage_source": frontage_source,
            "max_principal_footprint_sqft": round(footprint), "gross_area_low_sqft": gross_min,
            "gross_area_high_sqft": gross_max, "stories": 3,
            "method": "Minimum rotated parcel rectangle; named MassGIS road centerline matched to the frontage edge; front 10 ft, rear 20 ft, side setbacks totaling 12 ft, max 30 ft building width, max 50 ft building depth, three stories, and 60% lot-coverage cap.",
            "limitations": "Screening geometry, not a boundary survey or zoning determination. Assumes frontage-driveway side-setback standard, excludes an accessory cottage, existing accessory footprints, Green Score, easements, historic review, and the final design's principal entrance and component rules. Confirm with a survey and Somerville Planning before relying on it."}


def buildout(parcel, building, polygon=None, coordinates=None, frontage_road=None):
    building = building or {}
    multi_house = assessor_class(building) == "1090"
    year = building.get("year_built")
    checks = ["Confirm measured frontage, setbacks, lot coverage, and building placement.",
              "Check overlays, current amendments, and any affordability requirements for the selected building type."]
    if multi_house:
        checks.insert(0, "The assessor reports multiple houses. Verify the legal building and dwelling count and any nonconforming rights before assuming what can be retained or replaced.")
    if isinstance(year, int) and 0 < year <= date.today().year - 75:
        checks.append("The reported construction year triggers an age check for demolition review. Confirm the building age and review process with Somerville before assuming demolition.")
    lot_area = parcel.get("gis_area_sqft_approx")
    lot_coverage_ceiling = round(lot_area * .60) if isinstance(lot_area, (int, float)) and lot_area > 0 else None
    triple_decker_max_footprint = min(30 * 50, lot_coverage_ceiling) if lot_coverage_ceiling else 30 * 50
    triple_decker_gross_ceiling = triple_decker_max_footprint * 3
    site_fit = detached_triple_decker_fit(parcel, polygon, coordinates, frontage_road)
    return {
        "title": "Neighborhood Residence buildout screen",
        "summary": "The NR district allows principal-building types with one to three homes. The adopted ordinance permits a detached triple-decker without the former lot-depth, dwelling-unit-per-lot, or development-benefit conditions. A triple-decker accommodates two or three vertically stacked homes; site placement and demolition review remain separate checks.",
        "district_unit_range": "1–3 principal-building homes",
        "adopted_principal_building_scenario": {
            "building_type": "Detached triple-decker",
            "principal_units_low": 2,
            "principal_units_high": 3,
            "maximum_stories": 3,
            "building_width_ft": {"min": 24, "max": 30},
            "building_depth_ft": {"min": 36, "max": 50},
            "lot_coverage_max_pct": 60,
            "maximum_principal_footprint_sqft_before_setbacks": triple_decker_max_footprint,
            "maximum_principal_gross_area_sqft_before_setbacks": triple_decker_gross_ceiling,
            "setbacks_ft": {"primary_front_min": 10, "primary_front_max": 20,
                "side_min": 3, "side_sum_min_with_front_driveway": 12, "rear_min": 20},
            "green_score_minimum": 0.35,
            "site_fit": "Not confirmed: frontage, driveway access, parcel-specific setback geometry, building separation, Green Score, and current survey must be checked before treating the dimensional ceiling as a feasible footprint.",
        },
        "parcel_dimensional_screen": site_fit,
        "adopted_rule_basis": {
            "status": "adopted ordinance and current city dimensional submittal reviewed",
            "ordinance_enactment_date": "2023-11-21",
            "ordinance_effect": "Detached triple-decker permitted without conditions; lot-depth, units-per-lot and development-benefit conditions repealed.",
            "dimensional_reference": "Somerville NR detached triple-decker standards, corroborated by a City-hosted 2026 zoning dimensional table.",
        },
        "lot_area_sqft": parcel.get("gis_area_sqft_approx"),
        "existing_units": building.get("units"),
        "checks": checks,
        "accessory_option": "The district also lists a backyard cottage as an accessory building type. Check accessory dwelling eligibility and site fit separately.",
        "limit": "The unit range describes base district building types. Parcel fit, net new units, and buildable floor area need a site and permit review.",
        "sources": [{"name": "Somerville §3.1.3 · NR purpose", "url": NR_PURPOSE},
                    {"name": "Somerville §3.1.6 · NR building types", "url": NR_TYPES},
                    {"name": "Somerville §3.1.17 · Development standards", "url": NR_STANDARDS},
                    {"name": "Somerville Ordinance 23-1198 · three units by right", "url": TRIPLE_DECKER_ORDINANCE},
                    {"name": "City-hosted 2026 NR triple-decker dimensional table", "url": TRIPLE_DECKER_DIMENSIONAL_EXAMPLE},
                    {"name": "MassGIS Roads · named road centerlines", "url": MASSGIS_ROADS},
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
