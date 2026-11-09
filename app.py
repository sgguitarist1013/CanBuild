"""CanBuild Massachusetts: statewide parcel matching with municipality-scoped zoning rules."""
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone
import asyncio
import os, math, re
from secrets import compare_digest
import sqlite3
import httpx
from shapely.geometry import shape
from shapely.ops import unary_union, transform
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from parcel_engine import resolve_parcel, conceptual_massing, normalize_address, same_street
import boston
import ma_sales
import municipal_zoning
from data_quality import review_building
from condo_analysis import CondoProgram, analyze_program, cambridge_new_build_sales, condo_sales
from monitoring import configure_monitoring
from redevelopment import analyze_redevelopment
import zoning_research
import somerville
import registry
import registry_research
from valuation import current_valuation
import permit_research

configure_monitoring()

ROOT=Path(__file__).parent
VERSION = "0.16.12"
app=FastAPI(title="CanBuild Massachusetts",version=VERSION)
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
def health(): return {"ok":True,"version":VERSION,"time":now()}
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
class ZoningReviewDecision(BaseModel):
    decision: Literal["reviewed", "rejected"]
    note: str = Field(min_length=1, max_length=2000)


def require_reviewer(authorization):
    token = os.getenv("ZONING_REVIEW_TOKEN")
    if not token:
        raise HTTPException(404, "Reviewer access is not configured.")
    if not compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "Reviewer token required.")


@app.get("/api/internal/zoning-review", include_in_schema=False)
def zoning_review_queue(authorization: str | None = Header(default=None), limit: int = Query(default=40, ge=1, le=100)):
    require_reviewer(authorization)
    return {"pending": zoning_research.cache().queue(limit)}


@app.get("/api/internal/registry-review", include_in_schema=False)
def registry_review_queue(authorization: str | None = Header(default=None), limit: int = Query(default=40, ge=1, le=100)):
    require_reviewer(authorization)
    return {"pending": registry.review_queue(zoning_research.cache().connect, limit)}


@app.post("/api/internal/registry-review/{review_id}/research", include_in_schema=False)
async def research_registry_review(review_id: int, authorization: str | None = Header(default=None)):
    require_reviewer(authorization)
    pending = await asyncio.to_thread(registry.review_queue, zoning_research.cache().connect, 1, review_id)
    if not pending:
        raise HTTPException(404, "Pending deed review was not found.")
    lead = pending[0]
    municipal_website = zoning_research.WEBSITES.get(lead["municipality"], "")
    async with httpx.AsyncClient() as client:
        return await registry_research.find_sources(client, lead, municipal_website)


def queue_registry_review(city, items):
    try:
        registry.enqueue(zoning_research.cache().connect, city, items)
    except (OSError, sqlite3.Error):
        # An unavailable review store never changes what counts as confirmed.
        pass


@app.post("/api/internal/zoning-review/{revision}", include_in_schema=False)
def decide_zoning_review(revision: int, decision: ZoningReviewDecision, authorization: str | None = Header(default=None)):
    require_reviewer(authorization)
    try:
        updated = zoning_research.cache().review(revision, decision.decision, decision.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not updated:
        raise HTTPException(409, "The revision has changed, could not be refreshed, or was already reviewed.")
    return {"revision": revision, "decision": decision.decision}


async def search_zoning_sources(city, district, registered_url, section_urls=()):
    try:
        return await zoning_research.cache().search(city, district, registered_url, section_urls)
    except (OSError, sqlite3.Error):
        return None


@app.post('/api/condo-scenario')
def condo_scenario(program:CondoProgram):
    return analyze_program(program)

@app.get('/api/condo-sales')
async def condo_references(municipality:str=Query(min_length=2,max_length=80,pattern=r"^[A-Za-z .'-]+$"),
        lon:float=Query(ge=-73.6,le=-69.8),lat:float=Query(ge=41,le=43),
        boston_pid:str|None=Query(default=None,pattern=r'^\d{10}$'),min_year_built:int|None=Query(default=None,ge=1800,le=datetime.now().year)):
    try:
        async with httpx.AsyncClient(timeout=25,follow_redirects=True) as client:
            report = await condo_sales(client,lon,lat,municipality,boston_pid,min_year_built)
        report = registry.sale_report(municipality, report)
        queue_registry_review(municipality, report.get("pending_registry_review", []))
        return report
    except (httpx.HTTPError,ValueError,TypeError):
        raise HTTPException(503,'Condominium sale references are temporarily unavailable. Your scenario inputs are preserved; try again later.')
def matching_suggestions(values, query):
    # Ignore commas when matching a typed city. A state-only suffix is a search
    # boundary rather than part of the street prefix.
    prefix = re.sub(r",\s*(?:MA|Massachusetts)\s*$", "", query, flags=re.I)
    prefix = " ".join(prefix.upper().replace(",", " ").split())
    number = re.match(r"^\d+[A-Z]?(?=\s|$)", prefix)
    results = []
    for value in values:
        if not re.search(r",\s*(?:MA|Massachusetts)(?:\s|,|$)", value, re.I):
            continue
        candidate = " ".join(value.upper().replace(",", " ").split())
        if not candidate.startswith(prefix):
            continue
        if number and candidate.split()[0] != number[0]:
            continue
        results.append(value)
    return list(dict.fromkeys(results))[:8]


@app.get("/api/suggest")
async def suggest(q:str=Query(min_length=2,max_length=150)):
    """Massachusetts address suggestions, updated for each typed prefix."""
    query=normalize_address(q.strip())
    if len(query)<2:
        return {"suggestions":[]}
    key=os.getenv("GOOGLE_MAPS_API_KEY","")
    if key:
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                r=await client.post("https://places.googleapis.com/v1/places:autocomplete",json={"input":query,"includedRegionCodes":["us"],"includedPrimaryTypes":["street_address","route","premise","subpremise"],"locationBias":{"rectangle":{"low":{"latitude":41.2,"longitude":-73.6},"high":{"latitude":42.9,"longitude":-69.9}}}},headers={"X-Goog-Api-Key":key,"X-Goog-FieldMask":"suggestions.placePrediction.text.text"})
                r.raise_for_status()
                vals=[x.get("placePrediction",{}).get("text",{}).get("text","") for x in r.json().get("suggestions",[])]
                vals=matching_suggestions(vals,query)
                if vals:return {"configured":True,"provider":"Google Places","suggestions":vals}
        except (httpx.HTTPError,ValueError):
            pass
    try:
        async with httpx.AsyncClient(timeout=9) as client:
            r=await client.get("https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/suggest",params={"f":"json","text":query if re.search(r"\b(MA|Massachusetts)\b",query,re.I) else query+", MA","searchExtent":"-73.6,41.2,-69.9,42.9","location":"-71.06,42.36","maxSuggestions":15,"category":"Address","countryCode":"USA"})
            r.raise_for_status()
            data=r.json()
            if "error" in data:raise ValueError("ArcGIS suggestion error")
            vals=[x.get("text","") for x in data.get("suggestions",[]) if x.get("text")]
            return {"configured":True,"provider":"ArcGIS","suggestions":matching_suggestions(vals,query)}
    except (httpx.HTTPError,ValueError):
        return {"configured":False,"suggestions":[]}
@app.get("/api/permits")
async def permit_history(municipality: str = Query(min_length=2, max_length=80, pattern=r"^[A-Za-z .'-]+$"),
                         address: str = Query(min_length=3, max_length=240),
                         parcel: str = Query(default="", max_length=40)):
    try:
        async with httpx.AsyncClient() as client:
            return await permit_research.search(client, municipality, address, parcel)
    except (httpx.HTTPError, ValueError, TypeError):
        return {**(permit_research.coverage(municipality) or {}), "status": "unavailable", "records": []}


CENSUS_GEOGRAPHIES = "https://www.census.gov/data/what-is-data-census-gov/guidance-for-data-users/frequently-asked-questions/how-do-i-search-by-address-using-the-census-geocoder.html"


def municipality_for_match(match):
    """Use a Massachusetts county subdivision before the mailing locality."""
    boundaries = (match.get("geographies") or {}).get("County Subdivisions") or []
    places = set()
    for area in boundaries:
        if area.get("STATE") != "25":
            continue
        name = registry.city_key(area.get("BASENAME") or re.sub(r" (?:city|town)$", "", area.get("NAME") or "", flags=re.I))
        if name not in registry.DISTRICTS and name.endswith(" TOWN"):
            name = registry.city_key(name.removesuffix(" TOWN"))
        if name in registry.DISTRICTS:
            places.add(name)
    if len(places) > 1:
        raise HTTPException(409, "The address matches multiple municipalities. Include the city or town and ZIP code.")
    if places:
        return next(iter(places)), "Census municipal boundary"
    postal_city = registry.city_key((match.get("addressComponents") or {}).get("city"))
    if postal_city in registry.DISTRICTS:
        return postal_city, "Census address locality"
    return "", None


@app.get("/api/property")
async def property_lookup(address:str=Query(min_length=5,max_length=240), boston_pid:str|None=Query(default=None,pattern=r"^\d{10}$")):
    address=normalize_address(re.sub(r",\s*(?:USA|United States(?: of America)?)\s*$", "", address, flags=re.I))
    if not re.search(r"\b(MA|Massachusetts)\b",address,re.I): address=address.rstrip(", ") + ", MA"
    result={"query":address,"checked_at":now(),"parcel_candidates":[],"sales_research":{"status":"Sales connector not configured for this municipality","candidates":[]},"address":None,"coordinates":None,"parcel":None,"zoning":None,"existing_building":None,"zoning_potential":None,"recommendations":[],"constraints":[],"zoning_review":None,"verification":{"address":"unverified","parcel":"unverified","assessor":"not matched","zoning":"unverified","amendments":"not checked"},"warnings":[],"sources":[SOURCES[0].copy()]}
    async with httpx.AsyncClient(timeout=13,follow_redirects=True) as client:
        somerville_street = municipal_zoning.somerville_street(address)
        geocoding_failed = False
        try:
            resp=await client.get(CENSUS,params={"address":address,"benchmark":"Public_AR_Current","vintage":"Current_Current","format":"json","layers":"County Subdivisions"})
            resp.raise_for_status()
            matches=resp.json().get("result",{}).get("addressMatches",[])
        except (httpx.HTTPError, ValueError):
            geocoding_failed = True
            matches = []
        if not matches and not somerville_street:
            raise HTTPException(503 if geocoding_failed else 404,
                "Census geocoding is temporarily unavailable." if geocoding_failed else
                "Address not matched. Check the street number and name.")
        m = matches[0] if matches else {}
        city, jurisdiction_source = municipality_for_match(m)
        other_cities = {municipality_for_match(match)[0] for match in matches[1:]}
        if city and other_cities - {"", city}:
            raise HTTPException(409, "The address matches multiple municipalities. Include the city or town and ZIP code.")
        parts = dict(m.get("addressComponents") or {})
        postal_city = parts.get("city")
        parts["city"] = city
        parts["postal_city"] = postal_city
        if m and parts.get("state", "").upper() != "MA":
            raise HTTPException(422, "CanBuild currently supports Massachusetts addresses.")
        c = m.get("coordinates") or {}
        lon, lat = c.get("x"), c.get("y")
        geocoded = isinstance(lat, (int, float)) and isinstance(lon, (int, float))
        if somerville_street:
            matched_street = (m.get("matchedAddress") or "").split(",")[0].split(maxsplit=1)
            requested_street = somerville_street.split(maxsplit=1)
            geocoded = (geocoded and parts.get("city", "").upper() == "SOMERVILLE"
                        and len(matched_street) == len(requested_street) == 2
                        and same_street(matched_street[1], requested_street[1]))
            if not geocoded:
                parts = {"city": "Somerville", "state": "MA"}
                jurisdiction_source = "Somerville address request"
        elif not geocoded:
            raise HTTPException(502, "Geocoder returned no coordinates.")
        result["address"] = m.get("matchedAddress") if geocoded else address
        if jurisdiction_source == "Census municipal boundary":
            result["sources"].append({"name": "Census municipality boundary lookup", "url": CENSUS_GEOGRAPHIES})
        if not parts["city"]:
            result["warnings"].append("Could not identify a Massachusetts city or town from the geocoder. Check the address and ZIP code.")
        if geocoded:
            result["coordinates"] = {"lat": lat, "lon": lon}
            result["verification"]["address"] = "Census geocoded (not Google-confirmed)"
        polygon=None
        try:
            parcel,building,polygon=(None,None,None) if not geocoded or parts.get("city","").upper()=="BOSTON" else await resolve_parcel(client,lon,lat,parts,result["warnings"],address)
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
        if somerville_street and polygon is None:
            try:
                city_match = await municipal_zoning.somerville_parcel(client, address)
            except (httpx.HTTPError, ValueError, TypeError):
                city_match = None
            if city_match:
                city_parcel, polygon, city_address, lon, lat, source = city_match
                existing = result["parcel"]
                if not existing or existing.get("parcel_number") != city_parcel["parcel_number"]:
                    result["parcel"] = city_parcel
                    result["existing_building"] = None
                    result["verification"]["assessor"] = "Assessor record unconfirmed"
                    result["sales_research"] = {"status": "Comparable sales require a matched assessor record", "candidates": []}
                result["address"] = city_address
                result["coordinates"] = {"lat": lat, "lon": lon}
                jurisdiction_source = "Somerville municipal GIS address match"
                result["verification"]["address"] = "Exact Somerville municipal GIS address"
                result["verification"]["parcel"] = "Somerville municipal parcel GIS; not a legal survey"
                result["sources"].append({"name": "Somerville city parcel GIS", "url": source})
            elif not geocoded:
                result["warnings"].append("The municipal parcel search could not confirm the requested Somerville street address.")
        if parts.get("city", "").upper() == "SOMERVILLE" and result.get("parcel"):
            try:
                municipal_building = await somerville.subject_building(client, result["parcel"]["parcel_number"])
            except (httpx.HTTPError, ValueError, TypeError):
                municipal_building = None
            if municipal_building:
                existing = result.get("existing_building")
                if existing:
                    existing.update({key: municipal_building[key] for key in ("bedrooms_reported", "bathrooms_reported")})
                else:
                    result["existing_building"] = municipal_building
                    result["verification"]["assessor"] = "Somerville municipal GIS assessor attributes; ownership and dwelling count unconfirmed"
        sales_task = None
        if parts.get("city","").upper()=="BOSTON":
            result["sources"].insert(0, {"name": "Boston municipal assessor FY2026 and recorded sales", "url": boston.SOURCE})
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
                    sales_task = asyncio.create_task(boston.sales_research(client, record))
                elif candidates:
                    result["warnings"].append("Municipal records use different address labels here. Select and verify a candidate parcel before attributing ownership or sales.")
            except (httpx.HTTPError,ValueError,TypeError):
                result["warnings"].append("Boston municipal records temporarily unavailable; retry the lookup.")
        elif result.get("parcel") and parts.get("city", "").upper() == "SOMERVILLE" and somerville.assessor_class(result["existing_building"]):
            sales_task = asyncio.create_task(somerville.sales(
                client, lon, lat, result["parcel"], result["existing_building"],
            ))
        elif result.get("parcel") and result["verification"]["assessor"] != "Assessor record unconfirmed":
            sales_task = asyncio.create_task(ma_sales.search(
                client, lon, lat, parts.get("city"), result["parcel"], result.get("existing_building"),
            ))
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
        if result.get("parcel") or somerville_street:
            city = parts.get("city") or ""
            matched_zoning = result.get("zoning") or {}
            district = None if matched_zoning.get("historical_reference") else matched_zoning.get("district")
            zoning_sources = municipal_zoning.REGISTRY.get(city.upper(), {})
            sections = zoning_research.somerville_section_urls(matched_zoning.get("regulatory_links", ())) if city.upper() == "SOMERVILLE" else ()
            result["on_demand_zoning_research"] = await search_zoning_sources(city, district, zoning_sources.get("code_url"), sections)
        result["multifamily_screen"] = await atlas_task
        if sales_task:
            try:
                result["sales_research"] = await sales_task
                subject_transfer = result["sales_research"].pop("subject_transfer", None)
                if subject_transfer and result.get("parcel"):
                    result["parcel"]["last_transfer"] = subject_transfer
            except (httpx.HTTPError, ValueError, TypeError):
                result["sales_research"] = {"status": "Comparable sales service temporarily unavailable", "candidates": []}
    zoning = result.get('zoning') or {}
    historical = zoning.get('historical_reference')
    district = zoning.get('district')
    city=parts.get("city") or ""
    result["municipality_match"] = {"municipality": city, "mailing_locality": postal_city, "source": jurisdiction_source} if city else None
    result['coverage']=municipal_zoning.coverage(city)
    if result.get('parcel'):
        result['permit_coverage']=permit_research.coverage(city)
    local_sources=municipal_zoning.REGISTRY.get(city.upper(),{})
    for label,key in (('Municipal zoning map','map_url'),('Municipal planning department' if historical or local_sources.get('historical_zoning_only') else 'Municipal zoning ordinance','code_url')):
        if local_sources.get(key):
            result['sources'].append({'name':city.title()+' '+label,'url':local_sources[key]})
    registry_route = registry.district_for(city)
    result["registry"] = registry_route
    result["sources"].append({"name": (registry_route["district"] or "Massachusetts") + " Registry of Deeds search", "url": registry_route["url"]})
    sale_report = registry.sale_report(city, result["sales_research"])
    result["sales_research"] = sale_report
    pending = list(sale_report.get("pending_registry_review", []))
    if result.get("parcel") and result["parcel"].get("assessor_account_count", 1) == 1:
        parcel = result["parcel"]
        transfer = parcel.get("last_transfer") or {}
        registry_address = parcel.get("assessor_site_address") or result["address"]
        deed = registry.check(city, registry_address, registry.transfer_pair(transfer),
                              transfer.get("price_reported"), transfer.get("registered_reference"))
        parcel["deed_verification"] = deed
        pending.append({"address": registry_address, "deed_verification": deed})
    result["current_valuation"] = current_valuation(result["parcel"], result.get("existing_building"), sale_report) if result.get("parcel") else None
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
    if result.get("parcel") and city.upper() == "SOMERVILLE" and district == "NR" and not historical:
        result["buildout_review"] = somerville.buildout(result["parcel"], result.get("existing_building"))
    parcel = result.get("parcel") or {}
    multi_account = parcel.get("assessor_account_count", 1) > 1
    condo_account = ma_sales.use_group((result.get("existing_building") or {}).get("use_description")) == "condominium"
    ambiguous_site = multi_account or condo_account
    if parcel and district and not historical and not ambiguous_site:
        potential=conceptual_massing(polygon,result["parcel"],result.get("existing_building"),city,district)
        if potential:
            result["zoning_potential"]=potential
            result["warnings"].append("Buildable area is an indicative geometric scenario, not a zoning-compliant building design.")
        elif polygon is None:
            result["warnings"].append("Cannot calculate buildable envelope until selected parcel polygon is available.")
        elif not result.get("buildout_review"):
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
    elif city.upper() == "SOMERVILLE" and not result.get("zoning_potential"):
        result["recommendations"].append({
            "title": "Review the " + district + " building types and standards",
            "reason": "CanBuild matched the city zoning district. Somerville development capacity depends on the applicable building type and use.",
            "next_step": "Open On-demand zoning research for the city ordinance sections. Confirm the building type, dimensional standards, overlays and approvals with Somerville Planning before estimating a buildout.",
            "confidence": "Municipal GIS match; automated buildout estimate unavailable",
        })
    elif ambiguous_site:
        result["recommendations"].append({
            "title": "Confirm which property rights are included",
            "reason": "The assessor reports a condo or multiple tax accounts for the lot.",
            "next_step": "Check the deeds and any master deed or condo documents before modeling redevelopment of the whole site.",
            "confidence": "Development site ownership unresolved",
        })
    elif not result.get("zoning_potential"):
        result["recommendations"].append({"title":"Check local expansion standards","reason":"The district is mapped, but a reviewed rule set for this district is not available in CanBuild.","next_step":"Confirm setbacks, height, lot coverage, open space, nonconforming-lot rules, overlays, and permit requirements with the municipality.","confidence":"District mapped; dimensional rules incomplete"})
    else:
        result["recommendations"].append({"title":"Review the preliminary zoning scenario","reason":"A district-specific scenario is available, subject to its stated assumptions and limitations.","next_step":"Have the city review a current survey and proposed footprint before paying for design work.","confidence":result["zoning_potential"].get("confidence")})
    market_range = (result.get("current_valuation") or {}).get("market_range")
    result["redevelopment"] = analyze_redevelopment(
        result.get("zoning_potential"), result.get("existing_building"), include_costs=False,
    )
    if result["redevelopment"].get("best"):
        references = None
        try:
            async with asyncio.timeout(13), httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                if city.upper() == "CAMBRIDGE":
                    coords = result["coordinates"]
                    references = await cambridge_new_build_sales(client, lon=coords["lon"], lat=coords["lat"])
                else:
                    coords = result["coordinates"]
                    references = await condo_sales(client, coords["lon"], coords["lat"], city,
                        min_year_built=datetime.now().year - 4, radius_miles=3, new_build_only=True)
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError):
            pass
        if references:
            references = registry.sale_report(city, references)
            pending.extend(references.get("pending_registry_review", []))
        candidates = (references or {}).get("candidates", [])
        subject_parcel = (result.get("parcel") or {}).get("parcel_number")
        comparable = [candidate for candidate in candidates if candidate.get("project_key") != subject_parcel]
        result["redevelopment"] = analyze_redevelopment(
            result["zoning_potential"], result.get("existing_building"),
            comparable if references and not references.get("truncated") else [],
            current_market_range=market_range,
        )
        result["redevelopment"]["sale_references"] = references
    if scenario := result["redevelopment"].get("best"):
        result["dwelling_unit_summary"].update(
            potential_total=scenario["market_units"] + scenario["restricted_units"],
            potential_status="Conceptual redevelopment program based on the screened zoning envelope.",
        )
    if pending:
        queue_registry_review(city, pending)
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
