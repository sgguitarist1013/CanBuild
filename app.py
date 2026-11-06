"""CanBuild Cambridge: source-labeled address and parcel research. No zoning entitlement claims."""
from pathlib import Path
from datetime import datetime, timezone
import os, math, re
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).parent
app=FastAPI(title="CanBuild Cambridge",version="0.6.1")
app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")
CENSUS="https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
PARCELS="https://services1.arcgis.com/hGdibHYSPO59RG1h/ArcGIS/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query"
ZONES="https://data.cambridgema.gov/resource/yc3q-q3hf.json"
SOURCES=[
 {"name":"MassGIS statewide parcel and assessor extract","url":"https://www.mass.gov/info-details/massgis-data-property-tax-parcels"},
 {"name":"Cambridge assessor property database","url":"https://www.cambridgema.gov/Departments/assessing"},
 {"name":"Cambridge FY2027 parcel GIS","url":"https://www.cambridgema.gov/gis/gisdatadictionary/assessing/fy2027/assessing_parcelsfy2027"},
 {"name":"Cambridge zoning district GIS","url":"https://www.cambridgema.gov/gis/gisdatadictionary/cdd/cdd_zoningdistricts"},
 {"name":"Cambridge zoning ordinance","url":"https://www.cambridgema.gov/cdd/zoninganddevelopment/zoning/ordinance"},
 {"name":"Zoning amendments","url":"https://www.cambridgema.gov/cdd/zoninganddevelopment/zoning/amendments"}
]
def now(): return datetime.now(timezone.utc).isoformat()
@app.get("/")
def home(): return FileResponse(ROOT/"static"/"index.html")
@app.get("/api/health")
def health(): return {"ok":True,"version":"0.6.1","time":now()}
@app.get("/api/sources")
def sources(): return {"sources":SOURCES,"note":"Reference links only; no live amendment validation."}
@app.get("/api/suggest")
async def suggest(q:str=Query(min_length=3,max_length=150)):
    """Address suggestions from public ArcGIS geocoder, with optional Google Places."""
    key=os.getenv("GOOGLE_MAPS_API_KEY","")
    if key:
        try:
            async with httpx.AsyncClient(timeout=7) as client:
                r=await client.post("https://places.googleapis.com/v1/places:autocomplete",json={"input":q,"includedRegionCodes":["us"],"locationBias":{"circle":{"center":{"latitude":42.3736,"longitude":-71.1097},"radius":5000}}},headers={"X-Goog-Api-Key":key,"X-Goog-FieldMask":"suggestions.placePrediction.text.text"})
                r.raise_for_status()
                vals=[x.get("placePrediction",{}).get("text",{}).get("text","") for x in r.json().get("suggestions",[])]
                vals=[v for v in vals if "cambridge" in v.lower()][:6]
                if vals:return {"configured":True,"provider":"Google Places","suggestions":vals}
        except (httpx.HTTPError,ValueError):pass
    # Public ArcGIS World Geocoding endpoint: no API key needed for ordinary search.
    try:
        async with httpx.AsyncClient(timeout=9) as client:
            r=await client.get("https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/suggest",params={"f":"json","text":q if "cambridge" in q.lower() else q+", Cambridge, MA","searchExtent":"-71.17,42.34,-71.06,42.41","location":"-71.1097,42.3736","maxSuggestions":8,"category":"Address"})
            r.raise_for_status()
            data=r.json()
            if "error" in data: raise ValueError("ArcGIS suggestion service error")
            vals=[x.get("text","") for x in data.get("suggestions",[]) if "cambridge" in x.get("text","").lower()]
            return {"configured":True,"provider":"ArcGIS public geocoder","suggestions":list(dict.fromkeys(vals))[:6]}
    except (httpx.HTTPError,ValueError):
        return {"configured":False,"suggestions":[],"message":"Address suggestions temporarily unavailable; enter the complete address and select Analyze."}
@app.get("/api/property")
async def property_lookup(address:str=Query(min_length=5,max_length=240)):
    if not re.search(r"cambridge|,\s*ma\b",address,re.I): address=address.rstrip(", ") + ", Cambridge, MA"
    result={"query":address,"checked_at":now(),"address":None,"coordinates":None,"parcel":None,"zoning":None,"existing_building":None,"verification":{"address":"unverified","parcel":"unverified","assessor":"not matched","zoning":"unverified","amendments":"not checked"},"warnings":[],"sources":SOURCES}
    async with httpx.AsyncClient(timeout=13,follow_redirects=True) as client:
        try:
            resp=await client.get(CENSUS,params={"address":address,"benchmark":"Public_AR_Current","vintage":"Current_Current","format":"json"})
            resp.raise_for_status()
            matches=resp.json().get("result",{}).get("addressMatches",[])
        except (httpx.HTTPError,ValueError):
            raise HTTPException(503,"Census geocoding is temporarily unavailable.")
        if not matches: raise HTTPException(404,"Address not matched. Check the street number and name.")
        m=matches[0]; parts=m.get("addressComponents",{})
        if parts.get("city","").lower()!="cambridge" or parts.get("state","").upper()!="MA":
            raise HTTPException(422,"Matched address is outside Cambridge, MA.")
        c=m.get("coordinates",{}); lon,lat=c.get("x"),c.get("y")
        if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)): raise HTTPException(502,"Geocoder returned no coordinates.")
        result["address"]=m.get("matchedAddress");result["coordinates"]={"lat":lat,"lon":lon}
        result["verification"]["address"]="Census geocoded (not Google-confirmed)"
        try:
            # MassGIS hosted parcel layer is already a joined TaxPar/Assess export.
            # Query exact address first because Census address points can be in roads.
            fields="MAP_PAR_ID,LOC_ID,POLY_TYPE,Shape__Area,PROP_ID,SITE_ADDR,ADDR_NUM,FULL_STR,CITY,FY,LOT_SIZE,LOT_UNITS,BLD_AREA,RES_AREA,UNITS,YEAR_BUILT,STORIES,USE_DESC,USE_CODE,ZONING,TOTAL_VAL"
            house=str(parts.get("fromAddress") or "").strip()
            street=" ".join(str(parts.get(k) or "").strip() for k in ("preDirection","streetName","suffixType","suffixDirection")).strip()
            street_name=str(parts.get("streetName") or "").strip()
            def sqlsafe(v):return v.replace("'","''")
            feats=[]
            if house and street_name:
                where=f"UPPER(CITY) = 'CAMBRIDGE' AND ADDR_NUM = '{sqlsafe(house)}' AND UPPER(FULL_STR) LIKE '%{sqlsafe(street_name.upper())}%'"
                p=await client.get(PARCELS,params={"f":"json","where":where,"outFields":fields,"returnGeometry":"false","resultRecordCount":100})
                p.raise_for_status()
                data=p.json()
                if "error" in data:raise ValueError("MassGIS address query failed")
                feats=[x.get("attributes",{}) for x in data.get("features",[])]
                # Street name LIKE is broad: confirm the full street name from
                # the standardized assessor address, not merely a substring.
                def clean(v):return re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper()).strip()
                expected=clean(street_name)
                if expected:
                    feats=[x for x in feats if expected in clean(x.get("FULL_STR")).split(" ")]
            if not feats:
                # The assessor SITE_ADDR may not agree with standardized FULL_STR.
                if house and street_name:
                    where=f"UPPER(CITY) = 'CAMBRIDGE' AND UPPER(SITE_ADDR) LIKE '{sqlsafe(house)}%{sqlsafe(street_name.upper())}%'"
                    alt=await client.get(PARCELS,params={"f":"json","where":where,"outFields":fields,"returnGeometry":"false","resultRecordCount":100})
                    alt.raise_for_status()
                    alt_data=alt.json()
                    if "error" not in alt_data:feats=[x.get("attributes",{}) for x in alt_data.get("features",[])]
            if not feats:
                p=await client.get(PARCELS,params={"f":"json","geometry":f"{lon},{lat}","geometryType":"esriGeometryPoint","inSR":4326,"spatialRel":"esriSpatialRelIntersects","outFields":fields,"returnGeometry":"false","resultRecordCount":100})
                p.raise_for_status()
                data=p.json()
                if "error" in data:raise ValueError("MassGIS spatial query failed")
                feats=[x.get("attributes",{}) for x in data.get("features",[])]
                if house:
                    feats=[x for x in feats if str(x.get("ADDR_NUM") or "").strip()==house]
            feats=[x for x in feats if x.get("POLY_TYPE") not in ("ROW","WATER")]
            ids={str(x.get("MAP_PAR_ID") or x.get("LOC_ID")) for x in feats}
            if len(ids)==1 and feats:
                x=feats[0]
                area=x.get("Shape__Area")
                lot=x.get("LOT_SIZE")
                lot_unit=str(x.get("LOT_UNITS") or "").strip().upper()
                lot_sqft=(round(lot*43560) if lot_unit in ("A","AC","ACRES") else round(lot) if lot_unit in ("S","SF","SQFT","SQUARE FEET") else None) if isinstance(lot,(int,float)) else None
                result["parcel"]={"parcel_number":x.get("MAP_PAR_ID"),"full_number":x.get("MAP_PAR_ID"),"loc_id":x.get("LOC_ID"),"type":x.get("POLY_TYPE"),"gis_area_sqft_approx":lot_sqft,"assessor_site_address":x.get("SITE_ADDR"),"fiscal_year":x.get("FY")}
                result["verification"]["parcel"]="MassGIS assessor/parcel record match (not surveyed)"
                if len(feats)==1:
                    result["existing_building"]={"building_area_sqft":x.get("BLD_AREA"),"residential_area_sqft":x.get("RES_AREA"),"units":x.get("UNITS"),"year_built":x.get("YEAR_BUILT"),"stories":x.get("STORIES"),"use_description":x.get("USE_DESC"),"assessed_value":x.get("TOTAL_VAL"),"assessor_zoning":x.get("ZONING")}
                    result["verification"]["assessor"]="MassGIS standardized assessing extract, FY "+str(x.get("FY") or "unknown")
                else:
                    result["warnings"].append("Multiple assessor tax records share this parcel (e.g., condominiums). Select a specific unit before showing building data.")
            elif len(ids)>1:
                result["warnings"].append("Several MassGIS parcels match this address; parcel details require disambiguation.")
            else:
                result["warnings"].append("No MassGIS assessor parcel matched the address or point.")
        except (httpx.HTTPError,ValueError,TypeError,KeyError) as exc:
            result["warnings"].append("MassGIS property record query failed; no assessor values inferred.")
            print("MassGIS lookup failed:",type(exc).__name__,str(exc)[:300],flush=True)
        try:
            # Socrata geometry predicate, authoritative city zoning layer.
            predicate=f"intersects(the_geom, 'POINT ({lon} {lat})')"
            z=await client.get(ZONES,params={"$where":predicate,"$select":"zone_type,pud_type","$limit":"5"})
            z.raise_for_status(); zones=z.json()
            if len(zones)==1:
                result["zoning"]={"district":zones[0].get("zone_type"),"pud":zones[0].get("pud_type")}
                result["verification"]["zoning"]="GIS point match; overlays/amendments not checked"
            elif len(zones)>1:result["warnings"].append("Multiple zoning polygons intersect the point; boundary review needed.")
            else:result["warnings"].append("Zoning GIS did not return a unique district.")
        except (httpx.HTTPError,ValueError,TypeError):
            result["warnings"].append("Zoning GIS lookup unavailable; district not verified.")
    result["warnings"] += ["MassGIS assessor extracts can be stale; BLD_AREA and RES_AREA use different measurement conventions and may not equal zoning gross floor area.","Parcel maps are not legal surveys; legal units and additions require independent confirmation.","Zoning overlays, pending/adopted amendments, site constraints and legal buildability have NOT been automatically verified."]
    return result
