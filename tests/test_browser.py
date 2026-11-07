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
    expect(page.locator("#potentialAnswer")).to_contain_text("Confirm its zoning district")
    expect(page.locator("#redevelopmentMetrics")).to_be_hidden()
    expect(page.locator("#go")).to_be_enabled()


def test_keyboard_search_works_without_a_suggestion(page, app_url, property_record):
    calls = []
    def respond(route):
        calls.append(route.request.url)
        route.fulfill(json=property_record)
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.route("**/api/property?*", respond)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(app_url)
    field = page.get_by_role("combobox")
    field.fill("95 Magazine St, Cambridge, MA")
    field.press("Enter")
    expect(page.locator("#propertyAddress")).to_contain_text("95 MAGAZINE ST")
    expect(page.get_by_role("button", name="Explore property")).to_be_enabled()
    assert len(calls) == 1


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
    assert page.locator('details.research-card[open]').count() == 0
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')


def test_alias_selection_owner_and_sales(page, app_url, property_record):
    page.set_viewport_size({"width": 390, "height": 844})
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
    expect(page.locator('#potentialAnswer')).to_contain_text('Choose the assessor record')
    expect(page.locator('#redevelopmentMetrics')).to_be_hidden()
    expect(page.get_by_role('button',name='Review this assessor record')).to_be_hidden()
    page.get_by_role('button',name='Confirm parcel').click()
    expect(page.locator('#recordDetails > summary')).to_be_focused()
    page.get_by_role('button',name='Review this assessor record').click()
    expect(page.locator('#propertyAddress')).to_have_text('38-34 ATLANTIC AV, BOSTON, MA 02110')
    expect(page.locator('#ownerStatus')).to_contain_text('ONE LEWIS WHARF LLC')
    expect(page.locator('#recordDetails')).not_to_have_attribute('open', '')
    page.get_by_text('Comparable sales', exact=True).click()
    expect(page.locator('#salesCandidates')).to_be_visible()
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
    page.get_by_text('Advanced tools',exact=True).click()
    page.get_by_text('Optional manual condo scenario',exact=True).click()
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
    calls=[]
    def pricing_response(route):
        calls.append(1)
        route.fulfill(status=503,json={'detail':'Condo source temporarily unavailable'}) if len(calls)==1 else route.fulfill(json=refs)
    page.route('**/api/condo-sales?*',pricing_response)
    page.goto(app_url)
    page.get_by_role('combobox').fill('95 Magazine St, Cambridge, MA')
    page.get_by_role('button',name='Explore property').click()
    page.get_by_text('Advanced tools',exact=True).click()
    page.get_by_text('Optional manual condo scenario',exact=True).click()
    row=page.locator('.unit-mix-row').first
    row.locator('[data-field="units"]').fill('3')
    row.locator('[data-field="area_sqft"]').fill('1000')
    page.get_by_text('Condominium sale references for pricing',exact=True).click()
    expect(page.get_by_role('button',name='Use observed range as assumptions')).to_be_disabled()
    page.get_by_role('button',name='Find nearby condo sales').click()
    expect(page.locator('#condoCompStatus')).to_contain_text('temporarily unavailable')
    expect(row.locator('[data-field="units"]')).to_have_value('3')
    expect(row.locator('[data-field="area_sqft"]')).to_have_value('1000')
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


def test_automatic_redevelopment_shows_default_estimate_without_input(page, app_url, property_record):
    from redevelopment import analyze_redevelopment

    page.set_viewport_size({"width": 390, "height": 844})
    redevelopment = analyze_redevelopment({"gross_area_high_sqft": 12000, "stories": 4},
                                          {"assessed_value": 900000, "building_area_sqft": 3000})
    redevelopment["best"]["conditional"] = True
    record = dict(property_record, zoning={"district": "C-1"}, redevelopment=redevelopment)
    page.route("**/api/property?*", lambda route: route.fulfill(json=record))
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    page.get_by_role("combobox").fill("95 Magazine St, Cambridge, MA")
    page.get_by_role("button", name="Explore property").click()
    expect(page.locator("#autoSellout")).to_contain_text("$")
    expect(page.locator("#autoCost")).to_contain_text("$")
    expect(page.locator("#autoUnits")).to_have_text(str(redevelopment["best"]["market_units"] + redevelopment["best"]["restricted_units"]))
    expect(page.locator("#potentialLimit")).to_contain_text("affordable-housing requirements")
    expect(page.locator("#redevelopmentAreas")).to_be_hidden()
    assert page.locator('details.research-card[open]').count() == 0
    button = page.get_by_role("button", name="View development estimate")
    expect(button).to_be_in_viewport()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    button.click()
    expect(page.locator("#estimateDetails > summary")).to_be_focused()
    expect(page.locator("#redevelopmentAreas")).to_be_visible()
    expect(page.locator("#redevelopmentAreas")).to_contain_text("restricted")
    expect(page.locator("#redevelopmentMix")).to_contain_text("Cambridge 2026 study")
    assert not page.get_by_text("Optional manual condo scenario", exact=True).evaluate("(el) => el.parentElement.open")


def test_somerville_zoning_details_open_from_mobile_summary(page, app_url, property_record):
    page.set_viewport_size({"width": 390, "height": 844})
    section = "https://online.encodeplus.com/regs/somerville-ma/doc-view.aspx?pn=0&ajax=0&secid=540"
    report = {
        "status": "Official-source passages found. District applicability and amendments still need review.",
        "sources": [{"title": "Dimensional regulations", "url": section, "topics": ["dimensional standards"]}],
        "collected_at": "2026-11-07T18:30:00+00:00",
    }
    record = dict(property_record, address="50 PROSPECT ST, SOMERVILLE, MA",
                  zoning={"district": "MR6"}, on_demand_zoning_research=report)
    page.route("**/api/property?*", lambda route: route.fulfill(json=record))
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    page.get_by_role("combobox").fill("50 Prospect St, Somerville, MA")
    page.get_by_role("button", name="Explore property").click()
    expect(page.locator("#zoneStatus")).to_have_text("MR6 · City zoning map matched")
    expect(page.locator("#potentialAnswer")).to_contain_text("automated buildout estimate is currently unavailable")
    expect(page.locator("#redevelopmentMetrics")).to_be_hidden()
    expect(page.get_by_text("On-demand zoning research", exact=True)).to_be_hidden()
    page.get_by_role("button", name="View district standards").click()
    expect(page.locator("#districtDetails > summary")).to_be_focused()
    expect(page.locator("#onDemandStatus")).to_be_visible()
    expect(page.locator("#onDemandStatus")).to_contain_text("amendments still need review")
    expect(page.get_by_role("link", name="Dimensional regulations")).to_have_attribute("href", section)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
