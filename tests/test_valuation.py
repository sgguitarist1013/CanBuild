from valuation import current_valuation


def test_as_is_estimate_uses_three_distinct_registry_confirmed_like_sales():
    parcel = {"parcel_number": "subject", "fiscal_year": 2026}
    building = {"assessed_value": 900000, "residential_area_sqft": 2000,
                "bedrooms_reported": 3, "bathrooms_reported": 2}
    base = {
        "comparison_label": "Same assessor class", "arm_length_status": "City sale flag Q",
        "comparison_area_basis": "assessor living area", "bedrooms_reported": 3, "bathrooms_reported": 2,
    }

    def comp(identifier, distance, size, price, status="verified", **changes):
        return {**base, "parcel_number": identifier, "address": identifier, "distance_miles": distance,
                "comparison_area_sqft": size, "sale_price": price,
                "deed_verification": {"status": status, "consideration": price}, **changes}

    candidates = [
        comp("a", .12, 2000, 1000000),
        comp("a", .13, 2000, 1900000),
        comp("pending", .15, 2000, 1900000, "pending"),
        comp("other class", .2, 2000, 1900000, comparison_label="Similar residential sale"),
        comp("wrong size", .22, 2301, 1900000),
        comp("wrong beds", .23, 2000, 1900000, bedrooms_reported=5),
        comp("b", .25, 1800, 990000),
        comp("c", .55, 2200, 1320000),
        comp("far", .8, 2000, 1900000),
    ]
    report = current_valuation(parcel, building, {"candidates": candidates})
    assert report["market_range"] == {"low": 1000000, "high": 1200000, "comp_count": 3,
                                       "radius_miles": .55, "comp_addresses": ["a", "b", "c"]}
    assert report["assessment"] == {"amount": 900000, "fiscal_year": 2026}
    assert current_valuation(parcel, building, {"candidates": candidates[:7]})["market_range"] is None
    assert current_valuation(parcel, building, {"candidates": candidates, "truncated": True})["market_range"] is None
    historical = [dict(sale, older_reference=True) if sale["parcel_number"] == "c" else sale for sale in candidates[:8]]
    assert current_valuation(parcel, building, {"candidates": historical})["market_range"] is None


def test_missing_assessment_and_comps_stay_unavailable():
    assert current_valuation({}, {"assessed_value": float("inf")}, {}) == {"market_range": None, "assessment": None}
