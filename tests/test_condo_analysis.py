from datetime import date, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app import app
from condo_analysis import CondoProgram, analyze_program, condo_sales, reference_summary
import app as server


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def test_unit_mix_keeps_retained_and_restricted_units_separate():
    program=CondoProgram(existing_units_reported=3,retained_units_outside_sellout=2,
        market_units=[{'label':'1BR','units':3,'area_sqft':900,'low_price_per_sqft':800,'high_price_per_sqft':1000},
                      {'label':'2BR','units':2,'area_sqft':1200,'low_price_per_sqft':700,'high_price_per_sqft':900}],
        restricted_units=1,restricted_sale_price_per_unit=250000)
    result=analyze_program(program)
    assert result['total_proposed_dwelling_units']==8
    assert result['condo_units_in_sellout']==6
    assert result['net_change_from_reported_existing']==5
    assert result['gross_sellout_low']==4090000
    assert result['gross_sellout_high']==5110000


def test_conversion_does_not_inflate_the_dwelling_count():
    result=analyze_program(CondoProgram(existing_units_reported=3,market_units=[{'units':3,'area_sqft':800,'low_price_per_sqft':1000,'high_price_per_sqft':1000}]))
    assert result['total_proposed_dwelling_units']==3
    assert result['net_change_from_reported_existing']==0
    assert result['gross_sellout_low']==2400000


def test_counts_are_available_without_fabricating_missing_prices():
    result=analyze_program(CondoProgram(market_units=[{'units':4,'area_sqft':1000}]))
    assert result['total_proposed_dwelling_units']==4
    assert result['net_change_from_reported_existing'] is None
    assert result['gross_sellout_low'] is None
    assert result['missing_inputs']


def test_restricted_units_require_their_own_price_cap():
    result=analyze_program(CondoProgram(restricted_units=2,market_units=[{'units':3,'area_sqft':1000,'low_price_per_sqft':1000,'high_price_per_sqft':1000}]))
    assert result['total_proposed_dwelling_units']==5
    assert result['gross_sellout_low'] is None
    assert any('price cap' in x for x in result['missing_inputs'])


@pytest.mark.parametrize('row',[{'units':-1},{'units':1.5},{'units':True},{'units':1,'area_sqft':0},{'units':1,'low_price_per_sqft':float('inf')},{'units':1,'low_price_per_sqft':1000,'high_price_per_sqft':800}])
def test_invalid_scenario_inputs_are_rejected(row):
    with pytest.raises(ValidationError):
        CondoProgram(market_units=[row])


def test_api_calculates_sellout_and_returns_useful_validation():
    client=TestClient(app)
    response=client.post('/api/condo-scenario',json={'existing_units_reported':1,'market_units':[{'units':2,'area_sqft':1000,'low_price_per_sqft':700,'high_price_per_sqft':900}]})
    assert response.status_code==200
    assert response.json()['gross_sellout_high']==1800000
    assert response.json()['total_proposed_dwelling_units']==2
    assert client.post('/api/condo-scenario',json={'market_units':[{'units':2,'low_price_per_sqft':1000,'high_price_per_sqft':800}]}).status_code==422


def test_manual_condo_endpoint_keeps_deed_confirmed_references_without_pricing_range(monkeypatch):
    candidates = [
        {"address": f"{i} Test St Unit 1", "sale_price": price, "price_per_living_sqft": price / 1000,
         "record_reference": f"100/{i}", "arm_length_verified": False, "new_construction_verified": False}
        for i, price in enumerate((700000, 800000, 900000, 1000000), 1)
    ]
    monkeypatch.setattr(server, "condo_sales", AsyncMock(return_value={
        "candidates": candidates, "observed_price_per_sqft_low": 700, "observed_price_per_sqft_high": 1000,
    }))
    def check_deed(city, address, pair, price, registered_reference=None):
        if price < 1000000:
            return {"status": "verified", "consideration": price}
        return {"status": "pending"}

    monkeypatch.setattr(server.registry, "check", check_deed)
    monkeypatch.setattr(server, "queue_registry_review", lambda *args: None)

    response = TestClient(app).get("/api/condo-sales", params={
        "municipality": "Cambridge", "lat": 42.36, "lon": -71.1,
    })

    assert response.status_code == 200
    report = response.json()
    assert report["observed_price_per_sqft_low"] is report["observed_price_per_sqft_high"] is None
    assert [sale["sale_price"] for sale in report["candidates"]] == [700000, 800000, 900000]
    assert report["pending_registry_review"] == [
        {"address": "4 Test St Unit 1", "deed_verification": {"status": "pending"}},
    ]


def test_observed_range_requires_three_unbundled_unit_references():
    rows=[{'sale_date':'2026-01-01','sale_price':500000,'price_per_living_sqft':500,'record_reference':'Book 1 page 1'},
          {'sale_date':'2026-01-01','sale_price':500000,'price_per_living_sqft':250,'record_reference':'Book 1 page 1'},
          {'sale_date':'2026-02-01','sale_price':700000,'price_per_living_sqft':700,'record_reference':'Book 2 page 1'}]
    result=reference_summary(rows,'https://example.org')
    assert len(result['candidates'])==1
    assert result['possible_bundled_records_excluded']==2
    assert result['observed_price_per_sqft_low'] is None


@pytest.mark.anyio
async def test_condo_references_do_not_use_whole_building_or_future_sales():
    sold=(date.today()-timedelta(days=10)).strftime('%Y%m%d')
    attrs={'CITY':'CAMBRIDGE','USE_DESC':'Residential Condominium','SITE_ADDR':'Test unit','LS_DATE':sold,'LS_PRICE':800000,'RES_AREA':1000,'YEAR_BUILT':2025}
    rows=[{'attributes':dict(attrs,PROP_ID=str(i),LS_PRICE=p)} for i,p in enumerate([700000,800000,900000])]
    rows += [{'attributes':dict(attrs,PROP_ID='future',LS_DATE=(date.today()+timedelta(days=1)).strftime('%Y%m%d'))},
             {'attributes':dict(attrs,PROP_ID='other',USE_DESC='Single Family Residential')},
             {'attributes':dict(attrs,PROP_ID='missing',RES_AREA=None)}]
    def handler(request):
        assert '%CONDO%' in request.url.params['where']
        assert request.url.params['returnGeometry']=='false'
        return httpx.Response(200,json={'features':rows})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await condo_sales(client,-71.1,42.3,'CAMBRIDGE')
    assert len(result['candidates'])==3
    assert result['observed_price_per_sqft_low']==700
    assert result['observed_price_per_sqft_high']==900
    assert all(not c['new_construction_verified'] and not c['arm_length_verified'] for c in result['candidates'])


@pytest.mark.anyio
async def test_boston_condo_references_use_selected_parcel_and_unit_living_area():
    sold=(date.today()-timedelta(days=10)).isoformat()
    def handler(request):
        params=request.url.params
        if '/11/query' in str(request.url):
            return httpx.Response(200,json={'features':[{'attributes':{'parcel_id':'0000000001','latest_sales_date':sold,'latest_sales_price':900000,'latest_bkgpcert':'Book 1 page 2'}}]})
        if params['where']=="PID = '0303029000'":
            return httpx.Response(200,json={'features':[{'attributes':{'PID':'0303029000'},'geometry':{'rings':[[[-71,42],[-71,42.01],[-70.99,42.01],[-71,42]]]}}]})
        assert params['geometryType']=='esriGeometryPolygon'
        assert 'CONDO' in params['where']
        return httpx.Response(200,json={'features':[{'attributes':{'PID':'0000000001','LU_DESC':'Residential Condo','FULL_ADDRESS':'Test condo','LIVING_AREA':1000,'GROSS_AREA':2000,'YR_BUILT':2025}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await condo_sales(client,-71,42,'BOSTON','0303029000',2021)
    assert result['candidates'][0]['price_per_living_sqft']==900
    assert result['candidates'][0]['living_area_sqft']==1000
    assert result['observed_price_per_sqft_high'] is None
