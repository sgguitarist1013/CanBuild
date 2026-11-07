from datetime import date, timedelta
import json

import httpx
import pytest
from shapely.geometry import box, mapping

from data_quality import review_building
from ma_sales import search
from municipal_zoning import district_match, esri_polygon, lookup, reference_review, to_ground, cambridge_lookup, lonlat_geometry, multifamily_screen

@pytest.fixture
def anyio_backend():
    return 'asyncio'


def test_zero_units_remains_an_unresolved_observation():
    building = {"units": 0, "use_description": "Single Family Residential"}
    warnings = review_building(building)
    assert building['units'] is None
    assert building['reported_units'] == 0
    assert building['use_classification_units'] == 1
    assert building['unit_count_status'] == 'conflict'
    assert warnings


@pytest.mark.parametrize('raw,expected,status', [(2, 2, 'assessor reported'), (0, None, 'not reported'), (None, None, 'not reported'), (-1, None, 'not reported')])
def test_units_without_residential_classification(raw, expected, status):
    building = {'units': raw, 'use_description': 'Office'}
    review_building(building)
    assert building['units'] == expected
    assert building['unit_count_status'] == status


def test_zoning_coverage_unions_duplicates_and_rejects_split_parcels():
    parcel = box(0, 0, 100, 100)
    assert district_match([({'zone': 'A'}, parcel, parcel.area)] * 2, 'zone', parcel.area) == 'A'
    split = [({'zone': 'A'}, box(0, 0, 60, 100), 6000), ({'zone': 'B'}, box(60, 0, 100, 100), 4000)]
    assert district_match(split, 'zone', parcel.area) is None


def test_esri_polygon_keeps_holes_and_disjoint_parts():
    geometry = {'rings': [list(box(0, 0, 10, 10).exterior.coords), list(box(2, 2, 4, 4).exterior.coords), list(box(20, 20, 22, 22).exterior.coords)]}
    assert esri_polygon(geometry).area == 100


@pytest.mark.anyio
async def test_municipal_zoning_uses_parcel_overlap_not_a_street_point():
    latitude = 42
    parcel = to_ground(box(0, 0, 100, 100), latitude)
    def handler(request):
        geometry = json.loads(request.url.params['geometry'])
        assert request.url.params['geometryType'] == 'esriGeometryPolygon'
        assert geometry['spatialReference']['wkid'] == 3857
        if '/4/query' in str(request.url):
            assert request.url.params['returnGeometry']=='true'
            rows=[{'attributes': {'ZoneCode': 'ResB'}, 'geometry': {'rings': [list(box(0, 0, 100, 100).exterior.coords)]}}]
        else:
            assert request.url.params['returnGeometry']=='false'
            rows=[]
        return httpx.Response(200, json={'features': rows})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await lookup(client, 'CHICOPEE', parcel, latitude, {'gis_area_sqft_approx': 7200})
    assert result['zoning']['district'] == 'ResB'
    assert result['zoning']['current_amendments_verified'] is False
    assert result['review']['standards']['rear_ft'] == 20
    assert 'nonconforming' in result['review']['lot_area_status']
    assert all(c['status'] == 'no mapped intersection' for c in result['constraints'])


@pytest.mark.anyio
async def test_unavailable_municipal_service_is_not_clearance():
    def handler(request):
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await lookup(client, 'BOSTON', box(0, 0, 100, 100), 42, {})
    assert result['zoning'] is None
    assert all(c['status'] == 'unavailable' for c in result['constraints'])


@pytest.mark.anyio
async def test_polygon_sales_candidates_use_same_area_and_property_type():
    sold = (date.today() - timedelta(days=90)).strftime('%Y%m%d')
    attrs = {'CITY': 'CHICOPEE', 'LOC_ID': 'candidate', 'MAP_PAR_ID': '2', 'SITE_ADDR': 'Test St', 'LS_DATE': sold, 'LS_PRICE': 300000, 'RES_AREA': 1200, 'BLD_AREA': 2400, 'USE_DESC': 'Single Family Residential'}
    def feature(changes):
        return {'attributes': dict(attrs, **changes), 'geometry': {'rings': [list(box(-72.6001, 42.1499, -72.5999, 42.1501).exterior.coords)]}}
    rows = [feature({}), feature({'LOC_ID': 'multifamily', 'USE_DESC': 'Two Family Residential'}), feature({'LOC_ID': 'future', 'LS_DATE': (date.today()+timedelta(days=1)).strftime('%Y%m%d')}), feature({'LOC_ID': 'missing', 'RES_AREA': None}), feature({'LOC_ID': 'subject'})]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'features': rows}))) as client:
        result = await search(client, -72.6, 42.15, 'CHICOPEE', {'loc_id': 'subject'}, {'residential_area_sqft': 1204, 'building_area_sqft': 2412, 'use_description': 'Single Family Residential'})
    assert len(result['candidates']) == 1
    candidate = result['candidates'][0]
    assert candidate['comparison_area_sqft'] == 1200
    assert candidate['comparison_area_basis'] == 'residential living area'
    assert candidate['gross_area_sqft'] == 2400
    assert candidate['price_per_comparison_sqft'] == 250
    assert candidate['price_per_gross_sqft'] == 125


def test_unknown_district_has_no_invented_rules():
    assert reference_review('Commercial', {'gis_area_sqft_approx': 7200}) is None


@pytest.mark.anyio
async def test_cambridge_matches_the_whole_parcel():
    parcel = box(0, 0, 100, 100)
    def handler(request):
        assert 'POLYGON' in request.url.params['$where']
        assert 'POINT' not in request.url.params['$where']
        return httpx.Response(200,json=[{'zone_type':'C-1','the_geom':mapping(lonlat_geometry(parcel,42))}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await cambridge_lookup(client,parcel,42)
    assert result['district'] == 'C-1'
    assert result['current_amendments_verified'] is False


@pytest.mark.anyio
async def test_somerville_uses_current_city_map_host_and_safe_rule_links():
    parcel = to_ground(box(0,0,100,100),42)
    def handler(request):
        assert request.url.host == 'cos-geoportal.somervillema.gov'
        rows=[]
        if '/ZoningByParcel/' in str(request.url):
            assert 'COMMITMENT_OWNER' not in request.url.params['outFields']
            rows=[{'attributes':{'ZONECODE':'MR4','ZONEDESC':'Mid Rise 4','Permitted_Building_Types':'https://online.encodeplus.com/regs/somerville-ma/doc-viewer.aspx#secid-470','Use_Provisions':'https://untrusted.example/rules'},'geometry':{'rings':[list(box(0,0,100,100).exterior.coords)]}}]
        return httpx.Response(200,json={'features':rows})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await lookup(client,'SOMERVILLE',parcel,42,{})
    assert result['zoning']['district']=='MR4'
    assert result['zoning']['source_updated_at']=='2025-06-17'
    assert len(result['zoning']['regulatory_links'])==1


@pytest.mark.anyio
async def test_constraint_screening_does_not_download_large_geometry():
    def handler(request):
        if '/4/query' in str(request.url):
            return httpx.Response(200,json={'features':[]})
        assert request.url.params['returnGeometry']=='false'
        return httpx.Response(200,json={'features':[{'attributes':{'OverlayDistrictName':'Test overlay'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await lookup(client,'CHICOPEE',box(0,0,100,100),42,{})
    assert all(c['status']=='mapped intersection' for c in result['constraints'])
    assert result['constraints'][0]['labels']==['Test overlay']


def test_geometry_size_limit_is_enforced():
    with pytest.raises(ValueError,match='processing limit'):
        esri_polygon({'rings':[[[0,0]]*250001]})


@pytest.mark.anyio
@pytest.mark.parametrize("district_width,atlas_id,units", [
    (100, "M_100_200", 24),
    (97.99, "M_100_200", None),
    (100, "M_100_201", None),
])
async def test_atlas_capacity_requires_full_district_and_exact_parcel(district_width, atlas_id, units):
    parcel = to_ground(box(0, 0, 100, 100), 42)
    paths = []

    def handler(request):
        paths.append(request.url.path)
        where = request.url.params['where']
        assert "UPPER(MUNI) = 'WATERTOWN'" in where
        if request.url.path.endswith('/0/query'):
            attrs = {'MUNI': 'Watertown', 'ZONECOD': '314WSQ2_TODO', 'ZONENAM': 'Transit district'}
            geometry = box(0, 0, district_width, 100)
        else:
            assert "LOC_ID = 'M_100_200'" in where
            attrs = {'MUNI': 'Watertown', 'ZONECOD': '314WSQ2_TODO', 'LOC_ID': atlas_id, 'F_L_M__': 24}
            geometry = box(0, 0, 100, 100)
        return httpx.Response(200, json={'features': [
            {'attributes': attrs, 'geometry': {'rings': [list(geometry.exterior.coords)]}},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await multifamily_screen(client, 'Watertown', parcel, 42, 'M_100_200')
    assert result['districts'][0]['name'] == 'Transit district'
    assert result['local_rule_review']['district_references'][0]['district'] == 'WSQ2'
    assert result['modeled_units'] == units
    assert len(paths) == (1 if district_width < 98 else 2)


@pytest.mark.anyio
async def test_atlas_incomplete_response_is_not_an_outside_district_result():
    response = {'features': [], 'exceededTransferLimit': True}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as client:
        result = await multifamily_screen(client, 'Chelsea', box(0, 0, 100, 100), 42, 'F_100_200')
    assert result['status'] == 'State atlas temporarily unavailable'
    assert result['modeled_units'] is None


@pytest.mark.anyio
async def test_everett_triangle_stays_a_historical_reference():
    parcel = to_ground(box(0, 0, 100, 100), 42)
    def handler(request):
        assert request.url.path.endswith('/Zoning_2019/FeatureServer/0/query')
        return httpx.Response(200, json={'features': [{
            'attributes': {'Zoning2018': 'EDD', 'ZoningName': 'Commercial Triangle Economic Development District'},
            'geometry': {'rings': [list(box(0, 0, 100, 100).exterior.coords)]},
        }]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await lookup(client, 'Everett', parcel, 42, {})
    assert result['zoning']['district'] == 'EDD'
    assert result['zoning']['historical_reference'] is True
    assert result['zoning']['current_amendments_verified'] is False
    assert result['zoning']['regulatory_links'][0]['name'] == 'City-published Section 33 excerpt'
    assert result['coverage']['base_zoning_connected'] is False
    assert result['review'] is None


def test_everett_repair_preserves_area_and_rejects_bow_tie():
    touching = {'rings': [[(0, 0), (5, 0), (3, 0), (10, 0), (10, 10), (0, 10), (0, 0)]]}
    with pytest.raises(ValueError):
        esri_polygon(touching)
    assert esri_polygon(touching, repair_ring=True).equals(box(0, 0, 10, 10))
    bow_tie = {'rings': [[(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)]]}
    with pytest.raises(ValueError):
        esri_polygon(bow_tie, repair_ring=True)


def test_watertown_overlay_references_keep_active_frontage_and_district_rules_separate():
    from municipal_zoning import watertown_overlay_review
    review = watertown_overlay_review([
        {"code": "314WSQ2_TODO"},
        {"code": "314WSQ2_TODO_Active_Frontage"},
    ])
    assert review["active_frontage_mapped"]
    assert review["district_references"] == [{
        "district": "WSQ2", "stories": "4+", "flat_roof_ft": 66,
        "pitched_roof_ft": 72, "minimum_open_space_pct": 10,
    }]
    assert not watertown_overlay_review([{"code": "314WSQ3_TODO"}])["active_frontage_mapped"]
    assert watertown_overlay_review([{"code": "314UNKNOWN"}]) is None
