"""CanBuild Massachusetts: statewide parcel matching with municipality-scoped zoning rules."""
from pathlib import Path
from datetime import datetime, timezone
import os, math, re
import httpx
from shapely.geometry import shape
from shapely.ops import unary_union, transform
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from parcel_engine import resolve_parcel, conceptual_massing

ROOT=Path(__file__).parent
app=FastAPI(title="CanBuild Massachusetts",version="0.8.0")
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
def health(): return {"ok":True,"version":"0.8.0","time":now()}
@app.get("/api/sources")
def sources(): return {"sources":SOURCES,"note":"Reference links only; no live amendment validation."}
@app.get("/api/suggest")
async def suggest(q:str=Query(min_length=2,max_length=150)):
    """Massachusetts address suggestions, updated for each typed prefix."""
    query=q.strip()
    if len(query)<2:
        return {"suggestions":[]}
    key=os.getenv("GOOGLE_MAPS_API_KEY","")
    if key:
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                r=await client.post("https://places.googleapis.com/v1/places:autocomplete",json={"input":query,"includedRegionCodes":["us"],"includedPrimaryTypes":["street_address","route","premise","subpremise"],"locationBias":{"rectangle":{"low":{"latitude":41.2,"longitude":-73.6},"high":{"latitude":42.9,"longitude":-69.9}}}},headers={"X-Goog-Api-Key":key,"X-Goog-FieldMask":"suggestions.placePrediction.text.text"})
                r.raise_for_status()
                vals=[x.get("placePrediction",{}).get("text",{}).get("text","") for x in r.json().get("suggestions",[])]
                vals=[v for v in vals if re.search(r"\b(MA|Massachusetts)\b",v,re.I)]
                if vals:return {"configured":True,"provider":"Google Places","suggestions":list(dict.fromkeys(vals))[:8]}
        except (httpx.HTTPError,ValueError):
            pass
    try:
        async with httpx.AsyncClient(timeout=9) as client:
            r=await client.get("https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/suggest",params={"f":"json","text":query if re.search(r"\b(MA|Massachusetts|Cambridge|Somerville|Boston)\b",query,re.I) else query+", Cambridge, MA","searchExtent":"-73.6,41.2,-69.9,42.9","location":"-71.06,42.36","maxSuggestions":15,"category":"Address","countryCode":"USA"})
            r.raise_for_status()
            data=r.json()
            if "error" in data:raise ValueError("ArcGIS suggestion error")
            vals=[x.get("text","") for x in data.get("suggestions",[]) if x.get("text")]
            # Require a literal house-number and street-prefix match; geocoder rank alone is insufficient.
            parsed=re.match(r"^\\s*(\\d+[A-Za-z]?)\\s+([A-Za-z][A-Za-z0-9 .'-]*)",query)
            if parsed:
                number=parsed.group(1).upper()
                prefix=parsed.group(2).strip().upper()
                def matching(value):
                    candidate=value.upper().strip()
                    candidate=re.sub(r"^\\s*"+re.escape(number)+r"\\s+","",candidate)
                    return candidate.startswith(prefix) and re.match(r"^\\s*"+re.escape(number)+r"\\b",value.upper()) is not None
                vals=[v for v in vals if matching(v)]
            elif re.match(r"^\\s*\\d+\\s*$",query):
                vals=[v for v in vals if re.match(r"^\\s*"+re.escape(query.strip())+r"\\b",v)]

            return {"configured":True,"provider":"ArcGIS","suggestions":list(dict.fromkeys(vals))[:8]}
    except (httpx.HTTPError,ValueError):
        return {"configured":False,"suggestions":[]}
@app.get("/api/property")
async def property_lookup(address:str=Query(min_length=5,max_length=240)):
    if not re.search(r"\b(MA|Massachusetts)\b",address,re.I): address=address.rstrip(", ") + ", MA"
    result={"query":address,"checked_at":now(),"address":None,"coordinates":None,"parcel":None,"zoning":None,"existing_building":None,"zoning_potential":None,"verification":{"address":"unverified","parcel":"unverified","assessor":"not matched","zoning":"unverified","amendments":"not checked"},"warnings":[],"sources":SOURCES}
    async with httpx.AsyncClient(timeout=13,follow_redirects=True) as client:
        try:
            resp=await client.get(CENSUS,params={"address":address,"benchmark":"Public_AR_Current","vintage":"Current_Current","format":"json"})
            resp.raise_for_status()
            matches=resp.json().get("result",{}).get("addressMatches",[])
        except (httpx.HTTPError,ValueError):
            raise HTTPException(503,"Census geocoding is temporarily unavailable.")
        if not matches: raise HTTPException(404,"Address not matched. Check the street number and name.")
        m=matches[0]; parts=m.get("addressComponents",{})
        if parts.get("state","").upper()!="MA":
            raise HTTPException(422,"CanBuild currently supports Massachusetts addresses.")
        c=m.get("coordinates",{}); lon,lat=c.get("x"),c.get("y")
        if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)): raise HTTPException(502,"Geocoder returned no coordinates.")
        result["address"]=m.get("matchedAddress");result["coordinates"]={"lat":lat,"lon":lon}
        result["verification"]["address"]="Census geocoded (not Google-confirmed)"
        polygon=None
        try:
            parcel,building,polygon=await resolve_parcel(client,lon,lat,parts,result["warnings"],address)
            if parcel:
                result["parcel"]=parcel
                result["existing_building"]=building
                result["verification"]["parcel"]="MassGIS address-verified parcel record; not a legal survey"
                if building:
                    result["verification"]["assessor"]="MassGIS assessor data, fiscal year "+str(parcel.get("fiscal_year") or "not specified")
                else:
                    result["verification"]["assessor"]="Multiple assessor records; totals withheld"
                print("Parcel match:",parcel.get("parcel_number"),"polygon:",polygon is not None,flush=True)
            else:
                print("Parcel match unavailable for",parts.get("city","MA"),flush=True)
        except Exception as exc:
            result["warnings"].append("Statewide parcel matcher unavailable: "+type(exc).__name__+".")
            print("Statewide parcel lookup failed:",type(exc).__name__,str(exc)[:200],flush=True)
        if parts.get("city","").lower()=="cambridge":
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
            result["warnings"].append("Cambridge zoning GIS unavailable; district not verified.")
        else:
            result["warnings"].append("Live municipal zoning connector is not yet configured for "+str(parts.get("city"))+". Assessor zoning codes are not treated as verified current zoning.")
    district=(result.get("zoning") or {}).get("district")
    city=parts.get("city") or ""
    if result.get("parcel") and district:
        potential=conceptual_massing(polygon,result["parcel"],result.get("existing_building"),city,district)
        if potential:
            result["zoning_potential"]=potential
            result["warnings"].append("Buildable area is an indicative geometric scenario, not a zoning-compliant building design.")
        elif polygon is None:
            result["warnings"].append("Cannot calculate buildable envelope until selected parcel polygon is available.")
        else:
            result["warnings"].append("No verified zoning calculation rules configured for this municipality and district.")
    result["warnings"] += ["MassGIS assessor extracts can be stale; BLD_AREA and RES_AREA use different measurement conventions and may not equal zoning gross floor area.","Parcel maps are not legal surveys; legal units and additions require independent confirmation.","Zoning overlays, pending/adopted amendments, site constraints and legal buildability have NOT been automatically verified."]
    return result
