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
