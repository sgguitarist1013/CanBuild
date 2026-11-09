"""Boston municipal assessor identity and recorded-sale research; never an appraisal."""
from datetime import date, datetime, timezone
import math
import re
import httpx
from parcel_engine import clean, same_street, requested_house_number

async def selected_polygon(client, pid, latitude):
    from municipal_zoning import esri_polygon, to_ground
    data = await fetch(client, 0, {"where": f"PID = '{pid}'", "outFields": "PID", "returnGeometry": "true", "outSR": 3857, "resultRecordCount": 2})
    features = data.get("features", [])
    if len(features) != 1:
        return None
    polygon = to_ground(esri_polygon(features[0].get("geometry")), latitude)
    return polygon if not polygon.is_empty else None

BASE='https://gis.boston.gov/arcgis/rest/services/Assessing/properties_boston_gov/MapServer'
SOURCE='https://properties.boston.gov/'
FIELDS='OBJECTID,PID,CM_ID,MAP_PAR_ID,LOC_ID,FULL_ADDRESS,ST_NUM,ST_NUM2,ST_NAME,UNIT_NUM,OWNER,LU,LUC,LU_DESC,GROSS_AREA,LIVING_AREA,LAND_SF,RES_UNITS,COM_UNITS,NUM_BLDGS,TOTAL_VALUE,YR_BUILT'

async def fetch(client, layer, params):
    r=await client.get(f'{BASE}/{layer}/query',params={'f':'json','outFields':FIELDS if layer==0 else '*','returnGeometry':'false',**({'orderByFields':'latest_sales_date DESC,parcel_id ASC'} if layer==11 else {}),**params},timeout=httpx.Timeout(25,connect=4))
    r.raise_for_status(); d=r.json()
    if d.get('error'):raise ValueError('Boston municipal query failed')
    return d


def record_link(pid):
    # Public lookup landing page; PID is shown separately for a reproducible search.
    return SOURCE


def matches(a,house,street):
    # Exact street and endpoint match. A number merely inside an assessor range is ambiguous.
    return str(a.get('ST_NUM') or '')==house and same_street(a.get('ST_NAME'),street) and not a.get('UNIT_NUM')


def candidate(a):
    return {'parcel_number':a.get('PID'),'address':a.get('FULL_ADDRESS'),'recorded_owner':a.get('OWNER'),
            'use_description':a.get('LU_DESC'),'source_url':SOURCE}


def to_result(a,method):
    parcel={'parcel_number':a['PID'],'loc_id':a.get('LOC_ID'),'assessor_site_address':a.get('FULL_ADDRESS'),
            'gis_area_sqft_approx':a.get('LAND_SF'),'fiscal_year':2026,'area_source':'Boston FY2026 assessor LAND_SF',
            'recorded_owners':[a['OWNER']] if a.get('OWNER') else [],'ownership_source':SOURCE,
            'data_source':'Boston FY2026 municipal assessor','match_method':method,'use_code':a.get('LUC')}
    building={'building_area_sqft':a.get('GROSS_AREA'),'residential_area_sqft':a.get('LIVING_AREA'),
              'units':a.get('RES_UNITS'),'commercial_units':a.get('COM_UNITS'),'year_built':a.get('YR_BUILT'),
              'use_description':a.get('LU_DESC'),'assessed_value':a.get('TOTAL_VALUE')}
    return parcel,building

async def lookup(client,lon,lat,parts,address,pid=None):
    house=requested_house_number(address);street=parts.get('streetName','')
    if pid:
        # Explicit user-selected Boston record, kept distinct from an address-verified match.
        d=await fetch(client,0,{'where':f"PID = '{pid}'",'resultRecordCount':20})
        rows=[x['attributes'] for x in d.get('features',[])]
        if len(rows)!=1:raise ValueError('Selected Boston parcel is not unique')
        return rows[0],[], 'User-selected Boston assessor record; address alias not verified'
    safe=street.replace("'","''")
    # Street predicate prevents broad house-number searches truncating in large cities.
    d=await fetch(client,0,{'where':f"ST_NUM = {int(house)} AND UPPER(ST_NAME) LIKE '{safe.upper().split()[0]}%'" if house and house.isdigit() and safe else '1=0','resultRecordCount':500})
    rows=[x['attributes'] for x in d.get('features',[])]
    exact={x['PID']:x for x in rows if matches(x,house,street)}
    if len(exact)==1:return next(iter(exact.values())),[], 'Exact Boston municipal street and house-number match'
    # Nearby records are candidates only. No owner, area or value is attributed to the subject yet.
    d=await fetch(client,0,{'geometry':f'{lon},{lat}','geometryType':'esriGeometryPoint','inSR':4326,
                          'distance':150,'units':'esriSRUnit_Meter','spatialRel':'esriSpatialRelIntersects','resultRecordCount':1000})
    nearby={x['attributes']['PID']:x['attributes'] for x in d.get('features',[])}
    words=set(clean(address.split(',')[0]).split())-{'1','ONE'}
    def relevance(a):
        text=set(clean(str(a.get('OWNER',''))+' '+str(a.get('FULL_ADDRESS',''))).split())
        return (len(words&text),not bool(a.get('UNIT_NUM')),bool(a.get('GROSS_AREA')))
    ordered=sorted(nearby.values(),key=relevance,reverse=True)
    return None,[candidate(x) for x in ordered[:20]],'No exact municipal address match; review candidate records'


def sale_date(value):
    if isinstance(value,(int,float)):
        try:return datetime.fromtimestamp(value/1000,timezone.utc).date()
        except (ValueError,OverflowError):return None
    text=str(value or '')
    for fmt in ('%Y-%m-%d','%Y%m%d','%m/%d/%Y'):
        try:return datetime.strptime(text[:10] if fmt=='%Y-%m-%d' else text,fmt).date()
        except ValueError:pass
    return None


def filter_sales(records,sales,subject,today=None):
    today=today or date.today(); result=[]; seen=set()
    by_pid={r.get('parcel_id'):r for r in sales}
    size=subject.get('GROSS_AREA')
    if not isinstance(size,(int,float)) or size<=0:return []
    for a in records:
        pid=a.get('PID');s=by_pid.get(pid,{})
        if pid in seen or pid==subject.get('PID') or a.get('LUC')!=subject.get('LUC'):continue
        area=a.get('GROSS_AREA'); price=s.get('latest_sales_price'); sold=sale_date(s.get('latest_sales_date'))
        if not area or not .5<=area/size<=2 or not sold or not 0<=(today-sold).days<=1096:continue
        if not isinstance(price,(int,float)) or price<10000:continue
        seen.add(pid)
        result.append({'parcel_number':pid,'address':a.get('FULL_ADDRESS'),'sale_date':sold.isoformat(),
                       'sale_price':price,'gross_area_sqft':area,'price_per_gross_sqft':round(price/area),
                       'use_description':a.get('LU_DESC'),'use_code':a.get('LUC'),
                       'record_reference':s.get('latest_bkgpcert'),'source_url':SOURCE})
    return sorted(result,key=lambda x:x['sale_date'],reverse=True)[:10]

async def sales_research(client,subject):
    out={'status':'unavailable','candidates':[], 'source_url':SOURCE,
         'method':'Boston recorded-sale candidates: same exact assessor use code, within 1,609 meters of selected parcel geometry, 50–200% of subject gross area, last 36 months, recorded price at least $10,000.',
         'limitations':'Candidate sales require review for arms-length status, bundled transfers, condition, leases, title, and measurement differences. This is not an appraisal or a complete sales history. Boston FY2026 characteristics can lag current conditions.'}
    pid=subject['PID'];code=subject.get('LUC');area=subject.get('GROSS_AREA')
    if not re.fullmatch(r'\d{10}',pid) or not code or not isinstance(area,(int,float)) or area<=0:
        out['status']='Subject use code and gross area required';return out
    # Use the selected parcel geometry, not the geocoder's possibly incorrect street point.
    d=await fetch(client,0,{'where':f"PID = '{pid}'",'outFields':'PID','returnGeometry':'true','outSR':4326,'resultRecordCount':2})
    fs=d.get('features',[])
    if len(fs)!=1 or not fs[0].get('geometry'):
        out['status']='Selected municipal parcel geometry unavailable';return out
    import json
    geom=fs[0]['geometry']
    d=await fetch(client,0,{'geometry':json.dumps(geom),'geometryType':'esriGeometryPolygon','inSR':4326,
                          'distance':1609,'units':'esriSRUnit_Meter','spatialRel':'esriSpatialRelIntersects',
                          'where':f"LUC = '{str(code).replace(chr(39),chr(39)*2)}' AND GROSS_AREA >= {area*.5} AND GROSS_AREA <= {area*2}",
                          'resultRecordCount':500})
    records=[x['attributes'] for x in d.get('features',[])]
    ids=sorted(set(x['PID'] for x in records if re.fullmatch(r'\d{10}',x.get('PID',''))))
    quoted=','.join("'"+i+"'" for i in sorted(set(ids)|{pid}))
    d2=await fetch(client,11,{'where':f'parcel_id IN ({quoted})','resultRecordCount':2000})
    sales=[x['attributes'] for x in d2.get('features',[])]
    subject_sales=[row for row in sales if row.get('parcel_id')==pid]
    if len(subject_sales)==1:
        sale=subject_sales[0]
        sold=sale_date(sale.get('latest_sales_date'))
        out['subject_transfer']={'sale_date_reported':sold.isoformat() if sold else None,
            'price_reported':sale.get('latest_sales_price'),'record_reference':sale.get('latest_bkgpcert')}
    out['candidates']=filter_sales(records,sales,subject)
    out['status']='Recorded sale candidates found' if out['candidates'] else 'No qualifying recorded sales found'
    out['truncated']=bool(d.get('exceededTransferLimit') or d2.get('exceededTransferLimit'))
    return out
