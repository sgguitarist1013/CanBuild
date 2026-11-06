"""CanBuild Cambridge pilot: source-aware, conservative research prototype."""
from pathlib import Path
from datetime import datetime, timezone
import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
app = FastAPI(title="CanBuild Cambridge", version="0.2.0")
app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')

SOURCES = [
 {'id':'ordinance','name':'Cambridge zoning ordinance','url':'https://www.cambridgema.gov/cdd/zoninganddevelopment/zoning/ordinance','type':'official'},
 {'id':'maps','name':'Cambridge zoning maps','url':'https://www.cambridgema.gov/cdd/zoninganddevelopment/zoning/maps','type':'official'},
 {'id':'amendments','name':'Cambridge zoning amendments','url':'https://www.cambridgema.gov/cdd/zoninganddevelopment/zoning/amendments','type':'official'},
 {'id':'multifamily','name':'2025 multifamily zoning reform','url':'https://www.cambridgema.gov/cdd/projects/zoning/multifamilyhousing','type':'official'},
 {'id':'gis','name':'Cambridge GIS REST service','url':'https://gisserver.cambridgema.gov/arcgis/rest/services/VisionMap/MapServer/layers','type':'official'},
 {'id':'permits','name':'Cambridge Inspectional Services zoning','url':'https://www.cambridgema.gov/inspection/zoning','type':'official'},
]

class Scenario(BaseModel):
    lot_area: float = Field(gt=0, le=10000000)
    existing_floor_area: float = Field(ge=0, le=10000000)
    existing_footprint: float = Field(ge=0, le=10000000)
    existing_stories: float = Field(ge=0, le=100)
    proposed_stories: float = Field(gt=0, le=100)
    setbacks_front: float = Field(ge=0, le=1000)
    setbacks_rear: float = Field(ge=0, le=1000)
    setbacks_side: float = Field(ge=0, le=1000)
    lot_width: float = Field(gt=0, le=10000)
    lot_depth: float = Field(gt=0, le=10000)
    open_space_pct: float = Field(ge=0, le=100)
    coverage_pct: float = Field(ge=0, le=100)
    efficiency_pct: float = Field(gt=0, le=100)

@app.get('/')
def home():
    return FileResponse(ROOT/'static'/'index.html')

@app.get('/api/health')
def health():
    return {'ok':True,'version':'0.2.0','as_of':datetime.now(timezone.utc).isoformat()}

@app.get('/api/sources')
def sources():
    return {'sources':SOURCES, 'note':'Links are reference sources, not evidence of an automatic current amendment scan.'}

@app.get('/api/geocode')
async def geocode(address: str):
    if len(address.strip()) < 5 or len(address)>240:
        raise HTTPException(400,'Enter a complete street address.')
    try:
        async with httpx.AsyncClient(timeout=12,follow_redirects=True) as client:
            response=await client.get('https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress',params={
                'address':address,'benchmark':'Public_AR_Current','vintage':'Current_Current','format':'json'})
            response.raise_for_status()
            data=response.json()
        matches=data.get('result',{}).get('addressMatches',[])
        if not matches: return {'matched':False,'message':'No Census address match. Enter property details manually.'}
        m=matches[0]; comps=m.get('addressComponents',{})
        city=comps.get('city','')
        if city.lower()!='cambridge' or comps.get('state','').upper()!='MA':
            return {'matched':False,'message':'The address did not match Cambridge, Massachusetts.','suggestion':m.get('matchedAddress')}
        return {'matched':True,'address':m.get('matchedAddress'),'coordinates':m.get('coordinates'),
            'source':'US Census Geocoder','parcel_verified':False,'zoning_verified':False,
            'message':'Address located. Parcel, building and zoning information still require verification.'}
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(503,'Address lookup is temporarily unavailable. Manual analysis remains available.')

@app.post('/api/scenario')
def scenario(s:Scenario):
    buildable_width=max(0,s.lot_width-2*s.setbacks_side)
    buildable_depth=max(0,s.lot_depth-s.setbacks_front-s.setbacks_rear)
    rectangle_area=buildable_width*buildable_depth
    footprint=min(rectangle_area,s.lot_area*s.coverage_pct/100,s.lot_area*(1-s.open_space_pct/100))
    gross=footprint*s.proposed_stories
    added=max(0,gross-s.existing_floor_area)
    return {'theoretical_footprint_sqft':round(footprint), 'theoretical_total_floor_area_sqft':round(gross),
      'theoretical_additional_floor_area_sqft':round(added),'estimated_usable_additional_sqft':round(added*s.efficiency_pct/100),
      'inputs_are_verified':False,'zoning_determination':False,
      'warnings':['All dimensional limits are user-entered scenario assumptions, not verified Cambridge parcel-specific standards.',
       'Rectangular lot assumption; geometry, slopes, easements, overlays, existing nonconformities and code may materially reduce capacity.',
       'Residential FAR caps may not apply in many districts after the February 2025 reform; verify special district and overlay exceptions.',
       'Theoretical envelope does not establish buildability, permit eligibility or permissible number of units.']}
