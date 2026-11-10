from valuation import current_valuation


def test_as_is_estimate_uses_three_distinct_registry_confirmed_like_sales():
    parcel = {"parcel_number": "subject", "fiscal_year": 2026, "gis_area_sqft_approx": 5000}
    building = {"assessed_value": 900000, "residential_area_sqft": 2000,
                "bedrooms_reported": 3, "bathrooms_reported": 2}
    base = {
        "comparison_label": "Same assessor class", "arm_length_status": "City sale flag Q",
        "comparison_area_basis": "assessor living area", "bedrooms_reported": 3, "bathrooms_reported": 2,
        "lot_area_sqft": 5000,
    }

    def comp(identifier, distance, size, price, status="verified", **changes):
        return {**base, "parcel_number": identifier, "address": identifier, "distance_miles": distance,
                "comparison_area_sqft": size, "sale_price": price,
                "deed_verification": {"status": status, "consideration": price}, **changes}

    candidates = [
        comp("a", .12, 2000, 1000000),
        comp("a", .13, 2000, 1900000),
        comp("pending", .15, 2000, 1900000, "pending"),
        comp("wrong lot", .2, 2000, 1900000, lot_area_sqft=3749),
        comp("wrong size", .22, 2301, 1900000),
        comp("wrong beds", .23, 2000, 1900000, bedrooms_reported=5),
        comp("b", .25, 1800, 990000),
        comp("c", .55, 2200, 1320000, lot_area_sqft=3750, comparison_label="Different assessor class"),
        comp("far", .8, 2000, 1900000),
    ]
    report = current_valuation(parcel, building, {"candidates": candidates})
    assert report["market_range"] == {"low": 1000000, "high": 1200000, "comp_count": 3,
                                       "radius_miles": .55, "comp_addresses": ["a", "b", "c"]}
    assert report["assessment"] == {"amount": 900000, "fiscal_year": 2026}
    insufficient = current_valuation(parcel, building, {"candidates": candidates[:7]})
    assert insufficient["market_range"] is None
    assert insufficient["market_evidence"]["qualifying_count"] == 2
    assert current_valuation(parcel, building, {"candidates": candidates, "truncated": True})["market_range"] is None
    historical = [dict(sale, older_reference=True) if sale["parcel_number"] == "c" else sale for sale in candidates[:8]]
    assert current_valuation(parcel, building, {"candidates": historical})["market_range"] is None


def test_missing_assessment_and_comps_stay_unavailable():
    report = current_valuation({}, {"assessed_value": float("inf")}, {})
    assert report["market_range"] is None
    assert report["assessment"] is None
    assert report["market_evidence"]["qualifying_count"] is None


def test_gorham_sale_matches_use_lot_size_instead_of_assessor_class():
    parcel = {"parcel_number": "subject", "fiscal_year": 2025, "gis_area_sqft_approx": 4996}
    building = {"residential_area_sqft": 2722, "bedrooms_reported": 5, "bathrooms_reported": 2}
    candidates = [
        {"parcel_number": str(i), "sale_price": 1430000, "deed_verification": {
            "status": "verified", "consideration": 1430000,
        }, "comparison_label": "Similar residential sale · different assessor class",
         "comparison_area_sqft": 2945, "bedrooms_reported": 6, "bathrooms_reported": 3,
         "arm_length_status": "City sale flag Q", "comparison_area_basis": "assessor living area",
         "address": address, "distance_miles": .05 + i * .05, "lot_area_sqft": lot}
        for i, (address, lot) in enumerate([
            ("9 Jay St", 3905), ("30–32 Whitman St", 3238), ("62 Chandler St", 4609), ("47 Newbury St", 3937),
        ])
    ]
    report = current_valuation(parcel, building, {"candidates": candidates})
    assert report["market_range"]["comp_addresses"] == ["9 Jay St", "62 Chandler St", "47 Newbury St"]
    assert report["market_evidence"] == {"confirmed_count": 4, "qualifying_count": 3}
    candidates[0].pop("lot_area_sqft")
    assert current_valuation(parcel, building, {"candidates": candidates})["market_range"] is None
