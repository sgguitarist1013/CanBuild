"""Statewide Massachusetts tax-parcel matching and geometric feasibility helpers.

The same strict match and object-id geometry lookup apply to every municipality.
Zoning rule configurations are separate and enabled only where verified.
"""
import asyncio
import math
import re
import httpx
from shapely.geometry import Polygon
from shapely.ops import unary_union, transform

SERVICE = "https://services1.arcgis.com/hGdibHYSPO59RG1h/ArcGIS/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query"
FIELDS = "OBJECTID,MAP_PAR_ID,LOC_ID,POLY_TYPE,PROP_ID,SITE_ADDR,ADDR_NUM,FULL_STR,CITY,FY,LOT_SIZE,LOT_UNITS,BLD_AREA,RES_AREA,UNITS,YEAR_BUILT,STORIES,USE_DESC,ZONING,TOTAL_VAL,LAST_EDIT,OWNER1,USE_CODE,LS_DATE,LS_PRICE,LS_BOOK,LS_PAGE,REG_ID"

# Rules here are municipality-specific; no city rules are applied statewide.
SUPPORTED_RULES = {
    ("CAMBRIDGE", "C-1"): {
        "stories": 4, "height_ft": 45, "inclusionary_stories": 6,
        "inclusionary_height_ft": 74, "inclusionary_min_lot_sqft": 5000,
        "inclusionary_affordable_fraction": 0.20,
        "front_setback_ft": 10, "side_rear_setback_ft": 5,
        "open_space_fraction": 0.30,
        "source": "https://www.cambridgema.gov/cdd/projects/zoning/multifamilyhousing",
    },
}

def normalize_address(address):
    """Normalize only an unambiguous spelled-out leading house number."""
    words={'one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8','nine':'9','ten':'10'}
    return re.sub(r"^\s*(one|two|three|four|five|six|seven|eight|nine|ten)(?=\s+)",
                  lambda m:words[m.group(1).lower()],str(address or ''),flags=re.I)

def requested_house_number(address):
    """Return the explicitly requested house number, never Census block-range fromAddress."""
    segment=normalize_address(address).split(",")[0]
    match=re.match(r"^\s*(\d+[A-Za-z]?)(?=\s)",segment)
    return match.group(1).upper() if match else None

def clean(s):
    return re.sub(r"[^A-Z0-9]+", " ", str(s or "").upper()).strip()

STREET_TYPES = {
    "ST": "STREET", "RD": "ROAD", "AVE": "AVENUE", "AV": "AVENUE",
    "BLVD": "BOULEVARD", "DR": "DRIVE", "LN": "LANE", "CT": "COURT",
    "PL": "PLACE", "TER": "TERRACE", "PKWY": "PARKWAY", "CIR": "CIRCLE",
    "HWY": "HIGHWAY", "SQ": "SQUARE", "WH": "WHARF",
}
ORDINALS = dict(zip(
    ("1ST", "2ND", "3RD", "4TH", "5TH", "6TH", "7TH", "8TH", "9TH", "10TH"),
    ("FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH", "NINTH", "TENTH"),
))
DIRECTIONS = {"N": "NORTH", "NO": "NORTH", "S": "SOUTH", "SO": "SOUTH", "E": "EAST", "W": "WEST"}


def street_tokens(value):
    parts = clean(value).split()
    if not parts:
        return parts
    parts[0] = ORDINALS.get(parts[0], parts[0])
    if len(parts) > 1:
        parts[-1] = STREET_TYPES.get(parts[-1], parts[-1])
        if len(parts) > 2 or parts[-1] not in STREET_TYPES.values():
            parts[0] = DIRECTIONS.get(parts[0], parts[0])
            if parts[0] == "MT":
                parts[0] = "MOUNT"
    return ["MASSACHUSETTS", "AVENUE"] if parts == ["MASS", "AVENUE"] else parts


def same_street(actual, requested):
    a, b = street_tokens(actual), street_tokens(requested)
    if not a or not b:
        return False
    types = STREET_TYPES.values()
    return a == b or (a[-1] in types and a[:-1] == b) or (b[-1] in types and b[:-1] == a)


def street_type_conflicts(address, components):
    """Detect a known street type that Census replaced with another known type."""
    matched_type = clean(components.get("suffixType"))
    matched_type = STREET_TYPES.get(matched_type, matched_type)
    types = STREET_TYPES.values()
    if matched_type not in types or not requested_house_number(address):
        return False

    segment, _, remainder = normalize_address(address).partition(",")
    segment = re.split(r"\s*(?:#|\s(?:unit|apt\.?)\s+)", segment, maxsplit=1, flags=re.I)[0].strip()
    segment = re.sub(r"\s+(?:MA|Massachusetts)(?:\s+\d{5}(?:-\d{4})?)?$|\s+\d{5}(?:-\d{4})?$",
                     "", segment, flags=re.I)
    tokens = clean(segment).split()[1:]
    if not remainder or re.match(r"\s*(?:MA|Massachusetts|\d{5})\b", remainder, flags=re.I):
        localities = (clean(components.get(key)).split() for key in ("postal_city", "city"))
        for locality in sorted(localities, key=len, reverse=True):
            if locality and tokens[-len(locality):] == locality:
                tokens = tokens[:-len(locality)]
                break
    suffix_direction = clean(components.get("suffixDirection"))
    if tokens and suffix_direction and DIRECTIONS.get(tokens[-1], tokens[-1]) == DIRECTIONS.get(suffix_direction, suffix_direction):
        tokens.pop()
    if len(tokens) < 2:
        return False
    requested_type = STREET_TYPES.get(tokens[-1], tokens[-1])
    return requested_type in types and requested_type != matched_type


def exact_address_match(feature, house, street, city, postal_city=None):
    """Reject a nearby parcel unless its assessor address matches all supplied components."""
    if not house or not street or not city:
        return False
    if clean(feature.get("CITY")) != clean(city):
        return False
    site = str(feature.get("SITE_ADDR") or "").strip()
    numbers = set()
    addr_number = str(feature.get("ADDR_NUM") or "").strip()
    if addr_number:
        numbers.add(clean(addr_number))
    match = re.match(r"^\s*(\d+[A-Za-z]?)\b", site)
    if match:
        numbers.add(clean(match.group(1)))
    if not numbers or any(n != clean(house) for n in numbers):
        return False
    actual_street = str(feature.get("FULL_STR") or "").strip()
    if not actual_street:
        actual_street = re.sub(r"^\s*\d+[A-Za-z]?\s*", "", site)
    # Some assessors qualify repeated street names by village.
    qualifier = re.fullmatch(r"(.+?)\s+\(([^()]+)\)", actual_street)
    if qualifier and postal_city and clean(qualifier[2]) == clean(postal_city):
        actual_street = qualifier[1]
    return same_street(actual_street,street)

def lot_area_sqft(feature):
    value = feature.get("LOT_SIZE")
    if not isinstance(value, (float, int)) or value <= 0:
        return None
    units = clean(feature.get("LOT_UNITS"))
    if units in ("A", "AC", "ACRE", "ACRES"):
        return round(value * 43560)
    if units in ("S", "SF", "SQFT", "SQ FT", "SQUARE FEET", "SQUARE FOOT"):
        return round(value)
    return None

async def query(client, params, timeout=12.0):
    r = await client.get(SERVICE, params={"f":"json", "outFields":FIELDS,
        "returnGeometry":"false", "resultRecordCount":100, **params},
        timeout=httpx.Timeout(timeout, connect=4.0))
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise ValueError(str(data["error"].get("message","ArcGIS parcel error")))
    return data.get("features", [])

def polygon_from_rings(rings):
    """Read an ArcGIS exterior ring, excluding any enclosed interior rings."""
    candidates = []
    for ring in rings:
        if len(ring) < 4:
            continue
        poly = Polygon(ring)
        if poly.is_valid and poly.area > 0:
            candidates.append((ring, poly))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[1].area, reverse=True)
    outer_ring, outer = candidates[0]
    holes = [ring for ring, p in candidates[1:] if outer.contains(p.representative_point())]
    polygon = Polygon(outer_ring, holes)
    return polygon if polygon.is_valid and polygon.area > 0 else outer

async def selected_geometry(client, features, warnings, latitude):
    ids = list(dict.fromkeys(str(x.get("attributes",{}).get("OBJECTID"))
        for x in features if x.get("attributes",{}).get("OBJECTID") is not None))
    if not ids:
        warnings.append("Parcel geometry: selected assessor record contains no ArcGIS object ID.")
        return None
    try:
        r = await client.get(SERVICE, params={"f":"json","objectIds":",".join(ids[:12]),
            "outFields":"OBJECTID,MAP_PAR_ID,LOC_ID,POLY_TYPE","outSR":3857,
            "returnGeometry":"true","geometryPrecision":2,"resultRecordCount":15},
            timeout=httpx.Timeout(20.0, connect=4.0))
        r.raise_for_status()
        data=r.json()
        if "error" in data:
            raise ValueError(str(data["error"].get("message","ArcGIS geometry error")))
        selected=set(ids[:12])
        polygons=[]
        for item in data.get("features",[]):
            attrs=item.get("attributes") or {}
            if str(attrs.get("OBJECTID")) not in selected or clean(attrs.get("POLY_TYPE")) in ("ROW","RAIL ROW","WATER"):
                continue
            p=polygon_from_rings((item.get("geometry") or {}).get("rings") or [])
            if p is not None:
                polygons.append(p)
        if not polygons:
            warnings.append("Selected parcel has no usable GIS polygon.")
            return None
        geometry=unary_union(polygons)
        # ArcGIS sends EPSG:3857 meters. Convert to approximate local ground feet.
        scale=3.280839895 * math.cos(math.radians(latitude))
        return transform(lambda x,y,z=None: (x*scale,y*scale) if z is None else (x*scale,y*scale,z), geometry)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        warnings.append("Selected parcel polygon unavailable: "+type(exc).__name__+".")
        return None

async def resolve_parcel(client, lon, lat, components, warnings, requested_address):
    house = requested_house_number(requested_address)
    street = " ".join(str(components.get(key) or "").strip() for key in (
        "preQualifier", "preDirection", "preType", "streetName", "suffixType", "suffixDirection", "suffixQualifier",
    )).strip() if components.get("streetName") else ""
    city = str(components.get("city") or "").strip()
    if not all((house,street,city)):
        warnings.append("Geocoder did not provide enough address components to verify the parcel.")
        return None,None,None
    def quote(s): return s.replace("'","''")
    params = [
        {"geometry":f"{lon},{lat}","geometryType":"esriGeometryPoint",
         "inSR":4326,"spatialRel":"esriSpatialRelIntersects"},
        {"where":f"ADDR_NUM = '{quote(house)}' AND CITY = '{quote(city.upper())}'"}
    ]
    async def attempt(p, label):
        try:
            return await query(client,p)
        except (httpx.HTTPError, ValueError) as exc:
            warnings.append(label+": "+type(exc).__name__+".")
            return []
    responses=await asyncio.gather(attempt(params[0],"Point parcel search"),
                                   attempt(params[1],"Address parcel search"))
    def matches(features):
        return [f for f in features if clean((f.get("attributes") or {}).get("POLY_TYPE")) not in ("ROW","RAIL ROW","WATER")
                and exact_address_match(f.get("attributes") or {},house,street,city,components.get("postal_city"))]
    choices=matches(responses[0]+responses[1])
    if not choices:
        # A Census road-centreline point often falls outside the actual lot.
        # The envelope is a candidate search only; each hit still needs an exact address.
        dlon=0.0009
        dlat=0.0006
        nearby={"geometry":f"{lon-dlon},{lat-dlat},{lon+dlon},{lat+dlat}",
                "geometryType":"esriGeometryEnvelope","inSR":4326,
                "spatialRel":"esriSpatialRelIntersects","resultRecordCount":200}
        choices=matches(await attempt(nearby,"Nearby parcel search"))
    if not choices:
        # Explain why nearby candidates were rejected without exposing owner information.
        sample=[]
        for response in responses:
            for feature in response[:12]:
                a=feature.get("attributes") or {}
                sample.append(str(a.get("SITE_ADDR") or "")[:70])
        if sample:
            warnings.append("Assessor candidates did not match requested address; candidate site addresses: "+", ".join(dict.fromkeys(sample[:8])))
        warnings.append("No assessor parcel matched the exact house number, street and municipality; neighboring records withheld.")
        return None,None,None
    # Avoid a mix of spatial polygons and condo tax-account rows for different parcels.
    by_parcel={}
    for feat in choices:
        a=feat.get("attributes") or {}
        identifier=str(a.get("LOC_ID") or a.get("MAP_PAR_ID") or "")
        if identifier:
            by_parcel.setdefault(identifier,{})[str(a.get("OBJECTID"))]=feat
    if len(by_parcel)!=1:
        warnings.append("Multiple distinct parcel IDs match the address; a parcel must be selected.")
        return None,None,None
    features=list(next(iter(by_parcel.values())).values())
    records=[f.get("attributes") or {} for f in features]
    x=records[0]
    parcel={"parcel_number":x.get("MAP_PAR_ID") or x.get("LOC_ID"),
            "full_number":x.get("MAP_PAR_ID"),"loc_id":x.get("LOC_ID"),
            "type":x.get("POLY_TYPE"),"gis_area_sqft_approx":lot_area_sqft(x),
            "assessor_site_address":x.get("SITE_ADDR"),"fiscal_year":x.get("FY"),
            "last_edit":x.get("LAST_EDIT"),"assessor_account_count":len(records),"recorded_owners":list(dict.fromkeys(r["OWNER1"] for r in records if r.get("OWNER1"))),
            "ownership_source":"https://www.mass.gov/info-details/massgis-data-property-tax-parcels","area_source":"MassGIS assessor LOT_SIZE"}
    building=None
    if len(records)==1:
        parcel["last_transfer"]={"sale_date_reported":x.get("LS_DATE"),"price_reported":x.get("LS_PRICE"),
            "book":x.get("LS_BOOK"),"page":x.get("LS_PAGE"),"registered_reference":x.get("REG_ID"),
            "status":"Assessor-reported reference; deed and latest ownership not checked",
            "registry_url":"https://www.masslandrecords.com/"}
        building={"building_area_sqft":x.get("BLD_AREA"),
                  "residential_area_sqft":x.get("RES_AREA"),
                  "units":x.get("UNITS"),"year_built":x.get("YEAR_BUILT"),
                  "stories":x.get("STORIES"),"use_description":x.get("USE_DESC"),
                  "assessed_value":x.get("TOTAL_VAL"),"assessor_zoning":x.get("ZONING")}
    else:
        warnings.append("Multiple tax accounts share a parcel; building area and unit totals withheld.")
    polygon=await selected_geometry(client,features,warnings,lat)
    if polygon is not None:
        area=round(polygon.area)
        parcel["polygon_area_sqft_approx"]=area
        if not parcel.get("gis_area_sqft_approx"):
            parcel["gis_area_sqft_approx"]=area
            parcel["area_source"]="GIS polygon area, approximate"
        elif abs(area-parcel["gis_area_sqft_approx"])/parcel["gis_area_sqft_approx"]>0.2:
            warnings.append("Assessor lot area differs from mapped parcel polygon by more than 20%; verify boundaries.")
    return parcel,building,polygon

def conceptual_massing(polygon, parcel, building, city, district):
    rules=next((value for (town,zone),value in SUPPORTED_RULES.items() if clean(city)==clean(town) and clean(district)==clean(zone)), None)
    if rules is None or polygon is None or polygon.is_empty or polygon.area<=100:
        return None
    area=polygon.area
    # Frontage orientation is unknown. All-edge 10/5-foot inset is only a
    # geometric sensitivity, not a representation of legal zoning setbacks.
    open_cap=area*(1-rules["open_space_fraction"])
    footprint_low=max(0,min(polygon.buffer(-rules["front_setback_ft"],join_style=2).area,open_cap))
    footprint_high=max(0,min(polygon.buffer(-rules["side_rear_setback_ft"],join_style=2).area,open_cap))
    existing=(building or {}).get("building_area_sqft")
    existing=float(existing) if isinstance(existing,(int,float)) and existing>=0 else None
    base_low=round(footprint_low*rules["stories"])
    base_high=round(footprint_high*rules["stories"])
    result={"method":f"GIS polygon with {rules['side_rear_setback_ft']}–{rules['front_setback_ft']} ft all-edge inset sensitivity, {round(rules['open_space_fraction']*100)}% open-space cap and {rules['stories']}-story base scenario",
            "confidence":"conceptual, not a zoning determination",
            "parcel_polygon_area_sqft":round(area),
            "gross_area_low_sqft":base_low,"gross_area_high_sqft":base_high,
            "additional_area_low_sqft":round(max(0,base_low-existing)) if existing is not None else None,
            "additional_area_high_sqft":round(max(0,base_high-existing)) if existing is not None else None,
            "stories":rules["stories"],"height_ft":rules["height_ft"],
            "source":rules["source"],
            "limitations":"Conceptual envelope only. Confirm front, side and rear setbacks, qualifying open space, overlays, historic limits, Green Factor and zoning GFA. Cambridge also requires neighborhood outreach for buildings over three stories and 35 feet when Planning Board advisory consultation or special-permit review does not apply."}
    threshold = rules["inclusionary_min_lot_sqft"]
    assessor_area = parcel.get("gis_area_sqft_approx")
    if area >= threshold and (not assessor_area or assessor_area >= threshold):
        result["inclusionary_scenario"]={
            "stories":rules["inclusionary_stories"],
            "height_ft":rules["inclusionary_height_ft"],
            "affordable_fraction":rules["inclusionary_affordable_fraction"],
            "gross_area_low_sqft":round(footprint_low*rules["inclusionary_stories"]),
            "gross_area_high_sqft":round(footprint_high*rules["inclusionary_stories"]),
            "additional_area_low_sqft":round(max(0,footprint_low*rules["inclusionary_stories"]-existing)) if existing is not None else None,
            "additional_area_high_sqft":round(max(0,footprint_high*rules["inclusionary_stories"]-existing)) if existing is not None else None,
            "condition":"Qualifying inclusionary housing; affordable area and other eligibility rules must be independently verified."}
    return result
