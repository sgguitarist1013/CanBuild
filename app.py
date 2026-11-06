"""CanBuild Cambridge: source-labeled address and parcel research. No zoning entitlement claims."""
from pathlib import Path
from datetime import datetime, timezone
import os, math, re
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).parent
app=FastAPI(title="CanBuild Cambridge",version="0.5.0")
app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")
CENSUS="https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
PARCELS="https://services1.arcgis.com/hGdibHYSPO59RG1h/ArcGIS/rest/services/Massachusetts_Property_Tax_Parcels/FeatureServer/0/query"
ZONES="https://data.cambridgema.gov/resource/yc3q-q3hf.json"
SOURCES=[
 {"name":"MassGIS statewide property tax parcels","url":"https://www.mass.gov/info-details/massgis-data-property-tax-parcels"},
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
def health(): return {"ok":True,"version":"0.5.0","time":now()}
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
            # This hosted MassGIS layer contains parcel geometry ONLY. Assessor
            # building characteristics live in a separate Assess table.
            p=await client.get(PARCELS,params={"f":"json","geometry":f"{lon},{lat}","geometryType":"esriGeometryPoint","inSR":4326,"spatialRel":"esriSpatialRelIntersects","outFields":"MAP_PAR_ID,LOC_ID,POLY_TYPE,Shape__Area","returnGeometry":"false","resultRecordCount":30})
            p.raise_for_status()
            data=p.json()
            if "error" in data: raise ValueError("ArcGIS parcel service rejected query")
            feats=[x.get("attributes",{}) for x in data.get("features",[]) if x.get("attributes",{}).get("POLY_TYPE") in ("FEE","TAX")]
            unique={str(x.get("LOC_ID") or x.get("MAP_PAR_ID")) for x in feats}
            if len(unique)==1 and feats:
                x=feats[0]
                area=x.get("Shape__Area")
                result["parcel"]={"parcel_number":x.get("MAP_PAR_ID"),"full_number":x.get("MAP_PAR_ID"),"loc_id":x.get("LOC_ID"),"type":x.get("POLY_TYPE"),"gis_area_sqft_approx":round(area*10.76391041671) if isinstance(area,(int,float)) else None,"assessor_site_address":None,"fiscal_year":None}
                result["verification"]["parcel"]="MassGIS polygon match (not surveyed)"
            elif len(unique)>1:result["warnings"].append("Multiple parcel polygons intersect the address point; manual verification required.")
            else:result["warnings"].append("Geocoded address point does not intersect a MassGIS parcel polygon.")
        except (httpx.HTTPError,ValueError,TypeError,KeyError):
            result["warnings"].append("MassGIS parcel polygon lookup unavailable.")
        result["warnings"].append("The MassGIS polygon API does not contain building area, units or year built. A separate standardized Assess data service must be connected.")
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
