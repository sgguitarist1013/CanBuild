"""Cambridge assessor closed-sale leads for one- to three-family properties."""
from datetime import date, timedelta
from math import ceil, floor
import re

import httpx

import registry
from boston import sale_date
from condo_analysis import CAMBRIDGE_ASSESSOR, CAMBRIDGE_ASSESSOR_API, attach_cambridge_distances
from ma_sales import use_group
from valuation import positive_number

CLASSES = {"single family": "101", "two family": "104", "three family": "105"}
LAYOUT_FIELDS = "interior_bedrooms,interior_fullbaths,interior_halfbaths"
LAYOUT_NOTES = {
    "similar": "Assessor room counts are within one bedroom and one bathroom.",
    "different": "Size-matched reference. The room counts differ by more than one bedroom or bathroom.",
    "unknown": "Size-matched reference. Room-count similarity could not be checked.",
}


def reported_count(value):
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return value if type(value) is int and value >= 0 else None


def reported_layout(row):
    bedrooms = reported_count(row.get("interior_bedrooms"))
    full = reported_count(row.get("interior_fullbaths"))
    half = reported_count(row.get("interior_halfbaths"))
    bathrooms = full + half / 2 if full is not None and half is not None else None
    return {"bedrooms_reported": bedrooms, "bathrooms_reported": bathrooms}


def layout_match(comp, subject):
    comp_beds, comp_baths = comp["bedrooms_reported"], comp["bathrooms_reported"]
    subject_beds, subject_baths = subject.get("bedrooms_reported"), subject.get("bathrooms_reported")
    if not all(positive_number(count) for count in (comp_beds, comp_baths, subject_beds, subject_baths)):
        return "unknown"
    return "similar" if abs(comp_beds - subject_beds) <= 1 and abs(comp_baths - subject_baths) <= 1 else "different"


async def subject_layout(client, parcel):
    """Use Cambridge's room counts only for a unique, exact non-unit parcel record."""
    map_lot = str(parcel.get("parcel_number") or "")
    subject_address = registry.address_key(parcel.get("assessor_site_address"))
    if not re.fullmatch(r"[0-9]+-[0-9]+", map_lot) or not subject_address[0] or subject_address[1]:
        return {}

    response = await client.get(CAMBRIDGE_ASSESSOR_API, params={
        "$select": f"gisid,address,unit,{LAYOUT_FIELDS}",
        "$where": f"gisid = '{map_lot}' AND (unit IS NULL OR unit = '')",
        "$limit": 2,
    }, timeout=httpx.Timeout(5, connect=3))
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list) or len(rows) != 1:
        return {}
    row = rows[0]
    if (not isinstance(row, dict) or row.get("gisid") != map_lot or row.get("unit")
            or registry.address_key(row.get("address")) != subject_address):
        return {}
    layout = {key: value for key, value in reported_layout(row).items() if positive_number(value)}
    if layout:
        layout["assessor_layout_source_url"] = CAMBRIDGE_ASSESSOR
    return layout


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
        "$select": f"address,gisid,stateclasscode,saleprice,saledate,book_page,interior_livingarea,unit,{LAYOUT_FIELDS}",
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
        try:
            size, price = int(row["interior_livingarea"]), int(row["saleprice"])
        except (KeyError, TypeError, ValueError):
            continue
        if not low <= size <= high or price < 10000:
            continue
        layout = reported_layout(row)
        similarity = layout_match(layout, building)
        candidates.setdefault(pid, {"address": address, "project_key": pid, "parcel_number": pid,
            "sale_date": sold.isoformat(), "sale_price": price, "record_reference": ref,
            "comparison_area_sqft": size, "comparison_area_basis": "assessor living area",
            "comparison_label": "Same assessor class", "price_per_comparison_sqft": round(price / size),
            "older_reference": (today - sold).days > 1096, "source_url": CAMBRIDGE_ASSESSOR,
            "source_name": "Cambridge assessor", "arm_length_status": "Sale conditions require review",
            "layout_match": similarity, "layout_note": LAYOUT_NOTES[similarity], **layout})
    await attach_cambridge_distances(client, list(candidates.values()), lon, lat)
    matches = [row for row in candidates.values() if row.get("distance_miles", float("inf")) <= 3]
    selected = registry.shortlist("CAMBRIDGE", matches)
    if not selected:
        return None
    return {
        "status": "Cambridge assessor sale leads", "candidates": selected, "count": len(selected),
        "truncated": len(records) > 100, "expanded_search": True, "source_url": CAMBRIDGE_ASSESSOR,
        "method": "Same Cambridge assessor class, within 15% of reported living area and up to 3 miles. Recent sales with similar reported bedroom and bathroom counts take priority. Sales older than 3 years, up to 5 years, are historical references.",
        "limitations": "The assessor feed does not establish transaction conditions. Size-matched records with different or unconfirmed room layouts are reference leads. Check the recorded deed before relying on a sale.",
    }
