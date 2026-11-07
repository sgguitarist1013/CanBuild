import sqlite3

import registry


def test_registry_maps_pilots_and_covers_all_towns():
    from municipal_zoning import MUNICIPALITIES
    assert set(registry.DISTRICTS) == set(MUNICIPALITIES)
    assert registry.district_for("Somerville")["district"] == "Middlesex South"
    assert registry.district_for("Boston")["district"] == "Suffolk"


def test_registry_match_requires_exact_address_reference_and_consideration():
    book_page = registry.reference_pair("Book 80505, page 0062")
    result = registry.check("Somerville", "9 GORHAM STREET, Somerville, MA", book_page, 1650000)
    assert result["status"] == "verified"
    assert result["recorded_date"] == "2022-07-29"
    assert registry.check("Somerville", "9 Gorham St Unit 1", book_page)["status"] == "pending"
    assert registry.check("Cambridge", "9 Gorham St", book_page)["status"] == "pending"
    assert registry.check("Somerville", "9 Gorham St", book_page, 1500000)["status"] == "conflict"
    land_court = registry.check("Somerville", "62 Chandler St", registry.reference_pair("01631/16"), 2150000)
    assert land_court["office"] == "Registered Land (Land Court)"


def test_pending_deeds_cannot_influence_sale_ranges():
    report = {"candidates": [
        {"address": "9 Jay St", "record_reference": "84631/516", "sale_price": 1430000, "price_per_living_sqft": 600},
        {"address": "1 Unchecked St", "record_reference": "3/4", "sale_price": 2500000, "price_per_living_sqft": 2000},
    ], "observed_price_per_sqft_low": 600, "observed_price_per_sqft_high": 2000, "median_sale_price": 1965000}
    result = registry.sale_report("Somerville", report)
    assert [r["address"] for r in result["candidates"]] == ["9 Jay St"]
    assert result["pending_registry_review"] == [{
        "address": "1 Unchecked St", "deed_verification": registry.check("Somerville", "1 Unchecked St", (3, 4)),
    }]
    assert result["observed_price_per_sqft_low"] is None and result["observed_price_per_sqft_high"] is None
    assert result["median_sale_price"] is None


def test_registry_queue_deduplicates_and_resolves_seeded_matches(tmp_path):
    def connect():
        db = sqlite3.connect(tmp_path / "review.db")
        db.row_factory = sqlite3.Row
        return db
    leads = [{"address": "1 Unchecked St", "deed_verification": registry.check("Somerville", "1 Unchecked St", (3, 4))},
             {"address": "9 Jay St", "deed_verification": {**registry.check("Somerville", "9 Jay St", (84631, 516)), "status": "pending"}}]
    registry.enqueue(connect, "Somerville", leads)
    registry.enqueue(connect, "Somerville", leads)
    pending = registry.review_queue(connect, 40)
    assert [row["address"] for row in pending] == ["1 Unchecked St"]
