"""CanBuild Massachusetts: statewide parcel matching with municipality-scoped zoning rules."""
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone
import asyncio
import os, math, re
import httpx
from shapely.geometry import shape
from shapely.ops import unary_union, transform
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from parcel_engine import resolve_parcel, conceptual_massing, normalize_address
import boston
import ma_sales
import municipal_zoning
from data_quality import review_building
from condo_analysis import CondoProgram, analyze_program, cambridge_new_build_sales, condo_sales
from monitoring import configure_monitoring
from redevelopment import analyze_redevelopment

configure_monitoring()

ROOT=Path(__file__).parent
app=FastAPI(title="CanBuild Massachusetts",version="0.15.0")
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
def health(): return {"ok":True,"version":"0.15.0","time":now()}
@app.get("/api/coverage")
def coverage():
    rows = [municipal_zoning.coverage(city) for city in municipal_zoning.MUNICIPALITIES]
    return {"municipalities": rows, "base_map_counts": dict(Counter(row["base_map_status"] for row in rows)),
        "note": "Base map counts measure connected sources. Current ordinances and parcel-specific capacity require separate review."}
@app.get("/api/sources")
def sources(municipality:str|None=None):
    local=municipal_zoning.REGISTRY.get((municipality or '').upper(),{})
    return {"sources":[SOURCES[0], {"name":"MassGIS Section 3A District Atlas","url":municipal_zoning.ATLAS_SOURCE}]+[{"name":label,"url":local[key]} for label,key in (('Municipal zoning map','map_url'),('Municipal planning department' if local.get('historical_zoning_only') else 'Municipal zoning ordinance','code_url')) if local.get(key)],
        "coverage":municipal_zoning.coverage(municipality or ''),"note":"Source links and coverage status; no automatic amendment certification."}
@app.post('/api/condo-scenario')
def condo_scenario(program:CondoProgram):
    return analyze_program(program)

@app.get('/api/condo-sales')
async def condo_references(municipality:str=Query(min_length=2,max_length=80,pattern=r"^[A-Za-z .'-]+$"),
        lon:float=Query(ge=-73.6,le=-69.8),lat:float=Query(ge=41,le=43),
        boston_pid:str|None=Query(default=None,pattern=r'^\d{10}$'),min_year_built:int|None=Query(default=None,ge=1800,le=datetime.now().year)):
    try:
        async with httpx.AsyncClient(timeout=25,follow_redirects=True) as client:
            return await condo_sales(client,lon,lat,municipality,boston_pid,min_year_built)
    except (httpx.HTTPError,ValueError,TypeError):
        raise HTTPException(503,'Condominium sale references are temporarily unavailable. Your scenario inputs are preserved; try again later.')
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
            parsed=re.match(r"^\s*(\d+[A-Za-z]?)\s+([A-Za-z][A-Za-z0-9 .'-]*)",query)
            if parsed:
                number=parsed.group(1).upper()
                prefix=parsed.group(2).strip().upper()
                def matching(value):
                    candidate=value.upper().strip()
                    candidate=re.sub(r"^\s*"+re.escape(number)+r"\s+","",candidate)
                    return candidate.startswith(prefix) and re.match(r"^\s*"+re.escape(number)+r"\b",value.upper()) is not None
                vals=[v for v in vals if matching(v)]
            elif re.match(r"^\s*\d+\s*$",query):
                vals=[v for v in vals if re.match(r"^\s*"+re.escape(query.strip())+r"\b",v)]

            return {"configured":True,"provider":"ArcGIS","suggestions":list(dict.fromkeys(vals))[:8]}
    except (httpx.HTTPError,ValueError):
        return {"configured":False,"suggestions":[]}
@app.get("/api/property")
async def property_lookup(address:str=Query(min_length=5,max_length=240), boston_pid:str|None=Query(default=None,pattern=r"^\d{10}$")):
    address=normalize_address(address)
    if not re.search(r"\b(MA|Massachusetts)\b",address,re.I): address=address.rstrip(", ") + ", MA"
    result={"query":address,"checked_at":now(),"parcel_candidates":[],"sales_research":{"status":"Sales connector not configured for this municipality","candidates":[]},"address":None,"coordinates":None,"parcel":None,"zoning":None,"existing_building":None,"zoning_potential":None,"recommendations":[],"constraints":[],"zoning_review":None,"verification":{"address":"unverified","parcel":"unverified","assessor":"not matched","zoning":"unverified","amendments":"not checked"},"warnings":[],"sources":[SOURCES[0].copy()]}
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
            parcel,building,polygon=(None,None,None) if parts.get("city","").upper()=="BOSTON" else await resolve_parcel(client,lon,lat,parts,result["warnings"],address)
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
        if parts.get("city","").upper()=="BOSTON":
            result["sources"]=[{"name":"Boston municipal assessor FY2026 and recorded sales","url":boston.SOURCE},{"name":"MassGIS statewide parcels","url":SOURCES[0]["url"]}]
            try:
                record,candidates,method=await boston.lookup(client,lon,lat,parts,address,boston_pid)
                result["parcel_candidates"]=candidates
                if record:
                    result["parcel"],result["existing_building"]=boston.to_result(record,method)
                    result["verification"]["parcel"]=method
                    result["verification"]["assessor"]="Boston municipal FY2026 record"
                    result["assessor_address"]=record.get("FULL_ADDRESS")
                    try:
                        polygon=await boston.selected_polygon(client,record['PID'],lat)
                    except (httpx.HTTPError,ValueError,TypeError):
                        result['warnings'].append('Selected Boston parcel polygon unavailable; zoning lookup withheld.')
                    try:result["sales_research"]=await boston.sales_research(client,record)
                    except (httpx.HTTPError,ValueError,TypeError):
                        result["sales_research"]={"status":"Boston sales service temporarily unavailable","candidates":[]}
                elif candidates:
                    result["warnings"].append("Municipal records use different address labels here. Select and verify a candidate parcel before attributing ownership or sales.")
            except (httpx.HTTPError,ValueError,TypeError):
                result["warnings"].append("Boston municipal records temporarily unavailable; retry the lookup.")
        elif result.get("parcel"):
            try:
                result["sales_research"]=await ma_sales.search(client,lon,lat,parts.get("city"),result["parcel"],result.get("existing_building"))
            except (httpx.HTTPError,ValueError,TypeError):
                result["sales_research"]={"status":"Statewide assessor sale lookup temporarily unavailable","candidates":[],"source_url":ma_sales.SOURCE}
        atlas_task = asyncio.create_task(municipal_zoning.multifamily_screen(
            client, parts.get("city") or "", polygon, lat, (result.get("parcel") or {}).get("loc_id"),
        ))
        if parts.get("city","").lower()=="cambridge":
          try:
            result['zoning']=await municipal_zoning.cambridge_lookup(client,polygon,lat)
            if result['zoning']:
                result['verification']['zoning']='Municipal GIS selected-parcel polygon match; overlays/amendments not checked'
            else:
                result['warnings'].append('Cambridge zoning did not cover the selected parcel uniquely; district withheld.')
          except (httpx.HTTPError,ValueError,TypeError):
            result["warnings"].append("Cambridge zoning GIS unavailable; district not verified.")
        elif parts.get('city','').upper() in municipal_zoning.LOCAL_CONNECTORS:
            try:
                local=await municipal_zoning.lookup(client,parts['city'],polygon,lat,result.get('parcel') or {})
            except (httpx.HTTPError,ValueError,TypeError):
                local={'zoning':None,'constraints':[],'review':None,'status':'Municipal spatial response could not be validated'}
            result['zoning']=local['zoning'];result['constraints']=local['constraints'];result['zoning_review']=local['review']
            if local['zoning']:
                result['verification']['zoning'] = (
                    'Historical zoning map match; current zoning unverified' if local['zoning'].get('historical_reference')
                    else 'Municipal GIS selected-parcel polygon snapshot; amendments and full overlay coverage not verified'
                )
                result['warnings'].append(local['zoning']['snapshot_note'])
            else:
                result['warnings'].append('Municipal zoning: '+local.get('status','selected parcel geometry unavailable')+'.')
        else:
            result["warnings"].append("Live municipal zoning connector is not yet configured for "+str(parts.get("city"))+". Assessor zoning codes are not treated as verified current zoning.")
        result["multifamily_screen"] = await atlas_task
    zoning = result.get('zoning') or {}
    historical = zoning.get('historical_reference')
    district = zoning.get('district')
    city=parts.get("city") or ""
    result['coverage']=municipal_zoning.coverage(city)
    local_sources=municipal_zoning.REGISTRY.get(city.upper(),{})
    for label,key in (('Municipal zoning map','map_url'),('Municipal planning department' if historical or local_sources.get('historical_zoning_only') else 'Municipal zoning ordinance','code_url')):
        if local_sources.get(key):
            result['sources'].append({'name':city.title()+' '+label,'url':local_sources[key]})
    result['sources'].append({'name':'Massachusetts Registry of Deeds search','url':'https://www.masslandrecords.com/'})
    result['warnings'] += review_building(result.get('existing_building'))
    observed=result.get('existing_building') or {}
    result['dwelling_unit_summary']={'existing_reported_total':observed.get('units'),
        'status':observed.get('unit_count_status','not reported'),'note':observed.get('unit_count_note'),
        'potential_total':None,'potential_status':'A defined unit mix and local approvals are required; a geometric floor-area scenario is not a permitted dwelling-unit count.'}
    if (result.get('existing_building') or {}).get('unit_count_status')=='conflict':
        result['recommendations'].append({'title':'Resolve the legal dwelling count','reason':result['existing_building']['unit_count_note'],
            'next_step':'Check the municipal property card, certificates of occupancy and permit history before evaluating a conversion or additional units.','confidence':'Source conflict'})
    multifamily = result.get("multifamily_screen") or {}
    if multifamily.get("districts"):
        names = ", ".join(d["name"] for d in multifamily["districts"])
        local_review = multifamily.get("local_rule_review")
        result["recommendations"].append({
            "title": local_review["title"] if local_review else "Evaluate the multifamily zoning option",
            "reason": f"The selected parcel intersects {names} in the state Section 3A atlas.",
            "next_step": local_review["next_step"] if local_review else "Ask local planning to confirm eligible uses, current district boundaries, dimensional standards, any ground-floor use requirements, district caps and affordability rules before setting a unit mix or pricing a condo sellout.",
            "confidence": "State compliance map intersection",
        })
    revere_references = [ref for c in result["constraints"] for ref in c.get("rule_references", [])]
    if revere_references:
        result["recommendations"].append({
            "title": "Compare Revere's base zoning and overlay options",
            "reason": "The city map flags " + ", ".join(ref["name"] for ref in revere_references) + " at the selected parcel.",
            "next_step": "Confirm the overlay boundary and eligible project area with Revere Planning. Compare the base and overlay approval paths, dimensional limits and affordability before setting a unit mix.",
            "confidence": "City GIS screening intersection",
        })
    hits=[c['name'] for c in result['constraints'] if c['status']=='mapped intersection']
    if hits:
        result['recommendations'].append({'title':'Review mapped site constraints','reason':', '.join(hits)+' intersect the selected parcel in the listed municipal layers.',
            'next_step':'Review each mapped layer and its governing rules with the municipality before planning excavation, foundations, demolition or a larger building. A map intersection is a screening flag, not a permit determination.','confidence':'Mapped screening intersection'})
    if result.get("parcel") and district and not historical:
        potential=conceptual_massing(polygon,result["parcel"],result.get("existing_building"),city,district)
        if potential:
            result["zoning_potential"]=potential
            result["warnings"].append("Buildable area is an indicative geometric scenario, not a zoning-compliant building design.")
        elif polygon is None:
            result["warnings"].append("Cannot calculate buildable envelope until selected parcel polygon is available.")
        else:
            result["warnings"].append("No verified zoning calculation rules configured for this municipality and district.")
    result["warnings"] += ["MassGIS assessor extracts can be stale; BLD_AREA and RES_AREA use different measurement conventions and may not equal zoning gross floor area.","Parcel maps are not legal surveys; legal units and additions require independent confirmation.","Only listed municipal layers are screened. Additional overlays, zoning amendments, site constraints and legal buildability remain unverified."]
    if not result.get("zoning"):
        result["recommendations"].append({"title":"Verify the property's zoning district","reason":"CanBuild could not confirm a base district for the selected parcel. Base district standards are needed to evaluate expansion.","next_step":"Use the municipality's official zoning map and confirm the parcel's base and overlay districts with its planning or building department.","confidence":"Not yet verified"})
    elif historical:
        triangle = city.upper() == "EVERETT" and district == "EDD"
        result["recommendations"].append({
            "title": "Check the Commercial Triangle mixed-use path" if triangle else f"Confirm current {city.title()} zoning",
            "reason": f"The historical map shows {zoning.get('district_description') or district} on the selected parcel.",
            "next_step": ("Ask Everett Planning to confirm current Section 33 eligibility, required commercial space, site plan review and dimensional standards before estimating dwelling yield."
                          if triangle else "Ask the local planning department for the current district, overlays, allowed building uses and dimensional standards before pricing a development scenario."),
            "confidence": "Historical zoning map",
        })
    elif result.get('zoning_review'):
        review=result['zoning_review']
        result['recommendations'].append({'title':'Prepare an addition review for '+result['zoning']['district'],
            'reason':review['lot_area_status'],'next_step':review['next_step'],'confidence':review['status']})
    elif not result.get("zoning_potential"):
        result["recommendations"].append({"title":"Check local expansion standards","reason":"The district is mapped, but a reviewed rule set for this district is not available in CanBuild.","next_step":"Confirm setbacks, height, lot coverage, open space, nonconforming-lot rules, overlays, and permit requirements with the municipality.","confidence":"District mapped; dimensional rules incomplete"})
    else:
        result["recommendations"].append({"title":"Review the preliminary zoning scenario","reason":"A district-specific scenario is available, subject to its stated assumptions and limitations.","next_step":"Have the city review a current survey and proposed footprint before paying for design work.","confidence":result["zoning_potential"].get("confidence")})
    result["redevelopment"] = analyze_redevelopment(result.get("zoning_potential"), result.get("existing_building"))
    if result["redevelopment"].get("best"):
        references = None
        try:
            async with asyncio.timeout(13), httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                if city.upper() == "CAMBRIDGE":
                    references = await cambridge_new_build_sales(client)
                else:
                    coords = result["coordinates"]
                    references = await condo_sales(client, coords["lon"], coords["lat"], city,
                        min_year_built=datetime.now().year - 4, radius_miles=3, new_build_only=True)
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError):
            pass
        candidates = (references or {}).get("candidates", [])
        result["redevelopment"] = analyze_redevelopment(
            result["zoning_potential"], result.get("existing_building"),
            candidates if references and not references["truncated"] else [],
        )
        result["redevelopment"]["sale_references"] = references
    return result

# Regression tests run with every deployment. A failing suite prevents startup.
@app.on_event("startup")
async def validate_startup():
    import asyncio, unittest
    def run_checks():
        suite=unittest.defaultTestLoader.discover(start_dir=str(ROOT/"tests"),
            top_level_dir=str(ROOT),pattern="test_parcel_engine.py")
        return unittest.TextTestRunner(verbosity=1).run(suite)
    # IsolatedAsyncioTestCase must run outside the already-active server event loop.
    checks=await asyncio.to_thread(run_checks)
    print("CANBUILD REGRESSION TESTS:",checks.testsRun,
          "passed" if checks.wasSuccessful() else "FAILED",flush=True)
    if not checks.wasSuccessful():
        raise RuntimeError("CanBuild failed parcel and zoning regression tests.")
    # Live integration check, independent of visitors. Logs only; no displayed guesses.
    async def smoke():
        try:
            result=await property_lookup("95 Magazine St, Cambridge, MA, 02139",None)
            p=result.get("parcel") or {}
            print("CANBUILD 95 MAGAZINE SMOKE:",
                "parcel_id=",p.get("parcel_number"),
                "site_address=",p.get("assessor_site_address"),
                "building_units=",(result.get("existing_building") or {}).get("units"),
                "geometry_available=",bool(result.get("zoning_potential")),
                "warnings=",result.get("warnings",[])[:4],flush=True)
        except Exception as exc:
            print("CANBUILD 95 MAGAZINE SMOKE ERROR:",type(exc).__name__,str(exc)[:120],flush=True)
    asyncio.create_task(smoke())
