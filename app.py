"""CanBuild Cambridge: source-labeled address and parcel research. No zoning entitlement claims."""
from pathlib import Path
from datetime import datetime, timezone
import os, math, re
import httpx
from shapely.geometry import shape
from shapely.ops import unary_union, transform
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).parent
app=FastAPI(title="CanBuild Massachusetts",version="0.7.1")
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
def health(): return {"ok":True,"version":"0.7.1","time":now()}
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
        try:
            # Official MassGIS hosted feature layer, updated independently of CanBuild.
            # Query only indexed-style address fields; never download a static statewide snapshot.
            fields="MAP_PAR_ID,LOC_ID,POLY_TYPE,PROP_ID,SITE_ADDR,ADDR_NUM,FULL_STR,CITY,FY,LOT_SIZE,LOT_UNITS,BLD_AREA,RES_AREA,UNITS,YEAR_BUILT,STORIES,USE_DESC,ZONING,TOTAL_VAL,LAST_EDIT"
            house=str(parts.get("fromAddress") or "").strip()
            street_name=str(parts.get("streetName") or "").strip()
            city=str(parts.get("city") or "").strip()
            def safe(v):return v.replace("'","''")
            async def parcel_query(params):
                r=await client.get(PARCELS,params={"f":"json","outFields":fields,"returnGeometry":"false","resultRecordCount":30,**params},timeout=httpx.Timeout(8.0,connect=4.0))
                r.raise_for_status()
                data=r.json()
                if "error" in data:raise ValueError(str(data["error"].get("message","MassGIS query error")))
                return [x.get("attributes",{}) for x in data.get("features",[])]
            # Query geometry and assessor attributes independently; use bounded fallback queries.
            import asyncio
            queries=[
                {"geometry":f"{lon},{lat}","geometryType":"esriGeometryPoint","inSR":4326,"spatialRel":"esriSpatialRelIntersects"},
                {"where":f"ADDR_NUM = '{safe(house)}' AND CITY = '{safe(city.upper())}'"} if house and city else None
            ]
            async def attempt(params):
                if params is None:return []
                try:return await parcel_query(params)
                except (httpx.HTTPError,ValueError):return []
            results=await asyncio.gather(*(attempt(p) for p in queries))
            def norm(v):return re.sub(r"[^A-Z0-9]","",str(v or "").upper())
            feats=results[0]
            if house:
                matched=[x for x in feats if str(x.get("ADDR_NUM") or "").strip()==house]
                if matched:feats=matched
            if not feats and house:
                feats=[x for x in results[1] if not street_name or norm(street_name) in norm(x.get("FULL_STR"))]
            if not feats:
                result["warnings"].append("MassGIS did not return parcel records from spatial or address lookup.")
            feats=[x for x in feats if x.get("POLY_TYPE") not in ("ROW","WATER","RAIL_ROW")]
            ids={str(x.get("LOC_ID") or x.get("MAP_PAR_ID")) for x in feats}
            if len(ids)==1 and feats:
                x=feats[0];lot=x.get("LOT_SIZE")
                u=str(x.get("LOT_UNITS") or "").strip().upper()
                lot_sqft=round(lot*43560) if isinstance(lot,(int,float)) and u in ("A","AC","ACRES") else round(lot) if isinstance(lot,(int,float)) and u in ("S","SF","SQFT") else None
                result["parcel"]={"parcel_number":x.get("MAP_PAR_ID"),"full_number":x.get("MAP_PAR_ID"),"loc_id":x.get("LOC_ID"),"type":x.get("POLY_TYPE"),"gis_area_sqft_approx":lot_sqft,"assessor_site_address":x.get("SITE_ADDR"),"fiscal_year":x.get("FY"),"last_edit":x.get("LAST_EDIT")}
                result["verification"]["parcel"]="MassGIS live hosted parcel record; not a survey"
                if len(feats)==1:
                    result["existing_building"]={"building_area_sqft":x.get("BLD_AREA"),"residential_area_sqft":x.get("RES_AREA"),"units":x.get("UNITS"),"year_built":x.get("YEAR_BUILT"),"stories":x.get("STORIES"),"use_description":x.get("USE_DESC"),"assessed_value":x.get("TOTAL_VAL"),"assessor_zoning":x.get("ZONING")}
                    result["verification"]["assessor"]="MassGIS assessor data, fiscal year "+str(x.get("FY") or "not specified")
                    use=str(x.get("USE_DESC") or "").lower()
                    expected_units=3 if "three-family" in use or "three family" in use else 2 if "two-family" in use or "two family" in use else None
                    reported_units=x.get("UNITS")
                    if expected_units is not None and reported_units is not None and str(reported_units).strip()!=str(expected_units):
                        result["existing_building"]["units"]=None
                        result["existing_building"]["reported_units"]=reported_units
                        result["existing_building"]["use_classification_units"]=expected_units
                        result["verification"]["assessor"]="CONFLICT: MassGIS unit count disagrees with property use classification"
                        result["warnings"].append("Conflicting assessor data: use classification suggests "+str(expected_units)+" units, but UNITS reports "+str(reported_units)+". Legal unit count requires municipal verification; neither value is assumed correct.")
                else:result["warnings"].append("Multiple assessor records match this parcel; building details withheld to avoid confusing condominium units.")
            elif len(ids)>1:result["warnings"].append("Multiple parcels matched this address; manual parcel selection needed.")
            else:result["warnings"].append("No unique MassGIS parcel matched; municipal assessor verification recommended.")
        except (httpx.HTTPError,ValueError,TypeError,KeyError) as exc:
            result["warnings"].append("Live MassGIS query unavailable ("+type(exc).__name__+"); assessor information not verified.")
            print("MassGIS lookup failed:",type(exc).__name__,str(exc)[:300],flush=True)
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
    if result.get("parcel") and result.get("zoning") and str(result["zoning"].get("district") or "").strip().upper() in ("C-1","RES C-1","RES. C-1"):
        try:
            loc=str(result["parcel"].get("loc_id") or "").replace("'","''")
            if loc:
                async with httpx.AsyncClient(timeout=20) as gc:
                    rr=await gc.get(PARCELS,params={"f":"geojson","where":"LOC_ID = '"+loc+"'","outFields":"LOC_ID,POLY_TYPE","returnGeometry":"true","outSR":"3857","resultRecordCount":15})
                    rr.raise_for_status()
                    features=rr.json().get("features",[])
                shapes=[shape(feat["geometry"]) for feat in features if feat.get("geometry") and (feat.get("properties") or {}).get("POLY_TYPE") not in ("ROW","WATER","RAIL_ROW")]
                if shapes:
                    g=unary_union(shapes)
                    factor=math.cos(math.radians(lat))*3.280839895
                    g=transform(lambda x,y,z=None:((x*factor,y*factor) if abs(x)>180 else ((6378137*math.radians(x))*factor,(6378137*math.log(math.tan(math.pi/4+math.radians(y)/2)))*factor)) if z is None else (x*factor,y*factor,z),g)
                    if g.is_valid and g.area>100:
                        cap=g.area*.7
                        low=min(max(0,g.buffer(-10,join_style=2).area),cap)*4
                        high=min(max(0,g.buffer(-5,join_style=2).area),cap)*4
                        old=(result.get("existing_building") or {}).get("building_area_sqft")
                        old=float(old) if isinstance(old,(float,int)) and old>=0 else None
                        result["zoning_potential"]={"method":"Mapped parcel polygon with 5–10 ft all-edge offset sensitivity, 30% open-space allowance, four stories","confidence":"low","parcel_polygon_area_sqft":round(g.area),"gross_area_low_sqft":round(low),"gross_area_high_sqft":round(high),"additional_area_low_sqft":round(max(0,low-old)) if old is not None else None,"additional_area_high_sqft":round(max(0,high-old)) if old is not None else None,"source":"https://www.cambridgema.gov/cdd/zoninganddevelopment/zoning/ordinance","limitations":"Conceptual massing only. All-edge buffers do not identify front/side/rear boundaries. Actual setbacks, reductions, overlays, historic restrictions, building footprint and zoning GFA are not verified."}
                        result["warnings"].append("Zoning potential is a low-confidence polygon-based massing range, NOT allowable floor area or approved development.")
        except Exception as exc:
            result["warnings"].append("Parcel geometry estimate unavailable; no floor-area estimate shown.")
            print("Geometry lookup:",type(exc).__name__,str(exc)[:200],flush=True)
    result["warnings"] += ["MassGIS assessor extracts can be stale; BLD_AREA and RES_AREA use different measurement conventions and may not equal zoning gross floor area.","Parcel maps are not legal surveys; legal units and additions require independent confirmation.","Zoning overlays, pending/adopted amendments, site constraints and legal buildability have NOT been automatically verified."]
    return result
