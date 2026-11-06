"""Statewide assessor-reported local sale candidates from the MassGIS parcel layer."""
from datetime import date, datetime, timezone
import math
import httpx

SERVICE = "https://services1.arcgis.com/hGdibHYSPO59RG1h/ArcGIS/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query"
SOURCE = "https://www.mass.gov/info-details/massgis-data-property-tax-parcels"

def residential(use):
    text=str(use or "").upper()
    return any(x in text for x in ("RESID","SINGLE","MULTI","CONDO","APARTMENT","DWELLING","TWO FAM","THREE FAM"))

async def search(client, lon, lat, city, parcel, building, limit=5):
    if not city or not parcel:
        return {"status":"A verified parcel and municipality are required","candidates":[],"source_url":SOURCE}
    area=(building or {}).get("residential_area_sqft") or (building or {}).get("building_area_sqft")
    subject_use=(building or {}).get("use_description")
    since=date.today().replace(year=date.today().year-3).strftime("%Y%m%d")
    params={"f":"json","where":f"CITY = '{str(city).replace(chr(39),chr(39)*2)}' AND LS_PRICE > 0 AND LS_DATE >= '{since}'",
        "geometry":f"{lon},{lat}","geometryType":"esriGeometryPoint","inSR":4326,"distance":1609,
        "units":"esriSRUnit_Meter","spatialRel":"esriSpatialRelIntersects",
        "outFields":"OBJECTID,MAP_PAR_ID,LOC_ID,SITE_ADDR,CITY,LS_DATE,LS_PRICE,LS_BOOK,LS_PAGE,BLD_AREA,RES_AREA,UNITS,YEAR_BUILT,USE_DESC,LOT_SIZE,LOT_UNITS",
        "returnGeometry":"true","outSR":4326,"resultRecordCount":500,"orderByFields":"LS_DATE DESC"}
    try:
        response=await client.get(SERVICE,params=params,timeout=httpx.Timeout(18,connect=4))
        response.raise_for_status(); data=response.json()
        if data.get("error"): raise ValueError("MassGIS sales query failed")
    except (httpx.HTTPError,ValueError):
        return {"status":"MassGIS sale records are temporarily unavailable","candidates":[],"source_url":SOURCE}
    subject_id=str(parcel.get("loc_id") or parcel.get("parcel_number") or "")
    candidates=[]
    for feature in data.get("features",[]):
        a=feature.get("attributes") or {}; geom=feature.get("geometry") or {}
        if str(a.get("CITY") or "").upper()!=str(city).upper(): continue
        if str(a.get("LOC_ID") or a.get("MAP_PAR_ID") or "")==subject_id: continue
        price=a.get("LS_PRICE"); sold=str(a.get("LS_DATE") or "")
        if not isinstance(price,(int,float)) or price<10000 or len(sold)!=8: continue
        size=a.get("RES_AREA") or a.get("BLD_AREA")
        if area and size and not .5 <= size/area <= 2: continue
        use=a.get("USE_DESC")
        if subject_use and use and residential(subject_use)!=residential(use): continue
        xy=geom.get("x"),geom.get("y")
        if not all(isinstance(v,(int,float)) for v in xy): continue
        phi=math.radians(lat); dphi=math.radians(xy[1]-lat); dl=math.radians(xy[0]-lon)
        distance=3958.8*2*math.asin(math.sqrt(math.sin(dphi/2)**2+math.cos(phi)*math.cos(math.radians(xy[1]))*math.sin(dl/2)**2))
        candidates.append({"address":a.get("SITE_ADDR"),"sale_date":f"{sold[:4]}-{sold[4:6]}-{sold[6:]}",
            "sale_price":int(price),"gross_area_sqft":size,"price_per_gross_sqft":round(price/size) if size else None,
            "units":a.get("UNITS"),"use_description":use,"parcel_number":a.get("MAP_PAR_ID"),
            "record_reference":("Book "+str(a.get("LS_BOOK"))+" page "+str(a.get("LS_PAGE"))) if a.get("LS_BOOK") and a.get("LS_PAGE") else None,
            "distance_miles":round(distance,2),"source_url":SOURCE,"source_name":"MassGIS assessor-reported last sale"})
    candidates.sort(key=lambda c:(c["distance_miles"],-int(c["sale_date"].replace("-",""))))
    selected=candidates[:limit]; prices=sorted(x["sale_price"] for x in selected)
    median=(prices[len(prices)//2] if len(prices)%2 else round((prices[len(prices)//2-1]+prices[len(prices)//2])/2)) if prices else None
    return {"status":f"{len(selected)} local assessor-reported sale candidate(s)" if selected else "No sufficiently similar recent local sale records found",
        "candidates":selected,"median_sale_price":median,"count":len(selected),"source_url":SOURCE,
        "checked_at":datetime.now(timezone.utc).isoformat(),
        "method":"Within 1 mile, same municipality, reported sale in past 3 years, residential use screened, building area 50–200% of subject when available; nearest candidates first.",
        "limitations":"MassGIS assessor-reported last-sale data can be stale, bundled, non-arm's-length, or have differing property measurements. Confirm deed, transaction conditions, condition, units and adjustments before relying on these candidates. This is not an appraisal."}
