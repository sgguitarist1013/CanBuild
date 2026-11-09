"""Statewide assessor-reported local sale candidates from the MassGIS parcel layer."""
from datetime import date, datetime, timezone, timedelta
import math
import httpx

import registry
from municipal_zoning import esri_polygon

SERVICE = "https://services1.arcgis.com/hGdibHYSPO59RG1h/ArcGIS/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query"
SOURCE = "https://www.mass.gov/info-details/massgis-data-property-tax-parcels"

def residential(use):
    text=str(use or "").upper()
    return any(x in text for x in ("RESID","SINGLE","MULTI","CONDO","APARTMENT","DWELLING","TWO FAM","THREE FAM"))

def use_group(use):
    text = str(use or "").upper().replace("-", " ")
    for words, group in ((('SINGLE', 'ONE FAM'), 'single family'), (('TWO FAM',), 'two family'), (('THREE FAM',), 'three family'), (('CONDO',), 'condominium'), (('APARTMENT', 'MULTI'), 'multifamily')):
        if any(word in text for word in words):
            return group
    return 'other residential' if residential(use) else 'nonresidential'

def candidate_point(feature):
    geometry = feature.get('centroid') or feature.get('geometry') or {}
    if all(isinstance(geometry.get(k), (int, float)) for k in ('x', 'y')):
        return geometry['x'], geometry['y']
    try:
        polygon = esri_polygon(feature.get('geometry'))
    except ValueError:
        return None
    if not polygon.is_empty:
        point = polygon.centroid
        return point.x, point.y
    return None

async def search(client, lon, lat, city, parcel, building, limit=12):
    if not city or not parcel:
        return {"status":"A verified parcel and municipality are required","candidates":[],"source_url":SOURCE}
    area=(building or {}).get("residential_area_sqft") or (building or {}).get("building_area_sqft")
    subject_use=(building or {}).get("use_description")
    if not isinstance(area, (int, float)) or area <= 0 or not subject_use:
        return {"status":"Comparable sales require reported subject size and use","candidates":[],"source_url":SOURCE}
    today = date.today()
    since=(today-timedelta(days=1827)).strftime("%Y%m%d")
    area_field = "RES_AREA" if (building or {}).get("residential_area_sqft") else "BLD_AREA"
    params={"f":"json","where":f"CITY = '{str(city).replace(chr(39),chr(39)*2)}' AND LS_PRICE > 0 AND LS_DATE >= '{since}'"
        f" AND {area_field} BETWEEN {area*.85} AND {area*1.15}",
        "geometry":f"{lon},{lat}","geometryType":"esriGeometryPoint","inSR":4326,"distance":4829,
        "units":"esriSRUnit_Meter","spatialRel":"esriSpatialRelIntersects",
        "outFields":"OBJECTID,MAP_PAR_ID,LOC_ID,SITE_ADDR,CITY,LS_DATE,LS_PRICE,LS_BOOK,LS_PAGE,BLD_AREA,RES_AREA,UNITS,YEAR_BUILT,USE_DESC,LOT_SIZE,LOT_UNITS",
        "returnGeometry":"true","returnCentroid":"true","outSR":4326,"resultRecordCount":500,"orderByFields":"LS_DATE DESC"}
    try:
        response=await client.get(SERVICE,params=params,timeout=httpx.Timeout(18,connect=4))
        response.raise_for_status(); data=response.json()
        if data.get("error"): raise ValueError("MassGIS sales query failed")
    except (httpx.HTTPError,ValueError):
        return {"status":"MassGIS sale records are temporarily unavailable","candidates":[],"source_url":SOURCE}
    subject_id=str(parcel.get("loc_id") or parcel.get("parcel_number") or "")
    candidates=[]; seen=set()
    for feature in data.get("features",[]):
        a=feature.get("attributes") or {}; geom=feature.get("geometry") or {}
        if str(a.get("CITY") or "").upper()!=str(city).upper(): continue
        if str(a.get("LOC_ID") or a.get("MAP_PAR_ID") or "")==subject_id: continue
        identifier=str(a.get("LOC_ID") or a.get("MAP_PAR_ID") or a.get("OBJECTID"))
        if identifier in seen: continue
        price=a.get("LS_PRICE"); sold=str(a.get("LS_DATE") or "")
        if not isinstance(price,(int,float)) or price<10000 or len(sold)!=8: continue
        try:
            sale_day=datetime.strptime(sold,'%Y%m%d').date()
        except ValueError: continue
        age = (today - sale_day).days
        if not 0 <= age <= 1827: continue
        # Compare like measurements: living area to living area, gross to gross.
        living=bool((building or {}).get('residential_area_sqft'))
        size=a.get('RES_AREA') if living else a.get('BLD_AREA')
        if not isinstance(area,(int,float)) or area<=0 or not isinstance(size,(int,float)) or size<=0 or abs(size - area) > .15 * area: continue
        use=a.get("USE_DESC")
        if not subject_use or not use or use_group(subject_use)!=use_group(use): continue
        if use_group(subject_use)=='nonresidential' and str(subject_use).upper()!=str(use).upper(): continue
        xy=candidate_point(feature)
        if xy is None: continue
        phi=math.radians(lat); dphi=math.radians(xy[1]-lat); dl=math.radians(xy[0]-lon)
        distance=3958.8*2*math.asin(math.sqrt(math.sin(dphi/2)**2+math.cos(phi)*math.cos(math.radians(xy[1]))*math.sin(dl/2)**2))
        if distance>3: continue
        seen.add(identifier)
        candidates.append({"address":a.get("SITE_ADDR"),"sale_date":f"{sold[:4]}-{sold[4:6]}-{sold[6:]}",
            "sale_price":int(price),"gross_area_sqft":a.get('BLD_AREA'),"price_per_gross_sqft":round(price/a['BLD_AREA']) if a.get('BLD_AREA') and a['BLD_AREA']>0 else None,
            "comparison_area_sqft":size,"comparison_area_basis":"residential living area" if living else "assessor building area",
            "price_per_comparison_sqft":round(price/size),"arm_length_status":"Not verified; assessor last-sale record only",
            "units":a.get("UNITS"),"use_description":use,"parcel_number":a.get("MAP_PAR_ID"),
            "record_reference":("Book "+str(a.get("LS_BOOK"))+" page "+str(a.get("LS_PAGE"))) if a.get("LS_BOOK") and a.get("LS_PAGE") else None,
            "distance_miles":round(distance,3),"source_url":SOURCE,"source_name":"MassGIS assessor-reported last sale",
            "older_reference":age > 1096})
    selected = registry.shortlist(city, candidates, limit)
    return {"status":f"{len(selected)} local assessor-reported sale candidate(s)" if selected else "No sufficiently similar recent local sale records found",
        "candidates":selected,"median_sale_price":None,"count":len(selected),"source_url":SOURCE,"expanded_search":True,
        "checked_at":datetime.now(timezone.utc).isoformat(),
        "truncated":bool(data.get('exceededTransferLimit')),
        "method":"Same municipality and property-use group, within 15% of subject size. Prefer sales within 0.5 mile, then widen up to 3 miles when needed. Sales older than 3 years, up to 5 years, are historical references. This source lacks room counts.",
        "limitations":"MassGIS assessor-reported last-sale data can be stale, bundled, non-arm's-length, or have differing property measurements. Confirm deed, transaction conditions, condition, units and adjustments before relying on these candidates. This is not an appraisal."}
