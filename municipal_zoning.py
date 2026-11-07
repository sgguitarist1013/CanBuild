"""Municipal zoning snapshots matched against the selected parcel, never a road point."""
import asyncio
import json
import math
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from shapely.geometry import Polygon, shape
from shapely.ops import transform, unary_union

CHICOPEE = "https://hostingdata3.tighebond.com/arcgis/rest/services/ChicopeeMA/ChicopeeDynamic/MapServer"
BOSTON = "https://gis.boston.gov/arcgis/rest/services/Basemaps/basemap_IPS/MapServer"
SOMERVILLE = "https://cos-geoportal.somervillema.gov/server/rest/services/enCodePlus"
REGISTRY = {
    "CAMBRIDGE": {"priority": 1, "reason": "Multifamily and inclusionary housing capacity", "status": "District connector and C-1 conceptual rules", "map_url": "https://www.cambridgema.gov/gis/gisdatadictionary/cdd/cdd_zoningdistricts", "code_url": "https://www.cambridgema.gov/cdd/zoninganddevelopment/zoning/ordinance"},
    "SOMERVILLE": {"priority": 1, "reason": "Building type, density, and mixed-use redevelopment", "status": "Published June 2025 parcel zoning and official district-rule links; later amendments require review", "map_url": "https://map.viewprogis.com/ecp/somerville-test?widget=9efa8bb5-3de1-4ff7-86b1-f698d0d6fb0d", "code_url": "https://online.encodeplus.com/regs/somerville-ma/doc-viewer.aspx"},
    "BOSTON": {"priority": 1, "reason": "Subdistrict, overlays, and redevelopment capacity", "status": "Municipal subdistrict GIS connector; project rules require review", "map_url": "https://www.bostonplans.org/zoning/zoning-viewer", "code_url": "https://www.bostonplans.org/zoning/zoning-code"},
    "WATERTOWN": {"priority": 2, "reason": "Corridor and mixed-use redevelopment", "status": "Queued for municipal source verification", "map_url": "https://www.watertown-ma.gov/", "code_url": "https://www.watertown-ma.gov/"},
    "EVERETT": {"priority": 2, "reason": "Industrial and commercial redevelopment", "status": "Queued for municipal source verification", "map_url": "https://cityofeverett.com/", "code_url": "https://cityofeverett.com/"},
    "CHELSEA": {"priority": 2, "reason": "Density and conversion opportunities", "status": "Queued for municipal source verification", "map_url": "https://www.chelseama.gov/", "code_url": "https://www.chelseama.gov/"},
    "REVERE": {"priority": 2, "reason": "Transit and corridor redevelopment", "status": "Queued for municipal source verification", "map_url": "https://www.revere.org/", "code_url": "https://www.revere.org/"},
    "CHICOPEE": {"priority": 3, "reason": "Addition and nonconforming-lot reference case", "status": "Parcel zoning, constraint screening and residential A/B reference standards", "map_url": "https://chicopeema.gov/566/Geographic-Information-Systems-GIS", "code_url": "https://chicopeema.gov/383/Zoning-Ordinances"},
}


def coverage(city):
    return {"municipality": city, **REGISTRY.get(str(city).upper(), {"priority": None, "status": "Municipal zoning source not connected"})}


def esri_polygon(geometry):
    """Even-odd rings support disjoint exteriors and interior holes."""
    result = Polygon()
    for ring in (geometry or {}).get("rings", []):
        if len(ring) >= 4:
            piece = Polygon(ring)
            if not piece.is_valid:
                piece = piece.buffer(0)
            result = result.symmetric_difference(piece)
    return result


def to_ground(geometry, latitude):
    scale = 3.280839895 * math.cos(math.radians(latitude))
    return transform(lambda x, y: (x * scale, y * scale), geometry)


def esri_query_geometry(parcel, latitude):
    scale = 3.280839895 * math.cos(math.radians(latitude))
    projected = transform(lambda x, y: (x / scale, y / scale), parcel)
    rings = []
    for p in ([projected] if projected.geom_type == "Polygon" else projected.geoms):
        rings.append(list(p.exterior.coords))
        rings.extend(list(h.coords) for h in p.interiors)
    return json.dumps({"rings": rings, "spatialReference": {"wkid": 3857}})


async def fetch_layer(client, url, geometry, fields='*'):
    try:
        response = await client.get(url + "/query", params={"f": "json", "geometry": geometry,
            "geometryType": "esriGeometryPolygon", "inSR": 3857, "outSR": 3857,
            "spatialRel": "esriSpatialRelIntersects", "outFields": fields, "returnGeometry": "true", "resultRecordCount": 250},
            timeout=httpx.Timeout(18, connect=4))
        response.raise_for_status()
        data = response.json()
        if data.get("error") or data.get("exceededTransferLimit"):
            raise ValueError("Incomplete municipal spatial response")
        return data.get("features", []), None
    except (httpx.HTTPError, ValueError, TypeError):
        return [], "Source unavailable or response incomplete"


def lonlat_geometry(parcel, latitude):
    scale = 3.280839895 * math.cos(math.radians(latitude))
    radius = 6378137
    return transform(lambda x, y: (math.degrees(x / scale / radius), math.degrees(2 * math.atan(math.exp(y / scale / radius)) - math.pi / 2)), parcel)


async def cambridge_lookup(client, parcel, latitude):
    if parcel is None:
        return None
    wkt = lonlat_geometry(parcel, latitude).wkt
    response = await client.get("https://data.cambridgema.gov/resource/yc3q-q3hf.json", params={
        "$where": f"intersects(the_geom, '{wkt}')", "$select": "zone_type,pud_type,the_geom", "$limit": 100}, timeout=httpx.Timeout(18, connect=4))
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or len(rows) >= 100:
        raise ValueError("Incomplete Cambridge zoning response")
    scale = 3.280839895 * math.cos(math.radians(latitude))
    radius = 6378137
    matches = []
    for row in rows:
        if not row.get('the_geom'):
            continue
        projected = transform(lambda x, y: (radius * math.radians(x) * scale, radius * math.log(math.tan(math.pi / 4 + math.radians(y) / 2)) * scale), shape(row['the_geom']))
        clipped = projected.intersection(parcel)
        if clipped.area > max(1, parcel.area * .001):
            matches.append((row, clipped, clipped.area))
    district = district_match(matches, 'zone_type', parcel.area)
    if not district:
        return None
    return {"district": district, "source_url": REGISTRY['CAMBRIDGE']['map_url'],
        "source_checked_at": datetime.now(timezone.utc).isoformat(), "match_method": "At least 98% selected-parcel polygon coverage",
        "current_amendments_verified": False, "snapshot_note": "Municipal zoning GIS; overlays and amendments require separate review."}


def overlap_features(features, parcel, latitude):
    matches = []
    for feature in features:
        geometry = to_ground(esri_polygon(feature.get("geometry")), latitude)
        if geometry.is_empty:
            continue
        overlap = geometry.intersection(parcel).area
        # Boundary touches and tiny GIS slivers do not establish a zoning district.
        if overlap > max(1, parcel.area * .001):
            matches.append((feature.get("attributes") or {}, geometry, overlap))
    return matches


def district_match(matches, field, area):
    groups = {}
    for attrs, geom, _ in matches:
        label = attrs.get(field)
        if label:
            groups.setdefault(str(label), []).append(geom)
    portions = {label: unary_union(polys).area for label, polys in groups.items()}
    # Caller passes clipped geometries; retain exact union coverage, not summed duplicate rows.
    complete = [label for label, portion in portions.items() if portion / area >= .98]
    return complete[0] if len(complete) == 1 and all(label == complete[0] or portion / area <= .01 for label, portion in portions.items()) else None


def reference_review(district, parcel):
    rules = {
        "ResA": {"section": "275-52", "min_lot_sqft": 10000, "front_ft": 25, "side_ft": 6, "rear_ft": 25, "height_ft": 40, "frontage_ft": 100},
        "ResB": {"section": "275-53", "min_lot_sqft": 7500, "front_ft": 25, "side_ft": 6, "rear_ft": 20, "height_ft": 40, "frontage_ft": 75},
    }.get(district)
    if not rules:
        return None
    area = parcel.get("gis_area_sqft_approx")
    return {"status": "Residential reference standards identified; approval unresolved", "reviewed_on": "2026-11-06",
        "source_url": "https://ecode360.com/15068550", "section": "§ " + rules["section"], "standards": rules,
        "lot_area_status": "Below district minimum; investigate lawful nonconforming lot status" if area and area < rules["min_lot_sqft"] else "Reported area meets district minimum; frontage and legal lot status still require review" if area else "Lot area unavailable",
        "conditions": "Front-yard averaging and porch exceptions may apply. A ground-story rear projection may reach 15 ft from the rear lot line under the district footnote. These exceptions are not automatically applied.",
        "next_step": "Obtain a survey showing the proposed addition, front/side/rear lot lines and current setbacks. Ask Chicopee Building Department to review §§ 275-22 and 275-37 and confirm any later amendments.",
        "limitations": "Single-family principal-building reference only. No permit, legal nonconformity, frontage, overlay, structural or amendment determination. No buildable area is calculated."}


async def lookup(client, city, parcel_geometry, latitude, parcel):
    city = str(city).upper()
    if parcel_geometry is None or city not in ("BOSTON", "SOMERVILLE", "CHICOPEE"):
        return {"coverage": coverage(city), "zoning": None, "constraints": [], "review": None}
    if city == "CHICOPEE":
        base, field, updated = CHICOPEE + "/4", "ZoneCode", "2025-01-01"
        screens = [("Zoning overlays", CHICOPEE + "/105", "OverlayDistrictName"), ("Historic districts", CHICOPEE + "/99", "HISTORIC_N"), ("Mapped resource buffers", CHICOPEE + "/102", None), ("Mapped floodplain", CHICOPEE + "/107", "FLD_ZONE")]
    elif city == "BOSTON":
        base, field, updated = BOSTON + "/28", "Zoning_Subdistrict", None
        screens = [("Planned development areas", BOSTON + "/41", None), ("Groundwater conservation", BOSTON + "/35", None), ("Interim planning overlays", BOSTON + "/38", None)]
    else:
        base, field, updated = SOMERVILLE + "/ZoningByParcel/MapServer/0", "ZONECODE", "2025-06-17"
        screens = [("Zoning overlays", SOMERVILLE + "/OverlayDistricts/MapServer/0", 'District'), ("Historic districts", SOMERVILLE + "/LocalHistoricDistricts/MapServer/0", 'LHD'), ('Transit area context', SOMERVILLE + '/TransitAreas/MapServer/0', 'WALKSHED')]
    geometry = esri_query_geometry(parcel_geometry, latitude)
    fields='ZONECODE,ZONEDESC,Permitted_Building_Types,Use_Provisions,Development_Standards,Standards_and_Measurements,Parking_and_Mobility' if city=='SOMERVILLE' else '*'
    results = await asyncio.gather(fetch_layer(client, base, geometry, fields), *(fetch_layer(client, url, geometry) for _, url, _ in screens))
    features, error = results[0]
    matches = overlap_features(features, parcel_geometry, latitude)
    clipped = [(a, g.intersection(parcel_geometry), n) for a, g, n in matches]
    district = district_match(clipped, field, parcel_geometry.area)
    constraints = []
    for (name, url, label), (rows, failure) in zip(screens, results[1:]):
        hits = overlap_features(rows, parcel_geometry, latitude)
        constraints.append({"name": name, "status": "unavailable" if failure else "mapped intersection" if hits else "no mapped intersection",
            "labels": list(dict.fromkeys(str(a.get(label)) for a, _, _ in hits if label and a.get(label))), "source_url": url, "note": "Map screening only; absence of a mapped feature is not regulatory clearance."})
    zoning = None
    if district:
        attrs = next(a for a, _, _ in matches if a.get(field) == district)
        zoning = {"district": district, "source_url": base, "source_checked_at": datetime.now(timezone.utc).isoformat(),
            "source_updated_at": updated, "match_method": "At least 98% selected-parcel polygon coverage", "current_amendments_verified": False,
            "snapshot_note": "Published municipal GIS snapshot; confirm amendments and full overlay coverage before design.",
            "article": attrs.get("Article"), "zoning_district": attrs.get("Zoning_District"), "amendment_date_reported": attrs.get("Latest_Amendment_Effective_Date")}
        if city == 'BOSTON':
            zoning['reference_standards'] = {label: attrs[key] for label, key in (
                ('Maximum FAR', 'Max_FAR'), ('Maximum height', 'Max_Height'), ('Front setback', 'Front_Setback'),
                ('Side setback', 'Side_Setback'), ('Rear setback', 'Rear_Setback')) if attrs.get(key) is not None}
            zoning['reference_conditions'] = attrs.get('Condition_Text') or attrs.get('Additional_Restrictions_or_Foot')
        if city == 'SOMERVILLE':
            zoning['district_description'] = attrs.get('ZONEDESC')
            zoning['amendment_date_reported'] = '2025-05-22'
            zoning['snapshot_note'] = 'Municipal layer updated June 17, 2025 and reports amendments through May 22, 2025. Later amendments require confirmation.'
            zoning['regulatory_links'] = [{'name':label,'url':attrs[key]} for label,key in (
                ('Permitted building types','Permitted_Building_Types'),('Allowed uses','Use_Provisions'),('Development standards','Development_Standards'),
                ('Standards and measurements','Standards_and_Measurements'),('Parking and mobility','Parking_and_Mobility'))
                if isinstance(attrs.get(key),str) and urlparse(attrs[key]).scheme=='https' and urlparse(attrs[key]).hostname=='online.encodeplus.com']
    return {"coverage": coverage(city), "zoning": zoning, "constraints": constraints,
        "status": error or ("Parcel crosses zoning boundaries or GIS coverage is incomplete" if matches and not district else "No zoning polygon matched" if not district else "Municipal GIS snapshot matched"),
        "review": reference_review(district, parcel) if city == "CHICOPEE" else None}
