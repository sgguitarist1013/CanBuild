"""On-demand Somerville zoning amendment screen from the City's Legistar API."""
import asyncio
from datetime import date
import re

import httpx


MATTERS = "https://webapi.legistar.com/v1/somervillema/matters"
LEGISLATION = "https://somervillema.legistar.com/LegislationDetail.aspx"
SNAPSHOT_AMENDMENTS_THROUGH = date(2025, 5, 22)
TERMINAL = {"ordained", "withdrawn", "placed on file", "denied", "rejected"}
SECTION = re.compile(r"(?<!\d)(\d{1,2}(?:\.\d{1,2}){1,2})(?!\d)")
NR_PREFIXES = ("2.1.", "2.3.", "2.4.", "3.1.", "3.2.", "8.1.", "9.2.", "10.", "12.1.", "14.1.")


def _after_snapshot(row):
    for field in ("MatterEnactmentDate", "MatterPassedDate", "MatterIntroDate"):
        value = row.get(field)
        if value:
            try:
                if date.fromisoformat(value[:10]) > SNAPSHOT_AMENDMENTS_THROUGH:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _relevant(row):
    title = str(row.get("MatterTitle") or "")
    matter_type = str(row.get("MatterTypeName") or "").lower()
    if not any(kind in matter_type for kind in ("mayor's request", "zoning ordinance", "ordinance")):
        return False
    if any(section.startswith(NR_PREFIXES) for section in SECTION.findall(title)):
        return True
    return bool(re.search(r"\bzoning\b.*\b(?:amend|ordinance|district|overlay|upzon|bonus|ordinance)", title, re.I))


def _format(row):
    status = str(row.get("MatterStatusName") or "Unknown")
    enacted = status.lower() == "ordained" and bool(row.get("MatterEnactmentDate"))
    awaiting_code_check = status.lower() == "approved" and bool(row.get("MatterEnactmentDate"))
    return {
        "file": row.get("MatterFile"),
        "title": row.get("MatterTitle"),
        "status": status,
        "disposition": "enacted_after_map" if enacted and _after_snapshot(row) else
                       "enacted_before_or_at_map" if enacted else
                       "approved_needs_code_check" if awaiting_code_check and _after_snapshot(row) else
                       "terminal_without_ordinance" if status.lower() in TERMINAL else "pending_or_unresolved",
        "enactment_date": row.get("MatterEnactmentDate"),
        "sections": sorted(set(SECTION.findall(str(row.get("MatterTitle") or "")))),
        "source_url": f"{LEGISLATION}?GUID={row.get('MatterGuid')}&ID={row.get('MatterId')}",
    }


async def screen_current_amendments(client, *, today=None):
    """List relevant enacted or unresolved matters since the zoning-map cutoff.

    This is a change detector, not a legal conclusion. Even a clean result does
    not certify that the live ordinance text and dimensional rules were reviewed.
    """
    today = today or date.today()
    years = range(SNAPSHOT_AMENDMENTS_THROUGH.year % 100, today.year % 100 + 1)
    async def fetch(year):
        prefix = f"{year:02d}-"
        response = await client.get(MATTERS, params={
            "$filter": f"startswith(MatterFile,'{prefix}') and (MatterTypeName eq 'Mayor''s Request' or MatterTypeName eq 'Zoning Ordinance' or MatterTypeName eq 'Ordinance')",
            "$orderby": "MatterLastModifiedUtc desc", "$top": 1000,
            "$select": "MatterId,MatterGuid,MatterFile,MatterTitle,MatterTypeName,MatterStatusName,MatterEnactmentDate,MatterPassedDate,MatterIntroDate,MatterLastModifiedUtc",
        }, timeout=httpx.Timeout(8, connect=3))
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or len(rows) >= 1000:
            raise ValueError("Legistar amendment query was incomplete")
        return rows

    try:
        pages = await asyncio.gather(*(fetch(year) for year in years))
    except (httpx.HTTPError, ValueError, TypeError):
        return {"status": "unavailable", "complete": False, "current_rules_verified": False,
                "source_url": MATTERS, "snapshot_amendments_through": SNAPSHOT_AMENDMENTS_THROUGH.isoformat(),
                "message": "Official zoning amendment history could not be checked."}

    records = {}
    for row in (item for page in pages for item in page):
        if _relevant(row) and _after_snapshot(row):
            records[row.get("MatterFile") or row.get("MatterId")] = _format(row)
    matters = sorted(records.values(), key=lambda row: (row.get("file") or "", row.get("title") or ""))
    pending = [row for row in matters if row["disposition"] in {"pending_or_unresolved", "approved_needs_code_check"}]
    enacted = [row for row in matters if row["disposition"] == "enacted_after_map"]
    return {
        "status": "changes_need_review" if pending or enacted else "no_relevant_changes_found",
        "complete": True,
        "current_rules_verified": False,
        "snapshot_amendments_through": SNAPSHOT_AMENDMENTS_THROUGH.isoformat(),
        "pending_count": len(pending), "enacted_after_snapshot_count": len(enacted),
        "pending": pending, "enacted_after_snapshot": enacted,
        "source_url": MATTERS,
        "message": ("Current code still needs review against the live ordinance and every listed amendment."
                    if pending or enacted else
                    "No later relevant ordinance was found, but this check does not verify the current code text."),
    }
