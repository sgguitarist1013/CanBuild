"""Municipal zoning snapshots matched against the selected parcel, never a road point."""
import asyncio
import json
import math
import re
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse

from parcel_engine import requested_house_number, same_street

import httpx
from shapely import make_valid
from shapely.errors import GEOSException
from shapely.geometry import Polygon, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

CHICOPEE = "https://hostingdata3.tighebond.com/arcgis/rest/services/ChicopeeMA/ChicopeeDynamic/MapServer"
BOSTON = "https://gis.boston.gov/arcgis/rest/services/Basemaps/basemap_IPS/MapServer"
SOMERVILLE = "https://cos-geoportal.somervillema.gov/server/rest/services/enCodePlus"
CHELSEA = "https://services9.arcgis.com/diuwWhOq89A0FdTw/arcgis/rest/services/City_of_Chelsea_Map_Layers/FeatureServer"
EVERETT = "https://services2.arcgis.com/xJ0MjVdyImL1ajyn/arcgis/rest/services/Zoning_2019/FeatureServer/0"
EVERETT_TRIANGLE_CODE = "https://www.arcgis.com/sharing/rest/content/items/40748ac8f93344daa21a0a3023dd42f3/data"
REVERE = "https://gis.revere.org/arcgis/rest/services/RevereMA/MapServer"
REVERE_OVERLAY_REFERENCES = {
    "WTODOD": {
        "name": "Wonderland transit overlay",
        "summary": "Published overlay reference: 25,000 sq ft minimum lot, 100 ft minimum frontage, FAR up to 3.6 and up to 16 stories. The 160 ft ceiling has additional floor-area and spacing limits. Confirm design review and site plan approval before applying these limits.",
        "sources": [
            {"name": "Wonderland applicability and review", "url": "https://ecode360.com/45391745"},
            {"name": "Wonderland dimensional standards", "url": "https://ecode360.com/45391757"},
        ],
    },
    "GSSGOD": {
        "name": "Green Street smart growth overlay",
        "summary": "Published overlay reference: 10,000 sq ft minimum lot, FAR up to 1.5, and limits of 60 ft and six stories. Confirm plan approval, parking, affordability and any dimensional waivers.",
    },
    "SASGOD A": {
        "name": "Shirley Avenue smart growth overlay A",
        "summary": "Published overlay reference: 5,000 sq ft minimum lot, FAR up to 2, and limits of 60 ft and six stories. Confirm plan approval, parking, affordability and any dimensional waivers.",
    },
    "SASGOD B": {
        "name": "Shirley Avenue smart growth overlay B",
        "summary": "Published overlay reference: 5,000 sq ft minimum lot, FAR up to 2, and limits of 50 ft and five stories. Confirm plan approval, parking, affordability and any dimensional waivers.",
    },
}
REVERE_SMART_GROWTH_SOURCES = [
    {"name": "Green Street and Shirley Avenue district standards", "url": "https://ecode360.com/45392482"},
    {"name": "Smart growth housing and affordability", "url": "https://ecode360.com/45392388"},
    {"name": "Smart growth plan approval", "url": "https://ecode360.com/45392437"},
]
ATLAS = "https://services1.arcgis.com/hGdibHYSPO59RG1h/arcgis/rest/services/MBTA_Communities_3A_District_Atlas/FeatureServer"
ATLAS_SOURCE = "https://www.mass.gov/info-details/massgis-data-mbta-communities-3a-district-atlas"
MUNICIPALITIES = json.loads((Path(__file__).parent / "docs/massachusetts_municipalities.json").read_text())
SMART_GROWTH_SOURCE = "https://www.mass.gov/info-details/chapter-40r-smart-growth-zoning"
SMART_GROWTH_DISTRICTS = json.loads((Path(__file__).parent / "docs/massachusetts_40r_districts.json").read_text())

ARLINGTON = "https://services2.arcgis.com/s1Sh73K7qtP9JdrG/arcgis/rest/services/Zoning_and_Overlay_Districts/FeatureServer"
NORTHAMPTON = "https://services3.arcgis.com/rXBMAZhnkewHZIaE/arcgis/rest/services/Northampton_Zoning_and_Overlays_WFL2/FeatureServer"
NEWTON = "https://gisweb.newtonma.gov/server/rest/services/Data/MapServer"
WORCESTER = "https://services1.arcgis.com/j8dqo2DJE7mVUBU1/arcgis/rest/services"
LYNN = "https://services8.arcgis.com/vFKGBS59j3zLGs8e/arcgis/rest/services/Lynn_s_Zoning_Map_251209_WFL1/FeatureServer"
SPRINGFIELD = "https://maps.springfield-ma.gov/arcgis/rest/services/WebGIS/WebGISDynamicMap/MapServer"
WELLESLEY = "https://services6.arcgis.com/f6G5SbcwuEVmR1CW/arcgis/rest/services/Open_Data_Layers/FeatureServer"
SALEM = "https://services9.arcgis.com/nPsFhwkdebYjxn1R/arcgis/rest/services"
STOW = "https://services6.arcgis.com/bYxJFmZwD1DSkFy9/arcgis/rest/services"
NMCOG = "https://services2.arcgis.com/8sfNXBvIUURUO8wz/arcgis/rest/services/NMCOG_Zoning_Districts_v20230106/FeatureServer/131"
NMCOG_TOWNS = ("BILLERICA", "CHELMSFORD", "DRACUT", "DUNSTABLE", "LOWELL", "PEPPERELL", "TEWKSBURY", "TYNGSBOROUGH", "WESTFORD")

# New snapshot connectors can be added with verified layer IDs and fields.
SNAPSHOT_CONNECTORS = {
    "ARLINGTON": {
        "base": ARLINGTON + "/1", "field": "ZoneCode", "description_field": "ZoneName",
        "updated": "2025-11-07", "repair_ring": True,
        "screens": [("Arlington zoning overlays", ARLINGTON + "/0", "OverlayDistrict")],
        "map_url": "https://www.arcgis.com/home/item.html?id=9916a97573ca40db9c817f478bf44c43",
        "priority": 2, "reason": "Multifamily and transit corridor redevelopment",
        "status": "Town GIS base districts and overlays; current bylaws and amendments require confirmation",
    },
    "NORTHAMPTON": {
        "base": NORTHAMPTON + "/2", "field": "NAME", "description_field": None,
        "updated": "2026-02-06", "screens": [("Northampton zoning overlays", NORTHAMPTON + "/1", "NAME1_")],
        "map_url": "https://www.arcgis.com/home/item.html?id=60931800fa7c4fc2880b4c593af00b7a",
        "code_url": "https://ecode360.com/13265306",
        "priority": 2, "reason": "Downtown infill and housing redevelopment",
        "status": "City GIS base districts and overlays; current ordinance and amendments require confirmation",
    },
    "NEWTON": {
        "base": NEWTON + "/24", "field": "Zoning", "description_field": None,
        "updated": None, "local_rings_only": True, "screens": [("Newton village center overlays", NEWTON + "/23", "Name")],
        "map_url": "https://www.arcgis.com/home/item.html?id=a0903a1841634b3c8b7400f31bf9a8d7",
        "priority": 2, "reason": "Village center and transit redevelopment",
        "status": "City GIS district codes and village center overlay screening; project rules pending",
    },
    "WORCESTER": {
        "base": WORCESTER + "/Zoning/FeatureServer/9", "field": "ZONE", "description_field": "NAME",
        "updated": "2025-04-28", "screens": [("Worcester zoning overlays", WORCESTER + "/Zoning_Overlays/FeatureServer/10", "NAME")],
        "map_url": "https://www.arcgis.com/home/item.html?id=318e135d47f842e2893ee44906db6749",
        "code_url": "https://www.worcesterma.gov/planning-regulatory/zoning-ordinance-map",
        "priority": 2, "reason": "Downtown housing and adaptive reuse",
        "status": "City GIS base districts and overlays; current ordinance and amendments require confirmation",
    },
    "LYNN": {
        "base": LYNN + "/11", "field": "ZONE", "description_field": None,
        "updated": "2025-12-17", "amendments_through": "2025-12-09",
        "screens": [
            ("Mapped ground-floor commercial requirements", LYNN + "/9", None),
            ("Mapped required thoroughfares", LYNN + "/10", None),
        ],
        "map_url": "https://cityoflynn.maps.arcgis.com/apps/instant/basic/index.html?appid=046e581adf6c4a93bbb4e997497df453",
        "code_url": "https://www.lynnma.gov/city_government/departments/isd/zoning",
        "priority": 2, "reason": "Downtown mixed-use redevelopment and transit",
        "status": "City GIS zoning snapshot through December 9, 2025; later amendments and project requirements pending",
    },
    "SPRINGFIELD": {
        "base": SPRINGFIELD + "/31", "field": "ZONING", "description_field": "ZONE_NAME",
        "updated": None, "base_where": "ZONING IS NOT NULL", "split_layer": SPRINGFIELD + "/32",
        "screens": [
            ("Springfield zoning overlays", SPRINGFIELD + "/30", "NAME"),
            ("Springfield split zoning", SPRINGFIELD + "/32", "ZONE_NAME"),
        ],
        "map_url": "https://maps.springfield-ma.gov/gis/",
        "code_url": "https://www.springfield-ma.gov/planning/planning/zoning-ordinances",
        "priority": 2, "reason": "Downtown infill and mixed-use redevelopment",
        "status": "City GIS base districts, overlays and split-zone screening; project rules pending",
    },
    "WELLESLEY": {
        "base": WELLESLEY + "/26", "field": "ZoningCode", "description_field": "ZoningDescription",
        "updated": "2026-10-29", "screens": [("Wellesley zoning overlays", WELLESLEY + "/25", "DISTRICT")],
        "map_url": "https://www.arcgis.com/home/item.html?id=bba3b9e3352244fdbb3d4575aa1e348e",
        "code_url": "https://www.wellesleyma.gov/281/Zoning-Bylaws",
        "priority": 2, "reason": "Village business districts and transit-oriented infill",
        "status": "Town GIS screening map and overlays; confirm the adopted map, bylaws and amendments",
    },
    "SALEM": {
        "base": SALEM + "/Zoning_Districts_2024/FeatureServer/0", "field": "ZONE_CODE", "description_field": "ZONE_DES",
        "updated": "2024-07-25", "local_rings_only": True,
        "screens": [
            ("Salem smart growth subzones", SALEM + "/Zoning_SmartGrowth_Overlay_Subzones/FeatureServer/3", "Subzone_Name"),
            ("Salem coastal resiliency overlay", SALEM + "/CROD__Coastal_Resiliency_Overlay/FeatureServer/0", None),
            ("Salem waterfront industrial overlay", SALEM + "/Waterfront_Industrial_Overlay_District/FeatureServer/1", None),
            ("Salem local historic districts", SALEM + "/Salem_Local_Historic_Districts/FeatureServer/0", "NAME"),
        ],
        "map_url": "https://salemma.maps.arcgis.com/apps/instant/interactivelegend/index.html?appid=15f63e4da223424db6adfff4ab641be6",
        "code_url": "https://www.salemma.gov/933/Zoning-Ordinance-Zoning-Maps",
        "priority": 2, "reason": "Smart growth and waterfront redevelopment",
        "status": "City viewer base layer edited July 2024; selected overlay screens from separate layers",
    },
    "STOW": {
        "base": STOW + "/Base_Zoning_Districts/FeatureServer/0", "field": "Zone", "description_field": "Name",
        "updated": "2022-08-04", "screens": [],
        "map_url": "https://www.arcgis.com/home/item.html?id=8de93b19c6d342129fc1200a55e7f7c5",
        "status": "Historical 2022 municipal base zoning layer; current zoning requires confirmation",
        "historical_zoning_only": True,
    },
}
for town in NMCOG_TOWNS:
    SNAPSHOT_CONNECTORS[town] = {
        "base": NMCOG, "field": "Zone_CODE", "description_field": "Zone_DESC",
        "town_field": "Town_Name", "updated": None, "screens": [],
        "map_url": "https://www.arcgis.com/home/item.html?id=a79a7b6d02c64614b548474db85e4430",
        "status": "Historical regional compilation from January 2023; current local map required",
        "historical_zoning_only": True,
    }

LOCAL_CONNECTORS = {"BOSTON", "SOMERVILLE", "CHICOPEE", "CHELSEA", "EVERETT", "REVERE"} | SNAPSHOT_CONNECTORS.keys()
REGISTRY = {
    "CAMBRIDGE": {"priority": 1, "reason": "Multifamily and inclusionary housing capacity", "status": "District connector and C-1 conceptual rules", "map_url": "https://www.cambridgema.gov/gis/gisdatadictionary/cdd/cdd_zoningdistricts", "code_url": "https://www.cambridgema.gov/cdd/zoninganddevelopment/zoning/ordinance"},
    "SOMERVILLE": {"priority": 1, "reason": "Building type, density, and mixed-use redevelopment", "status": "Published June 2025 parcel zoning and official district-rule links; later amendments require review", "map_url": "https://map.viewprogis.com/ecp/somerville-test?widget=9efa8bb5-3de1-4ff7-86b1-f698d0d6fb0d", "code_url": "https://online.encodeplus.com/regs/somerville-ma/doc-viewer.aspx"},
    "BOSTON": {"priority": 1, "reason": "Subdistrict, overlays, and redevelopment capacity", "status": "Municipal subdistrict GIS connector; project rules require review", "map_url": "https://www.bostonplans.org/zoning/zoning-viewer", "code_url": "https://www.bostonplans.org/zoning/zoning-code"},
    "WATERTOWN": {"priority": 2, "reason": "Corridor and mixed-use redevelopment", "status": "Watertown Square overlay rules linked to state parcel screening; citywide base districts pending", "map_url": "https://www.watertown-ma.gov/zoning", "code_url": "https://ecode360.com/36825722"},
    "EVERETT": {"priority": 2, "reason": "Commercial Triangle and Lower Broadway redevelopment", "status": "Historical city zoning map available (July 2019); current district requires confirmation", "map_url": "https://www.arcgis.com/home/item.html?id=53ba373249404bae80dbb1e5366bd9b5", "code_url": "https://cityofeverett.com/city-hall/departments/planning-development/", "historical_zoning_only": True},
    "CHELSEA": {"priority": 2, "reason": "Density and conversion opportunities", "status": "City GIS snapshot from March 2023; confirm current base zoning and overlays", "map_url": "https://www.arcgis.com/home/item.html?id=ba933034c3eb41ebad0d14ba0c6ea3b1", "code_url": "https://library.municode.com/ma/chelsea/codes/code_of_ordinances"},
    "REVERE": {"priority": 2, "reason": "Transit and corridor redevelopment", "status": "City GIS base districts and overlay screening; selected transit and smart growth code references", "map_url": "https://www.revere.org/gis", "code_url": "https://ecode360.com/45390701"},
    "CHICOPEE": {"priority": 3, "reason": "Addition and nonconforming-lot reference case", "status": "Parcel zoning, constraint screening and residential A/B reference standards", "map_url": "https://chicopeema.gov/566/Geographic-Information-Systems-GIS", "code_url": "https://chicopeema.gov/383/Zoning-Ordinances"},
}


for town, snapshot in SNAPSHOT_CONNECTORS.items():
    REGISTRY[town] = {key: snapshot[key] for key in (
        "priority", "reason", "status", "map_url", "code_url", "historical_zoning_only",
    ) if key in snapshot}


def coverage(city):
    key = str(city).upper()
    entry = REGISTRY.get(key, {"status": "Municipal base zoning source not connected"})
    historical = entry.get("historical_zoning_only", False)
    connected = (key in LOCAL_CONNECTORS or key == "CAMBRIDGE") and not historical
    result = {
        "municipality": city, "priority": None, **entry,
        "base_zoning_connected": connected,
        "base_map_status": "municipal_snapshot" if connected else "historical_reference" if historical else "not_connected",
        "multifamily_screen_source": ATLAS_SOURCE,
    }
    if key in SMART_GROWTH_DISTRICTS:
        result["smart_growth_inventory"] = {
            "district_names": SMART_GROWTH_DISTRICTS[key],
            "snapshot_date": "2026-02-10", "source_url": SMART_GROWTH_SOURCE,
            "scope": "Municipality inventory only; parcel coverage not evaluated",
        }
    return result


def esri_polygon(geometry, *, repair_ring=False, bounds=None):
    """Build shells/holes with a spatial index instead of repeated polygon XOR."""
    rings = (geometry or {}).get('rings', [])
    if sum(len(ring) for ring in rings) > 250000:
        raise ValueError('Municipal geometry exceeds processing limit')
    pieces = [Polygon(ring) for ring in rings if len(ring) >= 4]
    if bounds is not None:
        min_x, min_y, max_x, max_y = bounds
        # Distant parts of a dissolved citywide district cannot affect parcel overlap.
        pieces = [p for p in pieces if p.bounds[0] <= max_x and p.bounds[2] >= min_x
                  and p.bounds[1] <= max_y and p.bounds[3] >= min_y]
    if not pieces:
        return Polygon()
    for index, piece in enumerate(pieces):
        if piece.is_valid:
            continue
        # Everett's historical layer includes a self-touching ring. Accept only
        # a polygon repair that preserves its area. Retain repaired holes.
        if repair_ring:
            repaired = make_valid(piece)
            if (repaired.geom_type == "Polygon"
                    and math.isclose(repaired.area, piece.area, rel_tol=1e-9, abs_tol=.0001)):
                pieces[index] = repaired
                continue
        raise ValueError('Municipal geometry contains an invalid ring')
    if len(pieces) == 1:
        return pieces[0]
    tree = STRtree(pieces)
    parents = {}
    depth = {}
    for i, piece in enumerate(pieces):
        enclosing = [int(j) for j in tree.query(piece, predicate='within') if pieces[int(j)].area > piece.area]
        depth[i] = len(enclosing)
        parents[i] = min(enclosing, key=lambda j: pieces[j].area) if enclosing else None
    children = {}
    for child, parent in parents.items():
        if parent is not None and depth[child] % 2 == 1:
            children.setdefault(parent, []).append(pieces[child].exterior.coords)
    shells = []
    for i, piece in enumerate(pieces):
        if depth[i] % 2 == 0:
            shells.append(Polygon(piece.exterior.coords, [h.coords for h in piece.interiors] + children.get(i, [])))
    return unary_union(shells)


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


async def fetch_layer(client, url, geometry, fields='*', include_geometry=True, *, where='1=1', timeout=18):
    try:
        response = await client.get(url + "/query", params={"f": "json", "where": where, "geometry": geometry,
            "geometryType": "esriGeometryPolygon", "inSR": 3857, "outSR": 3857,
            "spatialRel": "esriSpatialRelIntersects", "outFields": fields, "returnGeometry": str(include_geometry).lower(), "resultRecordCount": 250},
            timeout=httpx.Timeout(timeout, connect=4))
        response.raise_for_status()
        data = await asyncio.to_thread(response.json)
        if not isinstance(data, dict) or not isinstance(data.get("features"), list) or data.get("error") or data.get("exceededTransferLimit"):
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
    rows = await asyncio.to_thread(response.json)
    if not isinstance(rows, list) or len(rows) >= 100:
        raise ValueError("Incomplete Cambridge zoning response")
    return await asyncio.to_thread(cambridge_match, rows, parcel, latitude)


def cambridge_match(rows, parcel, latitude):
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


def overlap_features(features, parcel, latitude, *, repair_ring=False, local_rings_only=False):
    matches = []
    scale = 3.280839895 * math.cos(math.radians(latitude))
    bounds = tuple(coordinate / scale for coordinate in parcel.bounds) if local_rings_only else None
    for feature in features:
        geometry = to_ground(esri_polygon(feature.get("geometry"), repair_ring=repair_ring, bounds=bounds), latitude)
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



def somerville_street(address):
    """Use the requested street even when Census substitutes another street type."""
    match = re.fullmatch(r"(.+?)(?:,\s*|\s+)Somerville(?:,\s*|\s+)(?:MA|Massachusetts)(?:(?:,\s*|\s+)\d{5}(?:-\d{4})?)?", address.strip(), re.I)
    if not match:
        return None
    return re.split(r"\s+(?:#|unit\s+|apt\.?\s+)|,", match[1], maxsplit=1, flags=re.I)[0].strip()


async def somerville_parcel(client, address):
    """Match one parcel to an exact city GIS street address, without assuming tax ownership."""
    street_line = somerville_street(address)
    number = requested_house_number(street_line)
    if not number:
        return None
    street = street_line.split(maxsplit=1)[1]
    pairs = [("AddNum", "Street"), ("AddNum2", "Street2"), ("AddNum3", "Street3")]
    url = SOMERVILLE + "/ZoningByParcel/MapServer/0"
    params = {
        "f": "json", "where": " OR ".join(f"{field} = '{number}'" for field, _ in pairs),
        "outFields": "MBL,PolyType,AddNum,Street,AddNum2,Street2,AddNum3,Street3",
        "outSR": 3857, "returnGeometry": "true", "resultRecordCount": 250,
    }
    response = await client.get(url + "/query", params=params, timeout=httpx.Timeout(10, connect=4))
    response.raise_for_status()
    data = response.json()
    if data.get("error") or data.get("exceededTransferLimit") or not isinstance(data.get("features"), list):
        return None
    matches = {}
    for feature in data["features"]:
        attrs = feature.get("attributes") or {}
        parcel_id = attrs.get("MBL")
        if attrs.get("PolyType") != "PARCEL" or not parcel_id:
            continue
        for house_field, street_field in pairs:
            if str(attrs.get(house_field)).strip().upper() != number or not same_street(attrs.get(street_field), street):
                continue
            geometry = esri_polygon(feature.get("geometry"))
            if not geometry.is_empty:
                matches.setdefault(parcel_id, (attrs[street_field], []))[1].append(geometry)
            break
    if len(matches) != 1:
        return None
    parcel_id, (street, geometries) = next(iter(matches.items()))
    geometry = unary_union(geometries)
    point = geometry.representative_point()
    lat = math.degrees(math.atan(math.sinh(point.y / 6378137)))
    lon = math.degrees(point.x / 6378137)
    polygon = to_ground(geometry, lat)
    area = round(polygon.area)
    parcel = {"parcel_number": parcel_id, "gis_area_sqft_approx": area, "polygon_area_sqft_approx": area,
              "area_source": "Somerville municipal GIS polygon, approximate", "recorded_owners": []}
    return parcel, polygon, f"{number} {street}, Somerville, MA", lon, lat, url

async def lookup(client, city, parcel_geometry, latitude, parcel):
    city = str(city).upper()
    if parcel_geometry is None or city not in LOCAL_CONNECTORS:
        return {"coverage": coverage(city), "zoning": None, "constraints": [],
                "constraints_screen_complete": False, "review": None}
    snapshot = SNAPSHOT_CONNECTORS.get(city)
    if snapshot:
        base, field, updated = snapshot["base"], snapshot["field"], snapshot["updated"]
        screens = snapshot["screens"]
    elif city == "CHICOPEE":
        base, field, updated = CHICOPEE + "/4", "ZoneCode", "2025-01-01"
        screens = [("Zoning overlays", CHICOPEE + "/105", "OverlayDistrictName"), ("Historic districts", CHICOPEE + "/99", "HISTORIC_N"), ("Mapped resource buffers", CHICOPEE + "/102", None), ("Mapped floodplain", CHICOPEE + "/107", "FLD_ZONE")]
    elif city == "BOSTON":
        base, field, updated = BOSTON + "/28", "Zoning_Subdistrict", None
        screens = [("Planned development areas", BOSTON + "/41", None), ("Groundwater conservation", BOSTON + "/35", None), ("Interim planning overlays", BOSTON + "/38", None)]
    elif city == "REVERE":
        base, field, updated = REVERE + "/62", "zone_name", None
        screens = [("Revere zoning overlays", REVERE + "/20", "longname")]
    elif city == "EVERETT":
        base, field, updated = EVERETT, "Zoning2018", "2019-07-18"
        screens = []
    elif city == "CHELSEA":
        base, field, updated = CHELSEA + "/12", "ZONE_CODE", "2023-03-13"
        screens = [
            ("Airport overlay", CHELSEA + "/7", None),
            ("Floodplain overlay", CHELSEA + "/8", None),
            ("Gerrish Avenue smart growth overlay", CHELSEA + "/9", None),
            ("Mixed-use overlay", CHELSEA + "/13", None),
            ("Mapped FEMA flood hazards", CHELSEA + "/14", "FLD_ZONE"),
        ]
    else:
        base, field, updated = SOMERVILLE + "/ZoningByParcel/MapServer/0", "ZONECODE", "2025-06-17"
        screens = [("Zoning overlays", SOMERVILLE + "/OverlayDistricts/MapServer/0", 'District'), ("Historic districts", SOMERVILLE + "/LocalHistoricDistricts/MapServer/0", 'LHD'), ('Transit area context', SOMERVILLE + '/TransitAreas/MapServer/0', 'WALKSHED')]
    geometry = esri_query_geometry(parcel_geometry, latitude)
    fields='ZONECODE,ZONEDESC,Permitted_Building_Types,Use_Provisions,Development_Standards,Standards_and_Measurements,Parking_and_Mobility' if city=='SOMERVILLE' else '*'
    town_field = snapshot.get("town_field") if snapshot else None
    where = f"UPPER({town_field}) = '{city}'" if town_field else (snapshot or {}).get("base_where", "1=1")
    results = await asyncio.gather(fetch_layer(client, base, geometry, fields, where=where), *(fetch_layer(client, url, geometry, include_geometry=False) for _, url, _ in screens))
    features, error = results[0]
    if town_field:
        features = [f for f in features if str((f.get("attributes") or {}).get(town_field)).upper() == city]
    # Features without a district code cannot contribute to a district match.
    features = [f for f in features if (f.get("attributes") or {}).get(field)]
    matches, district = [], None
    repair_ring = city == "EVERETT" or bool(snapshot and snapshot.get("repair_ring"))
    try:
        matches = await asyncio.to_thread(
            overlap_features, features, parcel_geometry, latitude, repair_ring=repair_ring,
            local_rings_only=bool(snapshot and snapshot.get("local_rings_only")),
        )
        clipped = [(a, g.intersection(parcel_geometry), n) for a, g, n in matches]
        district = district_match(clipped, field, parcel_geometry.area)
    except (GEOSException, ValueError, TypeError):
        # Base district geometry and the attributes-only overlay screens are independent.
        error = "Base zoning geometry could not be validated"
    constraints = []
    for (name, url, label), (rows, failure) in zip(screens, results[1:]):
        # The server performs the parcel intersection. Screening needs attributes only;
        # fetching an entire citywide buffer polygon can stall a small web instance.
        hits = [row.get('attributes') or {} for row in rows]
        constraints.append({"name": name, "status": "unavailable" if failure else "mapped intersection" if hits else "no mapped intersection",
            "labels": list(dict.fromkeys(str(a.get(label)) for a in hits if label and a.get(label))), "source_url": url, "note": "Server-side parcel screening includes boundary touches. Absence of a mapped feature is not regulatory clearance."})
        if snapshot and url == snapshot.get("split_layer") and (failure or hits):
            district = None
            error = "Split zoning screen unavailable; base district withheld" if failure else "Parcel intersects mapped split zoning; single district withheld"
        if city == "REVERE":
            codes = dict.fromkeys(a.get("zonetype") for a in hits)
            constraints[-1]["rule_references"] = [
                {"code": code, "sources": REVERE_SMART_GROWTH_SOURCES, **REVERE_OVERLAY_REFERENCES[code]}
                for code in codes if code in REVERE_OVERLAY_REFERENCES
            ]
    zoning = None
    if district:
        attrs = next(a for a, _, _ in matches if a.get(field) == district)
        zoning = {"district": district, "source_url": base, "source_checked_at": datetime.now(timezone.utc).isoformat(),
            "source_updated_at": updated, "match_method": "At least 98% selected-parcel polygon coverage", "current_amendments_verified": False,
            "snapshot_note": "Published municipal GIS snapshot; confirm amendments and full overlay coverage before design.",
            "article": attrs.get("Article"), "zoning_district": attrs.get("Zoning_District"), "amendment_date_reported": attrs.get("Latest_Amendment_Effective_Date")}
        if snapshot:
            description = snapshot["description_field"]
            zoning["district_description"] = attrs.get(description) if description else None
            if snapshot.get("historical_zoning_only"):
                zoning["historical_reference"] = True
                zoning["snapshot_note"] = snapshot["status"] + "."
            if snapshot.get("amendments_through"):
                zoning["amendment_date_reported"] = snapshot["amendments_through"]
                zoning["snapshot_note"] = f"City viewer reports amendments through {snapshot['amendments_through']}. Confirm later changes and project requirements."
            if snapshot.get("code_url"):
                zoning["regulatory_links"] = [{"name": f"{city.title()} zoning resources", "url": snapshot["code_url"]}]
        if city == 'BOSTON':
            zoning['reference_standards'] = {label: attrs[key] for label, key in (
                ('Maximum FAR', 'Max_FAR'), ('Maximum height', 'Max_Height'), ('Front setback', 'Front_Setback'),
                ('Side setback', 'Side_Setback'), ('Rear setback', 'Rear_Setback')) if attrs.get(key) is not None}
            zoning['reference_conditions'] = attrs.get('Condition_Text') or attrs.get('Additional_Restrictions_or_Foot')
        if city == 'REVERE':
            zoning["snapshot_note"] = "Revere municipal GIS snapshot. Confirm the current zoning atlas, amendments and project requirements with the city."
            zoning["regulatory_links"] = [
                {"name": "Revere use regulations", "url": "https://ecode360.com/45391024"},
                {"name": "Revere dimensional regulations", "url": "https://ecode360.com/45391923"},
            ]
        if city == 'EVERETT':
            zoning["historical_reference"] = True
            zoning["district_description"] = attrs.get("ZoningName")
            zoning["snapshot_note"] = "Historical Everett city map from July 2019. Confirm the current district, overlays and amendments with the city."
            if district == "EDD":
                zoning["reference_conditions"] = "The published Section 33 excerpt describes a mixed-use path with a commercial-space requirement and site plan review. Confirm the current text and whether it applies to this parcel."
                zoning["regulatory_links"] = [{"name": "City-published Section 33 excerpt", "url": EVERETT_TRIANGLE_CODE}]
        if city == 'CHELSEA':
            zoning['district_description'] = attrs.get('ZONE_NAME')
            zoning['snapshot_note'] = 'City GIS layer last edited in March 2023. Confirm current zoning and later overlays against the city ordinance.'
            zoning['regulatory_links'] = [{'name': 'Chelsea zoning ordinance', 'url': REGISTRY[city]['code_url']}]
        if city == 'SOMERVILLE':
            zoning['district_description'] = attrs.get('ZONEDESC')
            zoning['amendment_date_reported'] = '2025-05-22'
            zoning['snapshot_note'] = 'Municipal layer updated June 17, 2025 and reports amendments through May 22, 2025. Later amendments require confirmation.'
            zoning['regulatory_links'] = [{'name':label,'url':attrs[key]} for label,key in (
                ('Permitted building types','Permitted_Building_Types'),('Allowed uses','Use_Provisions'),('Development standards','Development_Standards'),
                ('Standards and measurements','Standards_and_Measurements'),('Parking and mobility','Parking_and_Mobility'))
                if isinstance(attrs.get(key),str) and urlparse(attrs[key]).scheme=='https' and urlparse(attrs[key]).hostname=='online.encodeplus.com']
    constraints_screen_complete = bool(screens) and len(constraints) == len(screens) and all(
        item.get("status") in {"mapped intersection", "no mapped intersection"} and item.get("source_url")
        for item in constraints
    )
    return {"coverage": coverage(city), "zoning": zoning, "constraints": constraints,
        "constraints_screen_complete": constraints_screen_complete,
        "status": error or ("Parcel crosses zoning boundaries or GIS coverage is incomplete" if matches and not district else "No zoning polygon matched" if not district else "Municipal GIS snapshot matched"),
        "review": reference_review(district, parcel) if city == "CHICOPEE" else None}


def watertown_overlay_review(districts):
    """Attach reviewed code references to the city's named 3A subdistricts."""
    rows = [re.fullmatch(r"314(WSQ[123])_TODO(_Active_Frontage)?", row["code"]) for row in districts]
    matched = [row for row in rows if row]
    if not matched:
        return None
    active = any(row.group(2) for row in matched)
    standards = {
        "WSQ1": ("3+", 53, 59, 15),
        "WSQ2": ("4+", 66, 72, 10),
        "WSQ3": ("5+", 82, 88, 5),
    }
    references = []
    for district in sorted({row.group(1) for row in matched}):
        stories, flat, pitched, open_space = standards[district]
        references.append({
            "district": district, "stories": stories, "flat_roof_ft": flat,
            "pitched_roof_ft": pitched, "minimum_open_space_pct": open_space,
        })
    return {
        "title": "Watertown Square multifamily overlay review",
        "summary": "Section 5.09 allows qualifying multifamily and mixed-use development by right, subject to site plan review and applicable design and affordability requirements.",
        "active_frontage_mapped": active,
        "next_step": (
            "Ask Watertown Planning which frontage must carry ground-floor active use, how the overlay applies to the full lot, and which dimensional and affordability rules govern the proposal."
            if active else
            "Ask Watertown Planning to confirm the current overlay boundary, frontage designations, roof form, lot dimensions and affordability requirements."
        ),
        "limitations": "Section 5.08 table values are district references. A plus story is subject to roof and step-back rules. Section 5.09 generally limits the overlay to the mapped portion unless the zoning officer determines more than half the lot is in it. Verify current maps and amendments before estimating units.",
        "district_references": references,
        "sources": [
            {"name": "§ 5.09 Transit Oriented Development Overlay", "url": "https://ecode360.com/37104096"},
            {"name": "§ 5.08 Watertown Square dimensional and design rules", "url": "https://ecode360.com/37104064"},
            {"name": "§ 5.07 Affordable housing", "url": "https://ecode360.com/37104007"},
            {"name": "§ 9.03 Site plan review", "url": "https://ecode360.com/37103499"},
            {"name": "Watertown zoning amendments", "url": "https://watertown-ma.gov/current-zoning-amendments"},
        ],
    }


async def multifamily_screen(client, city, polygon, latitude, loc_id):
    """Keep the state compliance model separate from base zoning and permitted yield."""
    city = city.upper()
    result = {"status": "Parcel geometry unavailable", "districts": [], "modeled_units": None, "source_url": ATLAS_SOURCE}
    if polygon is None or polygon.is_empty or not re.fullmatch(r"[A-Z .'-]+", city):
        return result

    where = "UPPER(MUNI) = '" + city.replace("'", "''") + "' AND STATUS = 'Compliant'"
    geometry = esri_query_geometry(polygon, latitude)
    rows, error = await fetch_layer(
        client, ATLAS + "/0", geometry, "MUNI,ZONECOD,ZONENAM,DATE_,DU_CAP",
        where=where, timeout=8,
    )
    if error:
        return {**result, "status": "State atlas temporarily unavailable"}
    try:
        matches = await asyncio.to_thread(overlap_features, rows, polygon, latitude)
    except (GEOSException, ValueError, TypeError):
        return {**result, "status": "State atlas geometry could not be validated"}
    by_code = {}
    for attrs, shape_, _ in matches:
        code = attrs.get("ZONECOD")
        if code and str(attrs.get("MUNI")).upper() == city:
            by_code.setdefault(code, []).append((attrs, shape_.intersection(polygon)))
    if not by_code:
        return {**result, "status": "No district matched in the published state atlas"}

    shares = {}
    for code, group in by_code.items():
        attrs = group[0][0]
        share = unary_union([geometry for _, geometry in group]).area / polygon.area
        shares[code] = share
        date = attrs.get("DATE_")
        result["districts"].append({
            "code": code, "name": " ".join((attrs.get("ZONENAM") or code).split()),
            "parcel_overlap_pct": round(100 * share, 1),
            "municipal_submission_date": datetime.fromtimestamp(date / 1000, timezone.utc).date().isoformat() if date else None,
            "district_cap_reported": attrs.get("DU_CAP"),
        })
    result["status"] = "Mapped Section 3A district intersection"
    if city == "WATERTOWN":
        review = watertown_overlay_review(result["districts"])
        if review:
            result["local_rule_review"] = review
    district = result["districts"][0]
    # Withhold the model if the parcel spans multiple districts, has shifted, or lacks an exact ID.
    if len(by_code) != 1 or shares[district["code"]] < .98 or not re.fullmatch(r"[FM]_\d+_\d+", str(loc_id)):
        return result
    rows, error = await fetch_layer(
        client, ATLAS + "/1", geometry, "LOC_ID,MUNI,ZONECOD,F_L_M__",
        where=where + f" AND LOC_ID = '{loc_id}'", timeout=8,
    )
    if error or len(rows) != 1:
        return result
    attrs = rows[0].get("attributes") or {}
    if (attrs.get("LOC_ID"), str(attrs.get("MUNI")).upper(), attrs.get("ZONECOD")) != (loc_id, city, district["code"]):
        return result
    try:
        matches = await asyncio.to_thread(overlap_features, rows, polygon, latitude)
    except (GEOSException, ValueError, TypeError):
        return result
    units = attrs.get("F_L_M__")
    if matches and matches[0][2] >= .98 * max(polygon.area, matches[0][1].area):
        if type(units) in (int, float) and math.isfinite(units) and units >= 0 and int(units) == units:
            result["modeled_units"] = int(units)
    return result
