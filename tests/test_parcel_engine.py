"""Regression tests for the statewide matching / zoning boundary controls.

Run: python -m unittest discover -s tests -v
"""
import unittest
from shapely.geometry import box
from parcel_engine import exact_address_match, conceptual_massing, lot_area_sqft, requested_house_number, resolve_parcel
from unittest.mock import patch, AsyncMock

class StatewideParcelTests(unittest.TestCase):
    def test_accepts_same_address_in_multiple_municipalities(self):
        for city in ("CAMBRIDGE","WORCESTER","BOSTON","SPRINGFIELD","WESTBOROUGH"):
            with self.subTest(city=city):
                record={"CITY":city,"ADDR_NUM":"95","SITE_ADDR":"95 MAGAZINE ST",
                        "FULL_STR":"MAGAZINE ST"}
                self.assertTrue(exact_address_match(record,"95","Magazine",city))

    def test_street_suffix_variants_across_cities(self):
        for city in ("CAMBRIDGE","BOSTON","WORCESTER","SPRINGFIELD"):
            record={"CITY":city,"ADDR_NUM":"43","SITE_ADDR":"43 WENDELL STREET",
                    "FULL_STR":"WENDELL ST"}
            self.assertTrue(exact_address_match(record,"43","Wendell Street",city))
            self.assertTrue(exact_address_match(record,"43","Wendell St",city))
            self.assertTrue(exact_address_match(record,"43","Wendell",city))
            self.assertFalse(exact_address_match(record,"43","Wendell Avenue",city))

    def test_nearby_property_must_be_rejected(self):
        record={"CITY":"CAMBRIDGE","ADDR_NUM":"91","SITE_ADDR":"91 MAGAZINE ST",
                "FULL_STR":"MAGAZINE ST"}
        self.assertFalse(exact_address_match(record,"95","Magazine","CAMBRIDGE"))

    def test_other_city_and_other_street_rejected(self):
        record={"CITY":"CAMBRIDGE","ADDR_NUM":"95","SITE_ADDR":"95 MAGAZINE ST",
                "FULL_STR":"MAGAZINE ST"}
        self.assertFalse(exact_address_match(record,"95","Magazine","BOSTON"))
        self.assertFalse(exact_address_match(record,"95","Massachusetts","CAMBRIDGE"))

    def test_conflicting_assessor_address_fields_rejected(self):
        record={"CITY":"CAMBRIDGE","ADDR_NUM":"95","SITE_ADDR":"91 MAGAZINE ST",
                "FULL_STR":"MAGAZINE ST"}
        self.assertFalse(exact_address_match(record,"95","Magazine","CAMBRIDGE"))

    def test_house_number_not_census_street_range(self):
        self.assertEqual(requested_house_number("95 Magazine St, Cambridge, MA"),"95")
        self.assertEqual(requested_house_number("95A Magazine St, Cambridge, MA"),"95A")
        self.assertEqual(requested_house_number("91 Magazine St, Cambridge, MA"),"91")

    def test_lot_area_units(self):
        self.assertEqual(lot_area_sqft({"LOT_SIZE":0.2,"LOT_UNITS":"ACRES"}),8712)
        self.assertEqual(lot_area_sqft({"LOT_SIZE":7405,"LOT_UNITS":"SF"}),7405)
        self.assertIsNone(lot_area_sqft({"LOT_SIZE":7405,"LOT_UNITS":None}))

    def test_no_cambridge_rules_apply_elsewhere(self):
        plot=box(0,0,100,100)
        for city in ("BOSTON","WORCESTER","SPRINGFIELD","WESTBOROUGH"):
            with self.subTest(city=city):
                self.assertIsNone(conceptual_massing(plot,{"gis_area_sqft_approx":10000},
                    {"building_area_sqft":2000},city,"C-1"))

    def test_cambridge_four_and_conditional_six_stories(self):
        plot=box(0,0,100,100)
        out=conceptual_massing(plot,{"gis_area_sqft_approx":10000},
            {"building_area_sqft":2000},"CAMBRIDGE","C-1")
        self.assertIsNotNone(out)
        self.assertEqual(out["stories"],4)
        self.assertEqual(out["inclusionary_scenario"]["stories"],6)
        self.assertLessEqual(out["gross_area_high_sqft"],10000*0.7*4)
        self.assertLess(out["gross_area_low_sqft"],out["gross_area_high_sqft"])

    def test_no_inclusionary_option_when_assessor_lot_is_below_5000_sqft(self):
        out = conceptual_massing(box(0, 0, 100, 60), {"gis_area_sqft_approx": 4999},
                                 None, "CAMBRIDGE", "C-1")
        self.assertEqual(out["stories"], 4)
        self.assertNotIn("inclusionary_scenario", out)

    def test_no_inclusionary_option_below_5000_sqft(self):
        plot=box(0,0,60,60)
        out=conceptual_massing(plot,{},None,"CAMBRIDGE","C-1")
        self.assertIsNotNone(out)
        self.assertNotIn("inclusionary_scenario",out)
        self.assertIsNone(out["additional_area_high_sqft"])

class StatewideLookupIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_95_does_not_select_91_census_range(self):
        # Census fromAddress 91 must not influence matching user-entered 95.
        components={"fromAddress":"91","streetName":"MAGAZINE","city":"CAMBRIDGE"}
        neighboring={"attributes":{"OBJECTID":91,"CITY":"CAMBRIDGE","ADDR_NUM":"91",
            "SITE_ADDR":"91 MAGAZINE ST","FULL_STR":"MAGAZINE ST","MAP_PAR_ID":"101-99","LOC_ID":"101-99"}}
        target={"attributes":{"OBJECTID":95,"CITY":"CAMBRIDGE","ADDR_NUM":"95",
            "SITE_ADDR":"95 MAGAZINE ST","FULL_STR":"MAGAZINE ST","MAP_PAR_ID":"101-200",
            "LOC_ID":"101-200","LOT_SIZE":4000,"LOT_UNITS":"SF","FY":2026}}
        responses=[[neighboring],[target]]
        async def fake_query(client, params, timeout=12.0):
            return responses.pop(0)
        with patch("parcel_engine.query",new=fake_query), patch("parcel_engine.selected_geometry",new=AsyncMock(return_value=None)):
            warnings=[]
            parcel,building,geometry=await resolve_parcel(None,-71.11,42.35,components,warnings,
                "95 Magazine St, Cambridge, MA")
        self.assertEqual(parcel["parcel_number"],"101-200")
        self.assertEqual(parcel["assessor_site_address"],"95 MAGAZINE ST")
        self.assertNotEqual(parcel["parcel_number"],"101-99")

    async def test_assessor_street_aliases_match_census_components(self):
        cases = [
            ("CHELSEA", "300", "3RD", "", "THIRD ST"),
            ("ARLINGTON", "248", "MASSACHUSETTS", "", "MASS AVE"),
            ("WATERTOWN", "63", "MOUNT AUBURN", "", "MT AUBURN ST"),
            ("WATERTOWN", "43", "BEACON", "N", "NO BEACON ST"),
        ]
        for city, house, street, direction, assessor in cases:
            with self.subTest(city=city, street=street):
                components = {"city": city, "streetName": street, "preDirection": direction, "suffixType": assessor.split()[-1]}
                target = {"attributes": {"OBJECTID": 1, "CITY": city, "ADDR_NUM": house,
                                         "SITE_ADDR": f"{house} {assessor}", "FULL_STR": assessor, "LOC_ID": "target"}}
                with patch("parcel_engine.query", new=AsyncMock(return_value=[target])), patch(
                    "parcel_engine.selected_geometry", new=AsyncMock(return_value=None),
                ):
                    parcel, _, _ = await resolve_parcel(None, -71.1, 42.4, components, [], f"{house} {street} {components['suffixType']}, {city}, MA")
                self.assertEqual(parcel["loc_id"], "target")
                self.assertFalse(exact_address_match(target["attributes"], str(int(house) + 1), assessor, city))
                self.assertFalse(exact_address_match(target["attributes"], house, assessor.rsplit(" ", 1)[0] + " ROAD", city))

    async def test_only_neighbor_returns_no_parcel(self):
        components={"fromAddress":"91","streetName":"MAGAZINE","city":"CAMBRIDGE"}
        neighboring={"attributes":{"OBJECTID":91,"CITY":"CAMBRIDGE","ADDR_NUM":"91",
            "SITE_ADDR":"91 MAGAZINE ST","FULL_STR":"MAGAZINE ST","MAP_PAR_ID":"101-99","LOC_ID":"101-99"}}
        with patch("parcel_engine.query",new=AsyncMock(return_value=[neighboring])):
            warnings=[]
            parcel,building,geometry=await resolve_parcel(None,-71.11,42.35,components,warnings,
                "95 Magazine St, Cambridge, MA")
        self.assertIsNone(parcel)
        self.assertIsNone(building)
        self.assertIsNone(geometry)

if __name__=="__main__":
    unittest.main()
