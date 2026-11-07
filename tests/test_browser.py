import json
import pytest
from playwright.sync_api import expect


@pytest.fixture
def property_record():
    return {"address": "95 MAGAZINE ST, CAMBRIDGE, MA", "checked_at": "2026-11-06T12:00:00Z",
            "parcel": {"parcel_number": "test-95", "assessor_site_address": "95 MAGAZINE ST", "gis_area_sqft_approx": 4000},
            "existing_building": {"units": 2}, "verification": {}, "sources": [], "warnings": [],
            "zoning": None, "zoning_potential": None}


def test_search_displays_selected_property_and_withholds_estimates(page, app_url, property_record):
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": ["95 Magazine St, Cambridge, MA"]}))
    page.route("**/api/property?*", lambda route: route.fulfill(json=property_record))
    page.goto(app_url)
    page.get_by_role("combobox").fill("95 Mag")
    page.get_by_role("button", name="95 Magazine St, Cambridge, MA").click()
    expect(page.locator("#propertyAddress")).to_have_text("95 MAGAZINE ST, CAMBRIDGE, MA")
    expect(page.locator("#facts")).to_contain_text("95 MAGAZINE ST")
    expect(page.locator("#facts")).not_to_contain_text("91 MAGAZINE")
    expect(page.locator("#potentialArea")).to_have_text("Not yet estimated")
    expect(page.locator("#projectCost")).to_have_text("Pending verified rules")
    expect(page.locator("#go")).to_be_enabled()


def test_error_then_retry(page, app_url, property_record):
    calls = []
    def respond(route):
        calls.append(1)
        if len(calls) == 1:
            route.fulfill(status=503, json={"detail": "Census geocoding is temporarily unavailable."})
        else:
            route.fulfill(json=property_record)
    page.route("**/api/property?*", respond)
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    page.get_by_role("combobox").fill("95 Magazine St, Cambridge, MA")
    page.get_by_role("button", name="Explore property").click()
    expect(page.locator("#status")).to_contain_text("temporarily unavailable")
    expect(page.locator("#go")).to_be_enabled()
    page.get_by_role("combobox").press("Enter")
    expect(page.locator("#propertyAddress")).to_contain_text("95 MAGAZINE")


def test_mobile_layout_and_input_validation(page, app_url):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(app_url)
    page.get_by_role("button", name="Explore property").click()
    expect(page.locator("#status")).to_have_text("Enter a full Massachusetts address.")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate("window.scrollTo(1000, 0)")
    assert page.evaluate("window.scrollX === 0")
    assert page.evaluate("Array.from(document.body.querySelectorAll('*')).every(el => { const s=getComputedStyle(el), r=el.getBoundingClientRect(); return s.display === 'none' || s.visibility === 'hidden' || (r.left >= -1 && r.right <= window.innerWidth + 1) })")
    expect(page.locator('body')).not_to_contain_text('async function jsonResponse')


def test_property_conflicts_and_local_zoning_review(page, app_url, property_record):
    page.set_viewport_size({'width':390,'height':844})
    record=dict(property_record, existing_building={'units':None,'reported_units':0,'use_classification_units':1,'unit_count_status':'conflict','use_description':'Single Family Residential'},
        zoning={'district':'ResB','source_updated_at':'2025-01-01'},
        zoning_review={'section':'§ 275-53','standards':{'min_lot_sqft':7500,'frontage_ft':75,'front_ft':25,'side_ft':6,'rear_ft':20,'height_ft':40},'lot_area_status':'Below district minimum; nonconforming lot review needed','conditions':'Confirm ground-story exception.','limitations':'No permit approval.'},
        constraints=[{'name':'Historic districts','status':'unavailable','labels':[],'note':'No regulatory clearance.'}])
    page.route('**/api/property?*',lambda route:route.fulfill(json=record))
    page.route('**/api/suggest?*',lambda route:route.fulfill(json={'suggestions':[]}))
    page.goto(app_url)
    page.get_by_role('combobox').fill('95 Magazine St, Cambridge, MA')
    page.get_by_role('button',name='Explore property').click()
    expect(page.locator('#facts')).to_contain_text('Unresolved: 0 reported / 1 from use classification')
    expect(page.locator('#zoningStandards')).to_contain_text('7,500 sq ft')
    expect(page.locator('#constraintChecks')).to_contain_text('unavailable')
    assert not page.locator('details.research-card').evaluate('(el)=>el.open')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


def test_alias_selection_owner_and_sales(page, app_url, property_record):
    first=dict(property_record,parcel=None,existing_building=None,parcel_candidates=[{
        'parcel_number':'0303029000','address':'38-34 ATLANTIC AV, BOSTON, MA 02110',
        'recorded_owner':'ONE LEWIS WHARF LLC','use_description':'OFFICE 3-9 STORY'}])
    selected=dict(property_record,assessor_address='38-34 ATLANTIC AV, BOSTON, MA 02110',
        parcel={'parcel_number':'0303029000','recorded_owners':['ONE LEWIS WHARF LLC']},
        parcel_candidates=[],sales_research={'status':'Recorded sale candidates found','candidates':[{
            'address':'Test office sale','sale_date':'2026-01-01','sale_price':12000000,
            'gross_area_sqft':18000,'price_per_gross_sqft':667,'use_description':'OFFICE 3-9 STORY',
            'parcel_number':'0000000001','source_url':'https://properties.boston.gov/'}]})
    requests=[]
    def respond(route):
        requests.append(route.request.url)
        route.fulfill(json=selected if 'boston_pid=0303029000' in route.request.url else first)
    page.route('**/api/property?*',respond)
    page.route('**/api/suggest?*',lambda route:route.fulfill(json={'suggestions':[]}))
    page.goto(app_url)
    page.get_by_role('combobox').fill('One Lewis Wharf, Boston, MA')
    page.get_by_role('button',name='Explore property').click()
    expect(page.locator('#ownerStatus')).to_contain_text('No owner attributed')
    page.get_by_role('button',name='Review this assessor record').click()
    expect(page.locator('#propertyAddress')).to_have_text('38-34 ATLANTIC AV, BOSTON, MA 02110')
    expect(page.locator('#ownerStatus')).to_contain_text('ONE LEWIS WHARF LLC')
    expect(page.locator('#salesCandidates')).to_contain_text('$12,000,000')
    assert any('boston_pid=0303029000' in url for url in requests)


def test_condo_program_totals_sellout_and_property_reset(page,app_url,property_record):
    first=dict(property_record,existing_building={'units':3})
    second=dict(property_record,address='Another property',existing_building={'units':None,'unit_count_status':'conflict'})
    calls=[]
    def respond(route):
        calls.append(1)
        route.fulfill(json=first if len(calls)==1 else second)
    page.route('**/api/property?*',respond)
    page.route('**/api/suggest?*',lambda route:route.fulfill(json={'suggestions':[]}))
    page.goto(app_url)
    page.get_by_role('combobox').fill('95 Magazine St, Cambridge, MA')
    page.get_by_role('button',name='Explore property').click()
    expect(page.locator('#existingDwellingUnits')).to_have_text('3')
    rows=page.locator('.unit-mix-row')
    for i,values in enumerate([('2','1000','800','1000'),('1','1200','900','1100')]):
        for field,value in zip(['units','area_sqft','low_price_per_sqft','high_price_per_sqft'],values):
            rows.nth(i).locator('[data-field="'+field+'"]').fill(value)
    page.locator('#retainedUnits').fill('2')
    page.locator('#restrictedUnits').fill('1')
    page.locator('#restrictedUnitPrice').fill('250000')
    page.get_by_role('button',name='Calculate unit totals & sellout').click()
    expect(page.locator('#proposedDwellingUnits')).to_have_text('6')
    expect(page.locator('#condoSellout')).to_have_text('$2,930,000–$3,570,000')
    expect(page.locator('#unitTotalsNote')).to_contain_text('4 condos in sellout')
    expect(page.locator('#condoScenarioLimits')).to_contain_text('not profit')
    page.get_by_role('combobox').fill('Another property, Cambridge, MA')
    page.get_by_role('button',name='Explore property').click()
    expect(page.locator('#existingDwellingUnits')).to_have_text('Unresolved')
    expect(page.locator('#condoSellout')).to_have_text('Needs pricing')
    expect(page.locator('#proposedDwellingUnits')).to_have_text('Define unit mix')


def test_condo_sale_range_is_an_explicit_assumption(page,app_url,property_record):
    record=dict(property_record,coordinates={'lat':42.36,'lon':-71.1},coverage={'municipality':'CAMBRIDGE'})
    refs={'status':'3 condominium sale references','method':'Reported living area.','limitations':'New-construction condition is not verified.','observed_price_per_sqft_low':700,'observed_price_per_sqft_high':900,
        'candidates':[{'address':'Test condo '+str(i),'sale_date':'2026-10-01','sale_price':p,'living_area_sqft':1000,'price_per_living_sqft':p/1000,'source_url':'https://www.mass.gov/'} for i,p in enumerate([700000,800000,900000])]}
    page.route('**/api/property?*',lambda route:route.fulfill(json=record))
    page.route('**/api/suggest?*',lambda route:route.fulfill(json={'suggestions':[]}))
    page.route('**/api/condo-sales?*',lambda route:route.fulfill(json=refs))
    page.goto(app_url)
    page.get_by_role('combobox').fill('95 Magazine St, Cambridge, MA')
    page.get_by_role('button',name='Explore property').click()
    page.get_by_text('Condominium sale references for pricing',exact=True).click()
    expect(page.get_by_role('button',name='Use observed range as assumptions')).to_be_disabled()
    page.get_by_role('button',name='Find nearby condo sales').click()
    expect(page.locator('#condoReferences')).to_contain_text('Test condo 0')
    expect(page.locator('#condoCompStatus')).to_contain_text('not verified')
    page.get_by_role('button',name='Use observed range as assumptions').click()
    row=page.locator('.unit-mix-row').first
    expect(row.locator('[data-field="low_price_per_sqft"]')).to_have_value('700')
    expect(row.locator('[data-field="high_price_per_sqft"]')).to_have_value('900')
    row.locator('[data-field="units"]').fill('3')
    row.locator('[data-field="area_sqft"]').fill('1000')
    page.get_by_role('button',name='Calculate unit totals & sellout').click()
    expect(page.locator('#condoSellout')).to_have_text('$2,100,000–$2,700,000')
    expect(page.locator('#restrictedUnitPrice')).to_have_value('')
