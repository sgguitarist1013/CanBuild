import httpx
import pytest

from somerville_legislation import screen_current_amendments, triple_decker_rules_current


@pytest.mark.anyio
async def test_legistar_change_screen_finds_enacted_and_pending_zoning_changes():
    def handler(request):
        prefix = "25-" if "25-" in request.url.params["$filter"] else "26-"
        if prefix == "25-":
            rows = [{"MatterId": 1, "MatterGuid": "first-guid", "MatterFile": "25-0999",
                     "MatterTitle": "Amending zoning ordinance Sections 3.1.8 and 3.1.11",
                     "MatterTypeName": "Zoning Ordinance", "MatterStatusName": "Ordained",
                     "MatterEnactmentDate": "2025-06-25T00:00:00"}]
        else:
            rows = [{"MatterId": 2, "MatterGuid": "second-guid", "MatterFile": "26-1336",
                     "MatterTitle": "Requesting ordainment of an amendment to Sections 3.1.3 and 3.1.6",
                     "MatterTypeName": "Mayor's Request", "MatterStatusName": "Referred for Recommendation",
                     "MatterIntroDate": "2026-09-08T00:00:00"},
                    {"MatterId": 3, "MatterGuid": "third-guid", "MatterFile": "26-0061",
                     "MatterTitle": "Transit-oriented zoning bonuses for additional affordable housing",
                     "MatterTypeName": "Mayor's Request", "MatterStatusName": "Sent for Discussion",
                     "MatterIntroDate": "2026-01-08T00:00:00"},
                    {"MatterId": 5, "MatterGuid": "resident-zoning", "MatterFile": "26-0287",
                     "MatterTitle": "26 registered voters requesting a Zoning Text Amendment to amend sections 3.1.8c, 3.1.9c, and 3.1.10c",
                     "MatterTypeName": "Public Communication", "MatterStatusName": "Ordained",
                     "MatterEnactmentDate": "2026-06-25T00:00:00"},
                    {"MatterId": 4, "MatterGuid": "other-guid", "MatterFile": "26-0004",
                     "MatterTitle": "Approve city contract expenditure", "MatterTypeName": "Mayor's Request",
                     "MatterStatusName": "Approved", "MatterIntroDate": "2026-01-08T00:00:00"}]
        return httpx.Response(200, json=rows)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await screen_current_amendments(client)
    assert result["complete"] is True
    assert result["current_rules_verified"] is False
    assert result["enacted_after_snapshot_count"] == 2
    assert result["pending_count"] == 2
    assert {row["file"] for row in result["enacted_after_snapshot"] + result["pending"]} == {
        "25-0999", "26-1336", "26-0061", "26-0287",
    }
    assert result["pending"][0]["source_url"].startswith("https://somervillema.legistar.com/")


@pytest.mark.anyio
async def test_legistar_unavailable_never_certifies_current_zoning():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        result = await screen_current_amendments(client)
    assert result["status"] == "unavailable"
    assert result["complete"] is False
    assert result["current_rules_verified"] is False


def test_triple_decker_rule_audit_ignores_unenacted_proposals_and_unrelated_sections():
    audit = triple_decker_rules_current({
        "complete": True,
        "enacted_after_snapshot": [{"file": "26-0287", "sections": ["3.1.13"]}],
        "pending": [{"file": "26-1337", "sections": ["3.1.17"]}],
    })
    assert audit["verified"] is True
    assert [row["file"] for row in audit["pending_matters_not_applied"]] == ["26-1337"]


def test_triple_decker_rule_audit_blocks_if_a_governing_section_changed():
    audit = triple_decker_rules_current({
        "complete": True,
        "enacted_after_snapshot": [{"file": "26-9999", "sections": ["3.1.11"]}],
        "pending": [],
    })
    assert audit["verified"] is False
