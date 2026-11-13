"""Separate current market evidence from the tax assessment."""
from math import ceil, floor, isfinite


def positive_number(value):
    return type(value) in (int, float) and isfinite(value) and value > 0


def current_valuation(parcel, building, sales):
    building = building or {}
    assessment = building.get("assessed_value")
    result = {
        "market_range": None,
        "assessment": {"amount": assessment, "fiscal_year": parcel.get("fiscal_year")}
        if positive_number(assessment) else None,
    }
    area = building.get("residential_area_sqft")
    beds = building.get("bedrooms_reported")
    baths = building.get("bathrooms_reported")
    if sales.get("truncated") or not all(positive_number(n) for n in (area, beds, baths)):
        return result

    eligible = []
    for sale in sales.get("candidates", ()):
        deed = sale.get("deed_verification") or {}
        size = sale.get("comparison_area_sqft")
        sale_beds, sale_baths = sale.get("bedrooms_reported"), sale.get("bathrooms_reported")
        distance = sale.get("distance_miles")
        price = deed.get("consideration")
        lot_area = sale.get("lot_area_sqft")
        subject_lot = (parcel or {}).get("gis_area_sqft_approx")
        lot_match = (not positive_number(subject_lot) or
                     (positive_number(lot_area) and abs(lot_area-subject_lot) <= .30*subject_lot))
        if (deed.get("status") == "verified" and price == sale.get("sale_price")
                and sale.get("comparison_area_basis") == "assessor living area"
                and sale.get("parcel_number") and sale["parcel_number"] != parcel.get("parcel_number")
                and all(positive_number(n) for n in (size, sale_beds, sale_baths, price))
                and type(distance) in (int, float) and isfinite(distance) and distance >= 0
                and abs(size - area) <= .15 * area
                and abs(sale_beds - beds) <= 1 and abs(sale_baths - baths) <= 1
                and lot_match):
            eligible.append(sale)
    matches = {}
    for sale in sorted(eligible, key=lambda row: (row["distance_miles"], abs(row["comparison_area_sqft"] - area))):
        matches.setdefault(sale["parcel_number"], sale)
    nearby = [sale for sale in matches.values() if sale["distance_miles"] <= .5]
    selected = nearby[:4] if len(nearby) >= 3 else list(matches.values())[:3]
    if len(selected) >= 3:
        prices = [sale["deed_verification"]["consideration"] / sale["comparison_area_sqft"] * area for sale in selected]
        result["market_range"] = {
            "low": floor(min(prices) / 10000) * 10000,
            "high": ceil(max(prices) / 10000) * 10000,
            "comp_count": len(selected),
            "radius_miles": max(sale["distance_miles"] for sale in selected),
            "comp_addresses": [sale["address"] for sale in selected],
        }
    return result
