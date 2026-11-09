"""Cambridge assessor closed-sale leads for one- to three-family properties."""
from datetime import date, timedelta
from math import ceil, floor

import httpx

import registry
from boston import sale_date
from condo_analysis import CAMBRIDGE_ASSESSOR, CAMBRIDGE_ASSESSOR_API, attach_cambridge_distances
from ma_sales import use_group
from valuation import positive_number

CLASSES = {"single family": "101", "two family": "104", "three family": "105"}


async def search(client, lon, lat, parcel, building):
    building = building or {}
    code = CLASSES.get(use_group(building.get("use_description")))
    area = building.get("residential_area_sqft")
    if not code or not positive_number(area):
        return None

    today = date.today()
    cutoff = today - timedelta(days=1827)
    low, high = ceil(area * .85), floor(area * 1.15)
    response = await client.get(CAMBRIDGE_ASSESSOR_API, params={
        "$select": "address,gisid,stateclasscode,saleprice,saledate,book_page,interior_livingarea,interior_bedrooms,interior_fullbaths,interior_halfbaths,unit",
        "$where": (f"stateclasscode = '{code}' AND saleprice >= 10000 AND saledate >= '{cutoff.isoformat()}T00:00:00'"
                   f" AND interior_livingarea between {low} and {high}"),
        "$order": "saledate DESC", "$limit": 101,
    }, timeout=httpx.Timeout(8, connect=4))
    response.raise_for_status()
    records = response.json()
    if not isinstance(records, list):
        raise ValueError("Cambridge assessor sales response unavailable")

    candidates = {}
    for row in records[:100]:
        sold = sale_date(row.get("saledate"))
        pid, address = row.get("gisid"), row.get("address")
        ref = row.get("book_page")
        if (row.get("stateclasscode") != code or row.get("unit") or not pid or not address
                or pid == parcel.get("parcel_number") or not sold or not cutoff <= sold <= today
                or not registry.reference_pair(ref)):
            continue
        size, price = int(row["interior_livingarea"]), int(row["saleprice"])
        if not low <= size <= high or price < 10000:
            continue
        candidates.setdefault(pid, {"address": address, "project_key": pid, "parcel_number": pid,
            "sale_date": sold.isoformat(), "sale_price": price, "record_reference": ref,
            "comparison_area_sqft": size, "comparison_area_basis": "assessor living area",
            "comparison_label": "Same assessor class", "price_per_comparison_sqft": round(price / size),
            "older_reference": (today - sold).days > 1096, "source_url": CAMBRIDGE_ASSESSOR,
            "source_name": "Cambridge assessor", "arm_length_status": "Sale conditions require review"})
    await attach_cambridge_distances(client, list(candidates.values()), lon, lat)
    matches = [row for row in candidates.values() if row.get("distance_miles", float("inf")) <= 3]
    selected = registry.shortlist("CAMBRIDGE", matches)
    if not selected:
        return None
    return {
        "status": "Cambridge assessor sale leads", "candidates": selected, "count": len(selected),
        "truncated": len(records) > 100, "expanded_search": True, "source_url": CAMBRIDGE_ASSESSOR,
        "method": "Same Cambridge assessor class, within 15% of reported living area and up to 3 miles. Recent sales take priority. Sales older than 3 years, up to 5 years, are historical references.",
        "limitations": "The assessor feed does not establish transaction conditions. Review room counts and check the recorded deed before relying on a sale.",
    }
