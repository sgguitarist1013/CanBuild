"""MassLandRecords review status, district routing, and verified sale data."""
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import re
from statistics import median

from parcel_engine import street_tokens

DOCS = Path(__file__).parent / "docs"
HOME = "https://www.masslandrecords.com/"
DISTRICTS = json.loads((DOCS / "massachusetts_registry_districts.json").read_text())
# Use the statewide selector until a district-specific portal URL is reviewed.
PORTALS = {"Middlesex South": HOME + "MiddlesexSouth/D/", "Suffolk": HOME + "Suffolk/"}
LAND_COURT = "Registered Land (Land Court)"
BOOK_PAGE = re.compile(r"(?:BOOK\s*)?([0-9]+)\s*(?:/|,?\s*PAGE\s*)\s*([0-9]+)", re.I)


def address_key(address):
    # Keep unit designators and house ranges; a building match cannot verify a condo unit.
    line = str(address or "").split(",", 1)[0].strip()
    unit = re.search(r"\s+(?:UNIT|APT\.?|#)\s*([\w-]+)$", line, re.I)
    street = line[:unit.start()] if unit else line
    return " ".join(street_tokens(street)), unit[1].upper() if unit else ""


def reference_pair(reference):
    match = BOOK_PAGE.fullmatch(str(reference or "").strip())
    return tuple(int(group) for group in match.groups()) if match and all(int(group) for group in match.groups()) else None


def transfer_pair(transfer):
    book, page = transfer.get("book"), transfer.get("page")
    return reference_pair(f"{book}/{page}") if book and page else reference_pair(transfer.get("record_reference"))


def verified_index():
    index = {}
    for row in json.loads((DOCS / "verified_registry_sales.json").read_text()):
        city = row["municipality"].upper()
        key = (city, address_key(row["address"]), (row["book"], row["page"]))
        if (key in index or city not in DISTRICTS or row["office"] not in ("Recorded Land", "Registered Land (Land Court)")
                or not isinstance(row["consideration"], int) or row["consideration"] <= 0
                or not 0 < row["book"] or not 0 < row["page"]
                or date.fromisoformat(row["checked_on"]) < date.fromisoformat(row["recorded_date"])):
            raise ValueError(f"Invalid registry verification: {key}")
        index[key] = row
    return index


VERIFIED = verified_index()


def city_key(municipality):
    city = str(municipality or "").strip().upper()
    return {"MANCHESTER": "MANCHESTER-BY-THE-SEA", "NORTH ATTLEBORO": "NORTH ATTLEBOROUGH"}.get(city, city)


def district_for(municipality):
    district = DISTRICTS.get(city_key(municipality))
    return {"district": district, "url": PORTALS.get(district, HOME)}


def check(municipality, address, reference, reported_price=None, registered_reference=None):
    city = city_key(municipality)
    route = district_for(city)
    registered_reference = str(registered_reference or "").strip()
    result = {**route, "status": "pending" if reference or registered_reference else "reference_needed"}
    if reference:
        result.update(book=reference[0], page=reference[1])
    if registered_reference:
        result["registered_reference"] = registered_reference
    reviewed = VERIFIED.get((city, address_key(address), reference))
    if not reviewed:
        return result
    if registered_reference and (reviewed["office"] != LAND_COURT
                                 or reviewed.get("registered_reference") != registered_reference):
        return result
    if reported_price is not None and reported_price != reviewed["consideration"]:
        result["status"] = "conflict"
        return result
    return {**result, **reviewed, "status": "verified"}


def sale_deed(city, sale):
    return check(city, sale.get("address"), reference_pair(sale.get("record_reference")),
                 sale.get("sale_price"), registered_reference=sale.get("registered_reference"))


def shortlist(city, candidates, limit=12):
    """Keep confirmed sales from being crowded out by closer unconfirmed leads."""
    ranked = sorted(candidates, key=lambda row: (
        bool(row.get("older_reference")),
        row["distance_miles"],
        -int(row["sale_date"].replace("-", "")),
    ))
    verified, pending = [], []
    for sale in ranked:
        group = verified if sale_deed(city, sale)["status"] == "verified" else pending
        group.append(sale)
    recent = [sale for sale in verified if not sale.get("older_reference")]
    close = [sale for sale in recent if sale["distance_miles"] <= .5]
    if len(close) >= 3:
        return close[:min(4, limit)]
    if len(recent) >= 3:
        return recent[:min(3, limit)]
    selected = verified[:min(4, limit)]
    return selected + pending[:limit - len(selected)]


def sale_report(city, report):
    """Unverified leads stay separate and cannot set any price statistic."""
    candidates = report.get("candidates", [])
    if not candidates:
        return report
    verified, pending = [], []
    for sale in candidates:
        deed = sale_deed(city, sale)
        if deed["status"] == "verified":
            verified.append({**sale, "deed_verification": deed})
        else:
            lead = {"address": sale.get("address"), "deed_verification": deed}
            if report.get("expanded_search"):
                lead.update({key: sale[key] for key in ("sale_date", "distance_miles", "comparison_area_sqft",
                            "comparison_area_basis", "older_reference", "source_url") if key in sale})
            pending.append(lead)
    recent = [sale for sale in verified if not sale.get("older_reference")]
    result = {**report, "candidates": verified, "pending_registry_review": pending, "count": len(verified)}
    result["status"] = f"{len(verified)} registry-confirmed sale reference(s)"
    if pending:
        result["status"] += f" · {len(pending)} awaiting registry confirmation"
    if "median_sale_price" in result:
        result["median_sale_price"] = median(r["sale_price"] for r in recent) if len(recent) >= 3 else None
    if "same_class_count" in result:
        result["same_class_count"] = sum(r.get("comparison_label") == "Same assessor class" for r in verified)
    if "observed_price_per_sqft_low" in result:
        rates = [r["price_per_living_sqft"] for r in recent]
        usable = len(rates) >= 3 and not report.get("truncated")
        result["observed_price_per_sqft_low"] = min(rates) if usable else None
        result["observed_price_per_sqft_high"] = max(rates) if usable else None
    return result


def enqueue(connect, city, items):
    pending = [(city_key(city), item["address"], json.dumps(item["deed_verification"], sort_keys=True)) for item in items
               if city and item.get("address") and item["deed_verification"]["status"] != "verified"]
    if not pending:
        return
    with closing(connect()) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS registry_review (
            city TEXT NOT NULL, address TEXT NOT NULL, evidence TEXT NOT NULL,
            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (city, address, evidence)
        )""")
        db.executemany("""INSERT INTO registry_review (city, address, evidence) VALUES (?, ?, ?)
            ON CONFLICT(city,address,evidence) DO UPDATE SET requested_at=CURRENT_TIMESTAMP""", pending)


def pending_review(row):
    evidence = json.loads(row["evidence"])
    pair = transfer_pair(evidence)
    # Preserve price conflicts as outstanding even when a deed exists.
    if evidence["status"] == "conflict" or check(
        row["city"], row["address"], pair, registered_reference=evidence.get("registered_reference"),
    )["status"] != "verified":
        return {"id": row["id"], "municipality": row["city"], "address": row["address"],
                "deed_verification": evidence}
    return None


def review_queue(connect, limit, review_id=None):
    with closing(connect()) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='registry_review'").fetchone()
        if not exists:
            return []
        query = "SELECT rowid AS id, * FROM registry_review"
        rows = (db.execute(query + " WHERE rowid = ?", (review_id,)) if review_id is not None
                else db.execute(query + " ORDER BY requested_at DESC, rowid DESC"))
        pending = []
        for row in rows:
            review = pending_review(row)
            if review:
                pending.append(review)
                if len(pending) >= limit:
                    break
        return pending
