"""CanBuild Cambridge: source-labeled address and parcel research. No zoning entitlement claims."""
from pathlib import Path
from datetime import datetime, timezone
import os, math, re
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).parent
app=FastAPI(title="CanBuild Cambridge",version="0.3.0")
app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")
CENSUS="https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
PARCELS="https://services1.arcgis.com/WnzC35krSYGuYov4/ArcGIS/rest/services/Parcels/FeatureServer/0/query"
ZONES="https://data.cambridgema.gov/resource/yc3q-q3hf.json"
SOURCES=[
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
def health(): return {"ok":True,"version":"0.3.0","time":now()}
@app.get("/api/sources")
def sources(): return {"sources":SOURCES,"note":"Reference links only; no live amendment validation."}
@app.get("/api/suggest")
async def suggest(q:str=Query(min_length=3,max_length=150)):
    """Google Places predictions when configured; otherwise explicit fallback."""
    key=os.getenv("GOOGLE_MAPS_API_KEY","")
    if not key: return {"configured":False,"suggestions":[],"message":"Google Places requires a server-side API key and Google billing."}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r=await client.post("https://places.googleapis.com/v1/places:autocomplete",json={"input":q,"includedRegionCodes":["us"],"locationBias":{"circle":{"center":{"latitude":42.3736,"longitude":-71.1097},"radius":5000}}},headers={"X-Goog-Api-Key":key,"X-Goog-FieldMask":"suggestions.placePrediction.text.text"})
            r.raise_for_status()
            vals=[x.get("placePrediction",{}).get("text",{}).get("text","") for x in r.json().get("suggestions",[])]
        return {"configured":True,"suggestions":[v for v in vals if "cambridge" in v.lower()][:6]}
    except (httpx.HTTPError,ValueError): return {"configured":True,"suggestions":[],"message":"Google suggestions unavailable."}
@app.get("/api/property")
async def property_lookup(address:str=Query(min_length=5,max_length=240)):
    if not re.search(r"cambridge|,\s*ma\b",address,re.I): address=address.rstrip(", ") + ", Cambridge, MA"
    result={"query":address,"checked_at":now(),"address":None,"coordinates":None,"parcel":None,"zoning":None,"existing_building":None,"verification":{"address":"unverified","parcel":"unverified","assessor":"not connected","zoning":"unverified","amendments":"not checked"},"warnings":[],"sources":SOURCES}
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
            p=await client.get(PARCELS,params={"f":"json","geometry":f"{lon},{lat}","geometryType":"esriGeometryPoint","inSR":4326,"spatialRel":"esriSpatialRelIntersects","outFields":"Parcel_Num,Full_Num,Type,Shape__Area","returnGeometry":"false"})
            p.raise_for_status(); feats=p.json().get("features",[])
            if len(feats)==1:
                a=feats[0].get("attributes",{})
                result["parcel"]={"parcel_number":a.get("Parcel_Num"),"full_number":a.get("Full_Num"),"type":a.get("Type"),"gis_area_sqft_approx":round(float(a["Shape__Area"])*10.7639104) if isinstance(a.get("Shape__Area"),(int,float)) else None}
                result["verification"]["parcel"]="GIS spatial match (not assessor verified)"
            elif len(feats)>1:result["warnings"].append("Multiple parcels intersect the geocoded point; manual review required.")
            else:result["warnings"].append("No Cambridge GIS parcel intersected the address point.")
        except (httpx.HTTPError,ValueError,TypeError,KeyError):
            result["warnings"].append("Cambridge parcel GIS lookup unavailable.")
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
    result["warnings"] += ["Cambridge parcel polygons do not certify deeded lot area or existing building area.","Assessor building size, legal unit count and year built are not connected. Do not infer them from the GIS parcel.","Zoning overlays, pending/adopted amendments, site constraints and legal buildability have NOT been automatically verified."]
    return result
