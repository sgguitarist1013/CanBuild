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
    expect(page.locator("#potentialAnswer")).to_contain_text("PlotDocket matched the parcel.")
    expect(page.locator("#potentialAnswer")).to_contain_text("Confirm its zoning district")
    expect(page.locator("#redevelopmentMetrics")).to_be_hidden()
    expect(page.locator("#go")).to_be_enabled()
    expect(page.get_by_role("combobox")).to_have_attribute("aria-expanded", "false")


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


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize(("incorrect_address", "street_conflict"), [
    ("106 Magazine Avenue, Cambridge, MA", "type"),
    ("106 Magazne St, Cambridge, MA", "name"),
])
def test_street_conflict_keeps_the_typed_address_for_retry(page, app_url, property_record, width,
                                                         incorrect_address, street_conflict):
    page.set_viewport_size({"width": width, "height": 844})
    corrected_address = "106 Magazine St, Cambridge, MA"
    corrected = dict(property_record, address=corrected_address,
                     parcel={"parcel_number": "102-51", "assessor_site_address": "106 MAGAZINE ST"})

    def respond(route):
        url = route.request.url
        if "Avenue" in url or "Magazne" in url:
            route.fulfill(status=409, json={"detail": f"The address match uses a different street {street_conflict}. Check the address and try again."})
        else:
            route.fulfill(json=corrected if "106" in url else property_record)

    page.route("**/api/property?*", respond)
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    field = page.get_by_role("combobox")
    field.fill("95 Magazine St, Cambridge, MA")
    field.press("Enter")
    expect(page.locator("#propertyAddress")).to_contain_text("95 MAGAZINE ST")

    field.fill(incorrect_address)
    field.press("Enter")
    expect(page.locator("#status")).to_contain_text(f"different street {street_conflict}")
    expect(field).to_have_value(incorrect_address)
    expect(page.locator("#results")).to_be_hidden()
    expect(page.get_by_role("button", name="Explore property")).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    field.fill(corrected_address)
    field.press("Enter")
    expect(page.locator("#propertyAddress")).to_have_text(corrected_address)
    expect(page.locator("#facts")).to_contain_text("102-51")


def test_failed_address_search_hides_the_previous_property(page, app_url, property_record):
    def respond(route):
        if "Missing" in route.request.url:
            route.fulfill(status=404, json={"detail": "Address not matched."})
        else:
            route.fulfill(json=property_record)

    page.route("**/api/property?*", respond)
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    field = page.get_by_role("combobox")
    field.fill("95 Magazine St, Cambridge, MA")
    field.press("Enter")
    expect(page.locator("#results")).to_be_visible()
    field.fill("999 Missing St, Cambridge, MA")
    field.press("Enter")
    expect(page.locator("#status")).to_have_text("Address not matched.")
    expect(page.locator("#results")).to_be_hidden()
    expect(page.get_by_role("button", name="Explore property")).to_be_enabled()


@pytest.mark.parametrize("width", [320, 390])
def test_mobile_layout_and_input_validation(page, app_url, width):
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(app_url)
    expect(page).to_have_title("PlotDocket | Discover your property's potential")
    expect(page.locator("header .brand")).to_have_text("PlotDocket")
    expect(page.locator("footer")).to_contain_text("PlotDocket")
    assert page.request.get(f"{app_url}/static/plotdocket.svg").ok
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


@pytest.mark.parametrize("width", [320, 390])
def test_editing_address_hides_old_boston_parcel_candidates(page, app_url, property_record, width):
    page.set_viewport_size({"width": width, "height": 844})
    candidate = {"parcel_number": "0303029000", "address": "38-34 ATLANTIC AV, BOSTON, MA 02110"}
    alias = "One Lewis Wharf, Boston, MA"
    first = dict(property_record, address=alias, parcel=None, parcel_candidates=[candidate])
    selected = dict(property_record, address=alias, assessor_address=candidate["address"],
                    parcel={**candidate, "recorded_owners": ["ONE LEWIS WHARF LLC"]})
    requests = []

    def respond(route):
        url = route.request.url
        requests.append(url)
        if "boston_pid=" in url:
            route.fulfill(json=selected)
        elif "Beacon" in url:
            route.fulfill(status=503, json={"detail": "Assessor temporarily unavailable."})
        else:
            route.fulfill(json=first)

    page.route("**/api/property?*", respond)
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    field = page.get_by_role("combobox")
    field.fill(alias)
    field.press("Enter")
    page.get_by_role("button", name="Confirm parcel").click()
    candidate_button = page.get_by_role("button", name="Review this assessor record")
    expect(candidate_button).to_be_visible()
    field.fill("1 Beacon St, Boston, MA")
    expect(page.locator("#results")).to_be_hidden()
    expect(candidate_button).to_be_hidden()
    page.locator("#parcelCandidates button").evaluate("button => button.click()")
    field.press("Enter")
    expect(page.locator("#status")).to_have_text("Assessor temporarily unavailable.")
    expect(page.locator("#results")).to_be_hidden()
    assert len(requests) == 2
    assert "boston_pid=" not in requests[1]

    field.fill(alias)
    field.press("Enter")
    page.get_by_role("button", name="Confirm parcel").click()
    page.get_by_role("button", name="Review this assessor record").click()
    expect(page.locator("#propertyAddress")).to_have_text(candidate["address"])
    expect(page.locator("#ownerStatus")).to_contain_text("ONE LEWIS WHARF LLC")
    assert "One%20Lewis%20Wharf" in requests[-1]
    assert "boston_pid=0303029000" in requests[-1]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("width", [320, 390])
def test_alias_selection_owner_and_sales(page, app_url, property_record, width):
    page.set_viewport_size({"width": width, "height": 844})
    alias = "One Lewis Wharf, Boston, MA"
    outage = "Boston assessor records are temporarily unavailable. Try the same address again shortly."
    first=dict(property_record,address=alias,parcel=None,existing_building=None,parcel_candidates_truncated=True,parcel_candidates=[{
        'parcel_number':'0303029000','address':'38-34 ATLANTIC AV, BOSTON, MA 02110',
        'recorded_owner':'ONE LEWIS WHARF LLC','use_description':'OFFICE 3-9 STORY'}])
    selected=dict(property_record,address=alias,assessor_address='38-34 ATLANTIC AV, BOSTON, MA 02110',
        parcel={'parcel_number':'0303029000','recorded_owners':['ONE LEWIS WHARF LLC']},
        parcel_candidates=[],sales_research={'status':'Recorded sale candidates found','candidates':[{
            'address':'Test office sale','sale_date':'2026-01-01','sale_price':12000000,
            'gross_area_sqft':18000,'price_per_gross_sqft':667,'use_description':'OFFICE 3-9 STORY',
            'parcel_number':'0000000001','source_url':'https://properties.boston.gov/'}]})
    requests=[]
    def respond(route):
        if "Lewis%20St" in route.request.url:
            route.fulfill(status=409, json={"detail": "The address match uses a different street type. Check the street name and try again."})
            return
        requests.append(route.request.url)
        if len(requests) in (1, 3):
            route.fulfill(status=503, json={"detail": outage})
        else:
            route.fulfill(json=selected if 'boston_pid=0303029000' in route.request.url else first)
    page.route('**/api/property?*',respond)
    page.route('**/api/suggest?*',lambda route:route.fulfill(json={'suggestions':[]}))
    page.goto(app_url)
    field = page.get_by_role('combobox')
    wrong_street = "1 Lewis St, Boston, MA"
    field.fill(wrong_street)
    field.press('Enter')
    expect(page.locator('#status')).to_contain_text('different street type')
    expect(field).to_have_value(wrong_street)
    expect(page.locator('#results')).to_be_hidden()
    field.fill(alias)
    page.get_by_role('button',name='Explore property').click()
    expect(page.locator('#status')).to_have_text(outage)
    expect(field).to_have_value(alias)
    expect(page.locator('#results')).to_be_hidden()
    field.press('Enter')
    expect(page.locator('#ownerStatus')).to_contain_text('No owner attributed')
    expect(page.locator('#potentialAnswer')).to_have_text('Boston returned only part of its assessor results.')
    expect(page.locator('#redevelopmentMetrics')).to_be_hidden()
    expect(page.get_by_role('button',name='Review this assessor record')).to_be_hidden()
    page.get_by_role('button',name='Confirm parcel').click()
    expect(page.locator('#recordDetails > summary')).to_be_focused()
    page.get_by_role('button',name='Review this assessor record').click()
    expect(page.locator('#status')).to_have_text(outage)
    expect(page.locator('#parcelCandidateError')).to_have_text(outage)
    expect(page.locator('#parcelCandidateError')).to_be_in_viewport()
    expect(page.locator('#ownerStatus')).to_contain_text('No owner attributed')
    page.get_by_role('button',name='Review this assessor record').click()
    expect(page.locator('#propertyAddress')).to_have_text('38-34 ATLANTIC AV, BOSTON, MA 02110')
    expect(page.locator('#ownerStatus')).to_contain_text('ONE LEWIS WHARF LLC')
    expect(page.locator('#parcelCandidateError')).to_be_hidden()
    expect(page.locator('#recordDetails')).not_to_have_attribute('open', '')
    page.get_by_text('Comparable sales', exact=True).click()
    expect(page.locator('#salesCandidates')).to_be_visible()
    expect(page.locator('#salesCandidates')).to_contain_text('$12,000,000')
    assert requests[0] == requests[1]
    assert requests[2] == requests[3]
    assert 'boston_pid=0303029000' in requests[3]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("width", [320, 390])
def test_empty_partial_boston_search_explains_how_to_refine_address(page, app_url, property_record, width):
    address = "One Lewis Wharf, Boston, MA"
    record = dict(property_record, address=address, parcel=None, existing_building=None,
                  parcel_candidates=[], parcel_candidates_truncated=True)
    page.set_viewport_size({"width": width, "height": 844})
    page.route("**/api/property?*", lambda route: route.fulfill(json=record))
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    field = page.get_by_role("combobox")
    field.fill(address)
    field.press("Enter")
    expect(page.locator("#potentialAnswer")).to_have_text("Boston returned only part of its assessor results.")
    expect(page.locator("#potentialLimit")).to_contain_text("narrowing the search")
    expect(page.locator("#redevelopmentMetrics")).to_be_hidden()
    page.get_by_role("button", name="Check address").click()
    expect(field).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


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
    record = dict(property_record, zoning={"district": "C-1"}, redevelopment=redevelopment,
                  parcel=dict(property_record["parcel"], recorded_owners=["EXAMPLE HOLDINGS LLC"]),
                  current_valuation={"market_range": {"low": 1000000, "high": 1200000, "comp_count": 3, "radius_miles": .5},
                                     "assessment": {"amount": 900000, "fiscal_year": 2026}})
    page.route("**/api/property?*", lambda route: route.fulfill(json=record))
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    page.get_by_role("combobox").fill("95 Magazine St, Cambridge, MA")
    page.get_by_role("button", name="Explore property").click()
    expect(page.locator("#ownershipSummary")).to_contain_text("Assessor-reported owner / entity · EXAMPLE HOLDINGS LLC")
    expect(page.locator("#currentMarketValue")).to_have_text("$1,000,000 to $1,200,000")
    expect(page.locator("#currentMarketBasis")).to_contain_text("3 Registry-confirmed sales")
    expect(page.locator("#assessedValueContext")).to_contain_text("FY 2026 tax assessment · $900,000")
    expect(page.locator("#autoSellout")).to_have_text("Estimate unavailable")
    expect(page.locator("#potentialValueBasis")).to_contain_text("three deed-confirmed sales")
    expect(page.locator("#autoGrossArea")).to_have_text("12,000 sq ft")
    expect(page.locator("#autoCostMetric")).to_be_hidden()
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
    expect(page.locator("#redevelopmentMix")).to_contain_text("Insufficient matching deed-confirmed sales")
    assert not page.get_by_text("Optional manual condo scenario", exact=True).evaluate("(el) => el.parentElement.open")


def test_smaller_rebuild_leads_with_existing_building_on_mobile(page, app_url, property_record):
    from redevelopment import analyze_redevelopment

    building = {"assessed_value": 1932200, "residential_area_sqft": 2083, "units": 1}
    envelope = {"gross_area_high_sqft": 1703, "gross_area_low_sqft": 900, "stories": 4}
    record = dict(property_record, zoning={"district": "C-1"}, existing_building=building,
                  zoning_potential=envelope, redevelopment=analyze_redevelopment(envelope, building))
    page.set_viewport_size({"width": 390, "height": 844})
    page.route("**/api/property?*", lambda route: route.fulfill(json=record))
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    page.get_by_role("combobox").fill("95 Magazine St, Cambridge, MA")
    page.get_by_role("button", name="Explore property").click()
    expect(page.locator("#potentialAnswer")).to_contain_text("Start with the existing building")
    expect(page.locator("#redevelopmentMetrics")).to_be_hidden()
    expect(page.locator("#surplusSummary")).to_be_hidden()
    expect(page.locator("#autoSellout")).to_have_text("Estimate unavailable")
    page.get_by_role("button", name="View zoning comparison").click()
    expect(page.locator("#opportunityNote")).to_contain_text("1,050 sq ft of modeled unit area")
    expect(page.locator("#opportunityNote")).to_contain_text("2,083 sq ft of existing living area")


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


def test_nr_buildout_and_size_matched_sales_are_visible_on_mobile(page, app_url, property_record):
    from somerville import buildout

    page.set_viewport_size({"width": 390, "height": 844})
    building = {"use_description": "Multiple Houses on one parcel", "residential_area_sqft": 2722,
                "bedrooms_reported": 5, "bathrooms_reported": 2, "year_built": 1910}
    record = dict(property_record, address="9 GORHAM ST, SOMERVILLE, MA", zoning={"district": "NR"},
                  existing_building=building, buildout_review=buildout(property_record["parcel"], building),
                  sales_research={"candidates": [{"address": "9 JAY ST", "sale_price": 1430000,
                      "comparison_area_sqft": 2945, "size_difference_pct": 8, "bedrooms_reported": 6,
                      "bathrooms_reported": 3, "comparison_label": "Similar residential sale · different assessor class"}]})
    page.route("**/api/property?*", lambda route: route.fulfill(json=record))
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    page.get_by_role("combobox").fill("9 Gorham St, Somerville, MA")
    page.get_by_role("button", name="Explore property").click()
    expect(page.locator("#potentialAnswer")).to_contain_text("one to three")
    expect(page.locator("#propertyFactsSummary")).to_contain_text("2,722 sq ft living area · 5 beds · 2 baths")
    expect(page.locator("#salePreview")).to_contain_text("9 JAY ST sold for $1,430,000")
    page.get_by_role("button", name="View buildout summary").click()
    expect(page.locator("#buildoutChecks")).to_contain_text("nonconforming rights")
    page.get_by_text("Comparable sales", exact=True).click()
    expect(page.locator("#salesCandidates")).to_contain_text("+8% vs subject · 6 beds / 3 baths")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_verified_deed_and_pending_lead_stay_visibly_distinct(page, app_url, property_record):
    from registry import check

    deed = check("Somerville", "9 Gorham St", (80505, 62), 1650000)
    pending = {"address": "1 Unchecked St", "deed_verification": check("Somerville", "1 Unchecked St", (123, 456))}
    parcel = dict(property_record["parcel"], deed_verification=deed)
    record = dict(property_record, address="9 GORHAM ST, SOMERVILLE, MA", parcel=parcel,
                  sales_research={"candidates": [], "pending_registry_review": [pending]})
    page.set_viewport_size({"width": 390, "height": 844})
    page.route("**/api/property?*", lambda route: route.fulfill(json=record))
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.goto(app_url)
    page.get_by_role("combobox").fill("9 Gorham St, Somerville, MA")
    page.get_by_role("button", name="Explore property").click()
    expect(page.locator("#deedSummary")).to_contain_text("Transfer confirmed in MassLandRecords")
    expect(page.locator("#salePreview")).to_be_hidden()
    page.get_by_text("Property records", exact=True).click()
    expect(page.locator("#lastTransfer")).to_contain_text("$1,650,000 consideration")
    expect(page.locator("#lastTransfer")).to_contain_text("Book 80505, page 62")
    expect(page.locator("#lastTransfer").get_by_role("link")).to_have_attribute("href", deed["url"])
    page.get_by_text("Comparable sales", exact=True).click()
    page.get_by_text("Sale leads awaiting registry confirmation", exact=True).click()
    expect(page.locator("#pendingRegistryList")).to_contain_text("Book 123, page 456")
    expect(page.locator("#pendingRegistryList")).to_contain_text("Awaiting MassLandRecords confirmation")


def test_permit_history_loads_only_when_opened_and_keeps_city_links(page, app_url, property_record):
    coverage = {"source_url": "https://data.cambridgema.gov/d/3wsm-e5jx",
                "official_lookup_url": "https://www.cambridgema.gov/inspection/codesandmaps/recordsandarchives",
                "coverage_note": "City permit dataset"}
    property_record["coverage"] = {"municipality": "CAMBRIDGE"}
    property_record["permit_coverage"] = coverage
    calls = []

    def respond(route):
        calls.append(route.request.url)
        route.fulfill(json={**coverage, "status": "found", "match_note": "Matched by map and lot.",
                            "records": [{"permit_id": "138721", "permit_type": "Roof", "issued_on": "2021-08-23",
                                         "reported_status": "Complete", "source_url": coverage["source_url"]}]})

    page.route("**/api/property?*", lambda route: route.fulfill(json=property_record))
    page.route("**/api/suggest?*", lambda route: route.fulfill(json={"suggestions": []}))
    page.route("**/api/permits?*", respond)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(app_url)
    page.get_by_role("combobox").fill("95 Magazine St, Cambridge, MA")
    page.get_by_role("button", name="Explore property").click()
    page.get_by_text("Property records", exact=True).click()
    assert not calls
    page.get_by_text("Building permits and applications", exact=True).click()
    expect(page.locator("#permitRecords")).to_contain_text("138721")
    expect(page.get_by_role("link", name="City permit lookup")).to_have_attribute("href", coverage["official_lookup_url"])
    page.get_by_text("Building permits and applications", exact=True).click()
    page.get_by_text("Building permits and applications", exact=True).click()
    expect(page.locator("#permitRecords")).to_contain_text("Issued 2021-08-23")
    assert len(calls) == 1
