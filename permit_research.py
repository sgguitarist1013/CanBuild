"""Read-only, source-linked municipal permit history."""
from datetime import date
import re
from urllib.parse import urlsplit

import httpx

from parcel_engine import street_tokens

BOSTON_DATA = "https://data.boston.gov/dataset/approved-building-permits"
BOSTON_SQL = "https://data.boston.gov/api/3/action/datastore_search_sql"
BOSTON_TABLE = "6ddcd912-32a0-43df-9908-63574f8c7e77"
CAMBRIDGE_DATA = "https://data.cambridgema.gov/d/3wsm-e5jx"
CAMBRIDGE_API = "https://data.cambridgema.gov/resource/3wsm-e5jx.json"
SOMERVILLE_DATA = "https://data.somervillema.gov/d/vxgw-vmky"
SOMERVILLE_API = "https://data.somervillema.gov/resource/vxgw-vmky.json"
SOURCES = {
    "BOSTON": (BOSTON_DATA, "https://www.boston.gov/departments/inspectional-services/how-find-historical-permit-records",
               "The city publishes approved building permits issued after December 15, 2009."),
    "CAMBRIDGE": (CAMBRIDGE_DATA, "https://www.cambridgema.gov/inspection/codesandmaps/recordsandarchives",
                  "The city’s Permit Finder combines multiple permit types and is updated on a published schedule."),
    "SOMERVILLE": (SOMERVILLE_DATA, "https://www.somervillema.gov/citizenserve",
                   "Historical city open-data snapshot. Use the city permit portal for newer records."),
}


def coverage(city):
    source = SOURCES.get(city.upper())
    if not source:
        return None
    return {"source_url": source[0], "official_lookup_url": source[1], "coverage_note": source[2]}


def permit_date(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def compact(value, limit):
    return " ".join(str(value or "").split())[:limit]


def record(identifier, permit_type, address, issued, status, source, description=None):
    return {
        "permit_id": compact(identifier, 80), "permit_type": compact(permit_type, 110),
        "address": compact(address, 180), "issued_on": permit_date(issued),
        "reported_status": compact(status, 80), "description": compact(description, 350),
        "source_url": source,
    }


def prioritize(records):
    material = re.compile(r"addition|alteration|new construction|occupancy|demolition|zoning appeal|conversion|long form", re.I)
    # Rank project evidence ahead of routine trades, then keep the newest in each group.
    rows = sorted(records, key=lambda row: row["issued_on"] or "", reverse=True)
    rows.sort(key=lambda row: not material.search(row["permit_type"] + " " + row["description"]))
    return rows[:8]


async def rows(client, url, params):
    response = await client.get(url, params=params, timeout=httpx.Timeout(9, connect=4))
    response.raise_for_status()
    data = response.json()
    if url == BOSTON_SQL:
        if not isinstance(data, dict) or not data.get("success"):
            raise ValueError("Boston permit source unavailable")
        data = data.get("result", {}).get("records")
    if not isinstance(data, list):
        raise ValueError("Permit records unavailable")
    return data


async def boston_permits(client, parcel):
    if not re.fullmatch(r"\d{10}", parcel):
        return None, "Boston permit matching needs a ten-digit city assessor parcel ID."
    sql = (
        f'SELECT permitnumber, permittypedescr, description, issued_date, status, address, parcel_id FROM "{BOSTON_TABLE}" '
        f'WHERE parcel_id = {int(parcel)} ORDER BY issued_date DESC NULLS LAST LIMIT 51'
    )
    data = await rows(client, BOSTON_SQL, {"sql": sql})
    found = [record(r.get("permitnumber"), r.get("permittypedescr"), r.get("address"),
                    r.get("issued_date"), r.get("status"), BOSTON_DATA, r.get("description"))
             for r in data if str(r.get("parcel_id")) == str(int(parcel)) and r.get("permitnumber")]
    return found, "Matched by Boston assessor parcel ID."


def cambridge_link(value):
    if not isinstance(value, dict):
        return CAMBRIDGE_DATA
    url = value.get("url")
    if not isinstance(url, str):
        return CAMBRIDGE_DATA
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.netloc == "data.cambridgema.gov" and re.fullmatch(r"/resource/[a-z0-9]{4}-[a-z0-9]{4}\.json", parsed.path):
        return url
    return CAMBRIDGE_DATA


async def cambridge_permits(client, parcel):
    if not re.fullmatch(r"[0-9]+[A-Za-z]?-[0-9]+[A-Za-z]?", parcel):
        return None, "Cambridge permit matching needs a city map and lot identifier."
    data = await rows(client, CAMBRIDGE_API, {"$select": "full_address,permit_type,id,mbl,issue_date,status,specific_url",
        "$where": f"mbl='{parcel}'", "$order": "issue_date DESC NULL LAST,id DESC", "$limit": 51})
    found = [record(r.get("id"), r.get("permit_type"), r.get("full_address"), r.get("issue_date"),
                    r.get("status"), cambridge_link(r.get("specific_url")))
             for r in data if r.get("mbl") == parcel and r.get("id")]
    return found, "Matched by Cambridge map and lot."


async def somerville_permits(client, address):
    street = street_tokens(address.split(",", 1)[0])
    if len(street) < 2 or not re.fullmatch(r"[0-9]+[A-Z]?", street[0]):
        return None, "Somerville permit matching needs a street number and street name."
    # Broad street prefix allows the source to use its own suffix abbreviation.
    # An exact normalized street address is still required for every returned row.
    prefix = " ".join(street[:2])
    data = await rows(client, SOMERVILLE_API, {"$select": "address,type,id,issue_date,status,work",
        "$where": f"upper(address) like '{prefix}%'", "$order": "issue_date DESC,id DESC", "$limit": 51})
    found = [record(r.get("id"), r.get("type"), r.get("address"), r.get("issue_date"),
                    r.get("status"), SOMERVILLE_DATA, r.get("work"))
             for r in data if street_tokens(str(r.get("address") or "").split(",", 1)[0]) == street and r.get("id")]
    return found, "Matched by exact Somerville street address in the historical dataset."


async def search(client, city, address, parcel):
    city = city.upper()
    report = coverage(city)
    if report is None:
        return {"status": "unsupported", "records": []}
    if city == "BOSTON":
        found, match = await boston_permits(client, parcel)
    elif city == "CAMBRIDGE":
        found, match = await cambridge_permits(client, parcel)
    else:
        found, match = await somerville_permits(client, address)
    if found is None:
        return {**report, "status": "needs_identity", "records": [], "match_note": match}
    # A permit can appear more than once in a city extract.
    unique = {}
    for row in found:
        unique.setdefault((row["permit_type"], row["permit_id"]), row)
    return {**report, "status": "found" if unique else "no_matches", "records": prioritize(list(unique.values())),
            "match_note": match, "checked_on": date.today().isoformat()}
