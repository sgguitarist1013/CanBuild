"""Regression tests for the statewide matching / zoning boundary controls.

Run: python -m unittest discover -s tests -v
"""
import unittest
from shapely.geometry import box
from parcel_engine import exact_address_match, conceptual_massing, lot_area_sqft

class StatewideParcelTests(unittest.TestCase):
    def test_accepts_same_address_in_multiple_municipalities(self):
        for city in ("CAMBRIDGE","WORCESTER","BOSTON","SPRINGFIELD","WESTBOROUGH"):
            with self.subTest(city=city):
                record={"CITY":city,"ADDR_NUM":"95","SITE_ADDR":"95 MAGAZINE ST",
                        "FULL_STR":"MAGAZINE ST"}
                self.assertTrue(exact_address_match(record,"95","Magazine",city))

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

    def test_no_inclusionary_option_below_5000_sqft(self):
        plot=box(0,0,60,60)
        out=conceptual_massing(plot,{},None,"CAMBRIDGE","C-1")
        self.assertIsNotNone(out)
        self.assertNotIn("inclusionary_scenario",out)
        self.assertIsNone(out["additional_area_high_sqft"])

if __name__=="__main__":
    unittest.main()
