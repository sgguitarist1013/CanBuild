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
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), page.evaluate("Array.from(document.querySelectorAll('*')).filter(el => el.scrollWidth > el.clientWidth + 1 || el.getBoundingClientRect().left < -1).map(el => ({tag: el.tagName, id: el.id, className: String(el.className).slice(0,80), left: Math.round(el.getBoundingClientRect().left), right: Math.round(el.getBoundingClientRect().right), width: el.getBoundingClientRect().width, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, overflowX: getComputedStyle(el).overflowX})).slice(0,20)")


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
    expect(page.locator('#ownerStatus')).to_contain_text('No recorded owner attributed')
    page.get_by_role('button',name='Review this assessor record').click()
    expect(page.locator('#propertyAddress')).to_have_text('38-34 ATLANTIC AV, BOSTON, MA 02110')
    expect(page.locator('#ownerStatus')).to_contain_text('ONE LEWIS WHARF LLC')
    expect(page.locator('#salesCandidates')).to_contain_text('$12,000,000')
    assert any('boston_pid=0303029000' in url for url in requests)
