"""Explicit unit-mix scenarios and separately sourced condominium sale references."""
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from math import asin, cos, radians, sin, sqrt
from typing import Annotated

import httpx
from pydantic import BaseModel, Field, model_validator

import boston
import ma_sales
from municipal_zoning import CAMBRIDGE_PARCELS

CAMBRIDGE_ASSESSOR = "https://data.cambridgema.gov/d/waa7-ibdu"
CAMBRIDGE_ASSESSOR_API = "https://data.cambridgema.gov/resource/waa7-ibdu.json"
CAMBRIDGE_PARCELS = "https://services1.arcgis.com/WnzC35krSYGuYov4/ArcGIS/rest/services/Parcels/FeatureServer/0/query"

UnitCount = Annotated[int, Field(strict=True, ge=0, le=10000)]
MoneyRate = Annotated[Decimal, Field(gt=0, le=10000, allow_inf_nan=False)]


class UnitMix(BaseModel):
    label: str = Field(default="Condo", max_length=80)
    units: UnitCount = 0
    area_sqft: Annotated[Decimal, Field(gt=0, le=20000, allow_inf_nan=False)] | None = None
    low_price_per_sqft: MoneyRate | None = None
    high_price_per_sqft: MoneyRate | None = None

    @model_validator(mode='after')
    def ordered_prices(self):
        if self.low_price_per_sqft is not None and self.high_price_per_sqft is not None and self.low_price_per_sqft > self.high_price_per_sqft:
            raise ValueError('Low price per square foot must not exceed high price')
        return self


class CondoProgram(BaseModel):
    existing_units_reported: UnitCount | None = None
    retained_units_outside_sellout: UnitCount = 0
    market_units: list[UnitMix] = Field(default_factory=list, max_length=20)
    restricted_units: UnitCount = 0
    restricted_sale_price_per_unit: Annotated[Decimal, Field(ge=0, le=100000000, allow_inf_nan=False)] | None = None


def dollars(value):
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def analyze_program(program):
    market_count = sum(row.units for row in program.market_units)
    sales_count = market_count + program.restricted_units
    total_units = sales_count + program.retained_units_outside_sellout
    missing = []
    low = high = area = Decimal(0)
    if not sales_count:
        missing.append('Enter at least one condo unit in the sales program.')
    for row in program.market_units:
        if not row.units:
            continue
        if row.area_sqft is None or row.low_price_per_sqft is None or row.high_price_per_sqft is None:
            missing.append(f'{row.label}: enter unit size and both price-per-square-foot assumptions.')
            continue
        row_area = row.area_sqft * row.units
        area += row_area
        low += row_area * row.low_price_per_sqft
        high += row_area * row.high_price_per_sqft
    if program.restricted_units:
        if program.restricted_sale_price_per_unit is None:
            missing.append('Enter the supported sale-price cap for restricted/affordable units.')
        else:
            low += program.restricted_units * program.restricted_sale_price_per_unit
            high += program.restricted_units * program.restricted_sale_price_per_unit
    return {'status': 'Assumption-based scenario' if not missing else 'Incomplete assumptions',
        'existing_units_reported': program.existing_units_reported,
        'retained_units_outside_sellout': program.retained_units_outside_sellout,
        'market_condo_units': market_count, 'restricted_condo_units': program.restricted_units,
        'condo_units_in_sellout': sales_count, 'total_proposed_dwelling_units': total_units if sales_count else None,
        'net_change_from_reported_existing': total_units - program.existing_units_reported if sales_count and program.existing_units_reported is not None else None,
        'gross_sellout_low': dollars(low) if not missing else None,
        'gross_sellout_high': dollars(high) if not missing else None,
        'market_sellable_area_sqft': float(area) if not missing else None,
        'missing_inputs': missing,
        'method': 'Sum of market condo quantity × interior unit area × assumed sale price/sq ft, plus restricted condo quantity × entered restricted sale price. Retained units outside the sellout add to the dwelling total but contribute no sales revenue.',
        'limitations': 'Proposed unit counts are a user-defined building program, not an approved zoning capacity. Existing unit counts are observations supplied to the calculator. Gross sellout is before commissions, concessions, construction, financing, taxes and other costs; it is not profit. New-construction condition and inclusionary/affordable-housing compliance require separate verification.'}


def reference_summary(candidates, source_url, truncated=False, min_year_built=None, *, new_build_only=False, radius_miles=1, citywide=False, retain_unit_layouts=False):
    # A common recorded reference and price across multiple units can indicate a bundled deed.
    keys = [(c.get('record_reference'), c['sale_date'], c['sale_price']) for c in candidates]
    counts = Counter(key for key in keys if key[0])
    clean = [c for c, key in zip(candidates, keys) if not key[0] or counts[key] == 1]
    if new_build_only:
        # Sales close to the assessor's reported completion year are proxies
        # for first sales. Keep one representative record per project parcel.
        by_project = {}
        for c in sorted(clean, key=lambda c: c['sale_date'], reverse=True):
            year = c.get('year_built_reported')
            if not c.get('project_key') or not c.get('record_reference') or not isinstance(year, (int, float)):
                continue
            if 0 <= int(c['sale_date'][:4]) - year <= 1:
                if retain_unit_layouts:
                    key = (c['project_key'], c.get('bedrooms_reported'), c.get('bathrooms_reported'), c['living_area_sqft'])
                else:
                    key = (c['project_key'], c.get('bedrooms_reported')) if citywide else c['project_key']
                by_project.setdefault(key, c)
        clean = list(by_project.values())
    clean = sorted(clean, key=lambda c: c['sale_date'], reverse=True)[:250 if retain_unit_layouts else 30 if citywide else 10]
    rates = [c['price_per_living_sqft'] for c in clean]
    return {'status': f'{len(clean)} condominium sale reference(s)' if clean else 'No usable condominium sale references found',
        'candidates': clean, 'observed_price_per_sqft_low': min(rates) if len(rates) >= 3 else None,
        'observed_price_per_sqft_high': max(rates) if len(rates) >= 3 else None,
        'source_url': source_url, 'checked_at': datetime.now(timezone.utc).isoformat(),
        'truncated': truncated, 'possible_bundled_records_excluded': len(candidates)-len([c for c,key in zip(candidates,keys) if not key[0] or counts[key]==1]),
        'min_year_built':min_year_built,
        'radius_miles': radius_miles, 'new_build_screen': new_build_only,
        'method': ('Cambridge-wide assessor condominium records.' if citywide else f'Condominium records within approximately {radius_miles} mile(s).')+' Sale reported within three years and positive residential living area.'+(f' Reported year built is at least {min_year_built}.' if min_year_built else '')+' An observed min–max price/sq ft is shown only with at least three references.',
        'limitations': 'These are condominium sale references, not verified new-construction comps. Deed consideration, arm’s-length status, unit size, condition, parking, floor level, views and concessions need review before adopting a pricing assumption. An observed range is not a valuation.'}


def reference(attrs, price, sold, area, source_url):
    return {'address':attrs.get('SITE_ADDR') or attrs.get('FULL_ADDRESS'),
        'parcel_number':attrs.get('PROP_ID') or attrs.get('PID') or attrs.get('MAP_PAR_ID') or attrs.get('LOC_ID'),
        'sale_price':int(price), 'sale_date':sold.isoformat(), 'living_area_sqft':area,
        'price_per_living_sqft':round(price/area,2), 'year_built_reported':attrs.get('YEAR_BUILT') or attrs.get('YR_BUILT'),
        'record_reference':attrs.get('_record_reference'), 'source_url':source_url,
        'project_key':attrs.get('LOC_ID') or attrs.get('MAP_PAR_ID'),
        'new_construction_verified':False, 'arm_length_verified':False}


async def cambridge_new_build_sales(client, today=None, *, lon=None, lat=None):
    """Use the city assessor's condo records, including units without coordinates."""
    today = today or date.today()
    min_year = today.year - 4
    cutoff = today - timedelta(days=1096)
    response = await client.get(CAMBRIDGE_ASSESSOR_API, params={
        "$select": "pid,address,unit,gisid,stateclasscode,saleprice,saledate,book_page,condition_yearbuilt,interior_livingarea,interior_bedrooms,interior_fullbaths,interior_halfbaths",
        "$where": (
            f"stateclasscode = '102' AND condition_yearbuilt between {min_year} and {today.year} "
            f"AND saleprice >= 10000 AND saledate >= '{cutoff.isoformat()}T00:00:00'"
        ),
        "$order": "saledate DESC,pid",
        "$limit": 251,
    }, timeout=httpx.Timeout(8, connect=4))
    response.raise_for_status()
    records = response.json()
    if not isinstance(records, list):
        raise ValueError("Unexpected Cambridge assessor response")
    rows = []
    for row in records:
        sold = boston.sale_date(row.get("saledate"))
        book_page = re.fullmatch(r"([1-9][0-9]*)/([0-9]+)", row.get("book_page", "").strip())
        if row.get("stateclasscode") != "102" or not sold or not cutoff <= sold <= today or not book_page:
            continue
        try:
            price = int(row["saleprice"])
            area = int(row["interior_livingarea"])
            year = int(row["condition_yearbuilt"])
        except (KeyError, TypeError, ValueError):
            continue
        if price < 10000 or area <= 0 or not min_year <= year <= today.year:
            continue
        address = row.get("address")
        unit = row.get("unit")
        project = row.get("gisid")
        if not address or not project:
            continue
        attrs = {
            "SITE_ADDR": f"{address} Unit {unit}" if unit else address,
            "PROP_ID": row.get("pid"),
            "LOC_ID": project,
            "YEAR_BUILT": year,
            "_record_reference": f"Book {book_page[1]}, page {book_page[2]}",
        }
        comp = reference(attrs, price, sold, area, CAMBRIDGE_ASSESSOR)
        bedrooms = row.get("interior_bedrooms", "")
        comp["bedrooms_reported"] = int(bedrooms) if bedrooms.isdecimal() else None
        full, half = row.get("interior_fullbaths", ""), row.get("interior_halfbaths", "")
        comp["bathrooms_reported"] = int(full) + int(half or "0") / 2 if full.isdecimal() and (not half or half.isdecimal()) else None
        comp["market_scope"] = "Cambridge assessor · citywide"
        rows.append(comp)
    summary = reference_summary(rows, CAMBRIDGE_ASSESSOR, len(records) > 250, min_year,
                                new_build_only=True, radius_miles=None, citywide=True, retain_unit_layouts=lon is not None and lat is not None)
    if lon is not None and lat is not None:
        await attach_cambridge_distances(client, summary["candidates"], lon, lat)
    return summary


async def attach_cambridge_distances(client, candidates, lon, lat):
    # Condo unit records can lack coordinates. Join the city parcel map by its
    # published map-lot ID, then measure from the recorded parcel centroid.
    keys = sorted({r["project_key"] for r in candidates if re.fullmatch(r"[0-9A-Za-z-]+", r["project_key"])})
    if not keys:
        return
    response = await client.get(CAMBRIDGE_PARCELS, params={"f": "json",
        "where": "ML IN (" + ",".join(f"'{key}'" for key in keys) + ")",
        "outFields": "ML", "outSR": 4326, "returnGeometry": "true", "returnCentroid": "true",
        "geometryPrecision": 6, "resultRecordCount": 250}, timeout=httpx.Timeout(6, connect=3))
    response.raise_for_status()
    data = response.json()
    if data.get("error") or data.get("exceededTransferLimit"):
        return
    points = {}
    for feature in data.get("features", []):
        key = feature.get("attributes", {}).get("ML")
        point = ma_sales.candidate_point(feature)
        if key and point:
            points.setdefault(key, set()).add(point)
    for comp in candidates:
        matches = points.get(comp["project_key"], set())
        if len(matches) != 1:
            continue
        x, y = next(iter(matches))
        dx, dy = radians(x-lon), radians(y-lat)
        hav = sin(dy/2)**2 + cos(radians(lat)) * cos(radians(y)) * sin(dx/2)**2
        comp["distance_miles"] = 2 * 3958.8 * asin(sqrt(min(1, hav)))
        comp["market_scope"] = "Cambridge assessor sales"


async def condo_sales(client, lon, lat, city, boston_pid=None, min_year_built=None, *, radius_miles=1, new_build_only=False):
    today=date.today()
    if city.upper()=='BOSTON':
        params={'geometry':f'{lon},{lat}','geometryType':'esriGeometryPoint','inSR':4326,'distance':1609 * radius_miles,
            'units':'esriSRUnit_Meter','spatialRel':'esriSpatialRelIntersects',
            'where':"UPPER(LU_DESC) LIKE '%CONDO%' AND LIVING_AREA > 0"+(f' AND YR_BUILT >= {min_year_built}' if min_year_built else ''),'resultRecordCount':250}
        if boston_pid:
            parcel_data=await boston.fetch(client,0,{'where':f"PID = '{boston_pid}'",'outFields':'PID','returnGeometry':'true','outSR':4326,'resultRecordCount':2})
            features=parcel_data.get('features',[])
            if len(features)!=1 or not features[0].get('geometry'):
                raise ValueError('Selected Boston parcel geometry unavailable')
            import json
            params.update(geometry=json.dumps(features[0]['geometry']),geometryType='esriGeometryPolygon')
        data=await boston.fetch(client,0,params)
        records=[f['attributes'] for f in data.get('features',[])]
        ids=sorted({a['PID'] for a in records if str(a.get('PID','')).isdigit() and len(str(a['PID']))==10})
        if not ids:
            return reference_summary([],boston.SOURCE,bool(data.get('exceededTransferLimit')),min_year_built)
        sales=await boston.fetch(client,11,{'where':'parcel_id IN ('+','.join("'"+pid+"'" for pid in ids)+')','resultRecordCount':1000})
        by_pid={s['attributes'].get('parcel_id'):s['attributes'] for s in sales.get('features',[])}
        rows=[]
        for a in records:
            if 'CONDO' not in str(a.get('LU_DESC','')).upper():continue
            if any(word in str(a.get('LU_DESC','')).upper() for word in ('COMMERCIAL','INDUSTRIAL','PARKING','GARAGE','STORAGE')):continue
            if min_year_built and (not isinstance(a.get('YR_BUILT'),(int,float)) or not min_year_built<=a['YR_BUILT']<=today.year):continue
            s=by_pid.get(a.get('PID'),{});sold=boston.sale_date(s.get('latest_sales_date'));price=s.get('latest_sales_price');area=a.get('LIVING_AREA')
            if not sold or not 0<=(today-sold).days<=1096 or not isinstance(price,(int,float)) or price<10000 or not isinstance(area,(int,float)) or area<=0:
                continue
            a['_record_reference']=s.get('latest_bkgpcert')
            rows.append(reference(a,price,sold,area,boston.SOURCE))
        return reference_summary(rows,boston.SOURCE,bool(data.get('exceededTransferLimit') or sales.get('exceededTransferLimit')),min_year_built)
    response=await client.get(ma_sales.SERVICE,params={'f':'json',
        'where':"CITY = '"+city.upper().replace("'","''")+"' AND UPPER(USE_DESC) LIKE '%CONDO%' AND LS_PRICE >= 10000 AND RES_AREA > 0"+(f' AND YEAR_BUILT >= {min_year_built}' if min_year_built else ''),
        'geometry':f'{lon},{lat}','geometryType':'esriGeometryPoint','inSR':4326,'distance':1609 * radius_miles,'units':'esriSRUnit_Meter',
        'spatialRel':'esriSpatialRelIntersects','outFields':'OBJECTID,PROP_ID,MAP_PAR_ID,LOC_ID,SITE_ADDR,CITY,LS_DATE,LS_PRICE,LS_BOOK,LS_PAGE,RES_AREA,YEAR_BUILT,USE_DESC',
        'returnGeometry':'false','resultRecordCount':500,'orderByFields':'LS_DATE DESC'},timeout=httpx.Timeout(18,connect=4))
    response.raise_for_status();data=response.json()
    if data.get('error'):
        raise ValueError('Condominium reference query unavailable')
    rows=[];seen=set()
    for feature in data.get('features',[]):
        a=feature.get('attributes') or {};sold=boston.sale_date(a.get('LS_DATE'));price=a.get('LS_PRICE');area=a.get('RES_AREA')
        if str(a.get('CITY','')).upper()!=city.upper() or 'CONDO' not in str(a.get('USE_DESC','')).upper():continue
        if any(word in str(a.get('USE_DESC','')).upper() for word in ('COMMERCIAL','INDUSTRIAL','PARKING','GARAGE','STORAGE')):continue
        if min_year_built and (not isinstance(a.get('YEAR_BUILT'),(int,float)) or not min_year_built<=a['YEAR_BUILT']<=today.year):continue
        if not sold or not 0<=(today-sold).days<=1096 or not isinstance(price,(int,float)) or price<10000 or not isinstance(area,(int,float)) or area<=0:continue
        key=a.get('PROP_ID') or a.get('OBJECTID')
        if key is None or key in seen:continue
        seen.add(key)
        a['_record_reference']=f"Book {a['LS_BOOK']}, page {a['LS_PAGE']}" if a.get('LS_BOOK') and a.get('LS_PAGE') else None
        rows.append(reference(a,price,sold,area,ma_sales.SOURCE))
    return reference_summary(rows,ma_sales.SOURCE,bool(data.get('exceededTransferLimit')),min_year_built,
                             new_build_only=new_build_only,radius_miles=radius_miles)
