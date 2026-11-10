import asyncio

import httpx
from fastapi.testclient import TestClient

from app import app
import zoning_research as zr


def test_state_directory_covers_all_municipalities():
    from municipal_zoning import MUNICIPALITIES

    assert set(zr.WEBSITES) == set(MUNICIPALITIES)
    assert zr.WEBSITES["NORTH ATTLEBOROUGH"] == "https://www.nattleboro.com/"


def test_fetch_rejects_unapproved_redirect_and_mismatched_code():
    async def run():
        def respond(request):
            assert request.url.host in {"example.gov", "ecode360.com"}
            if request.url.host == "example.gov":
                return httpx.Response(302, headers={"location": "https://example.gov.evil.test/zoning"})
            return httpx.Response(200, text="<title>City of Another Town, MA</title>", headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await zr.fetch_document(client, "https://example.gov/zoning", {"example.gov"}, "REVERE") is None
            assert await zr.fetch_document(client, "https://ecode360.com/12345", {"example.gov"}, "REVERE") is None

    asyncio.run(run())


def test_chicopee_district_text_and_grounded_candidates():
    html = b"<title>Chicopee Zoning Ordinance</title><main><p>In the Residence B district the minimum front setback is 20 feet, subject to other provisions.</p></main>"
    document = zr.parse_document(html, "text/html", "https://www.chicopeema.gov/zoning", "CHICOPEE")
    rows = zr.evidence([document], "ResB")
    assert rows[0]["district_text_match"]
    assert zr.grounded_rules([
        {"excerpt_index": 0, "field": "setback", "source_text": "20 feet"},
        {"excerpt_index": 0, "field": "setback", "source_text": "30 feet"},
        {"excerpt_index": -1, "field": "setback", "source_text": "20 feet"},
    ], rows) == [{"excerpt_index": 0, "field": "setback", "source_text": "20 feet"}]


def test_single_shared_refresh_and_changed_source_reopens_queue(tmp_path, monkeypatch):
    cache = zr.ResearchCache(tmp_path / "research.sqlite")
    count = 0

    async def discover(city, district, registered):
        nonlocal count
        count += 1
        await asyncio.sleep(0)
        return {
            "sources": [{"url": zr.WEBSITES[city], "title": "Ordinance", "digest": str(count)}],
            "candidates": [{"source_index": 0, "topic": "allowed uses", "excerpt": "internal evidence"}],
        }

    monkeypatch.setattr(zr, "discover", discover)

    async def run():
        reports = await asyncio.gather(*(cache.search("Revere", "GB", None) for _ in range(4)))
        assert count == 1
        assert all(r["lookup"] == "on demand" for r in reports)
        assert "candidates" not in reports[0]
        assert reports[0]["sources"][0]["topics"] == ["allowed uses"]
        revision = cache.queue()[0]["id"]
        assert cache.review(revision, "reviewed", "Reviewed dimensional chapter")
        assert cache.queue() == []
        assert (await cache.search("REVERE", " gb ", None))["lookup"] == "cache"
        monkeypatch.setattr(cache, "fresh", lambda _: False)
        await cache.search("Revere", "GB", None)
        assert count == 2
        assert not cache.review(revision, "rejected", "Outdated")
        assert cache.queue()[0]["id"] != revision

    asyncio.run(run())


def test_failed_refresh_preserves_reviewed_sources_and_records_status(tmp_path):
    cache = zr.ResearchCache(tmp_path / "research.sqlite")
    source = {"title": "Ordinance", "url": zr.WEBSITES["REVERE"], "digest": "initial"}
    original = cache.save("REVERE", "GB", {"sources": [source], "candidates": []})
    assert cache.review(original["id"], "reviewed", "Reviewed")
    refreshed = cache.save("REVERE", "GB", {"sources": [dict(source, digest=None)], "candidates": []})
    assert refreshed["id"] == original["id"]
    assert refreshed["decision"] == "reviewed"
    assert refreshed["refresh_failed"]
    assert "incomplete" in zr.public_report(refreshed, "cache")["status"]


def test_review_endpoint_requires_configured_secret(tmp_path, monkeypatch):
    cache = zr.ResearchCache(tmp_path / "research.sqlite")
    monkeypatch.setattr(zr, "cache", lambda: cache)
    client = TestClient(app)
    path = "/api/internal/zoning-review"
    monkeypatch.delenv("ZONING_REVIEW_TOKEN", raising=False)
    assert client.get(path).status_code == 404
    monkeypatch.setenv("ZONING_REVIEW_TOKEN", "test-review-token")
    assert client.get(path).status_code == 401
    auth = {"Authorization": "Bearer test-review-token"}
    assert client.get(path, headers=auth).json() == {"pending": []}


def test_unavailable_ordinance_retains_registered_link(monkeypatch):
    async def unavailable(*_):
        return None

    monkeypatch.setattr(zr, "fetch_document", unavailable)
    code = "https://ecode360.com/45390701"
    report = asyncio.run(zr.discover("REVERE", None, code))
    assert report["candidates"] == []
    assert report["sources"][0]["url"] == code
    assert report["sources"][0]["digest"] is None


def test_somerville_ordinance_uses_fetchable_district_sections():
    viewer = "https://online.encodeplus.com/regs/somerville-ma/doc-viewer.aspx"
    links = [{"url": viewer + "#secid-540"}, {"url": viewer + "#unexpected"},
             {"url": "https://example.org/regs/somerville-ma/doc-viewer.aspx#secid-540"}]
    urls = zr.somerville_section_urls(links)
    assert urls == ("https://online.encodeplus.com/regs/somerville-ma/doc-view.aspx?pn=0&ajax=0&secid=540",)
    html = b"<title>Somerville Zoning Ordinance</title><div role='main'><h4>Development standards</h4><p>Maximum building height depends on the applicable building type.</p></div>"
    doc = zr.parse_document(html, "text/html", urls[0], "SOMERVILLE")
    assert "Development standards" in doc["title"]
    assert zr.evidence([doc], "MR6")


def test_public_zoning_passages_are_grounded_and_scoped_to_the_mapped_district(tmp_path):
    cache = zr.ResearchCache(tmp_path / "research.sqlite")
    source = {"url": "https://www.chicopeema.gov/zoning", "title": "Zoning Ordinance", "digest": "ordinance"}
    earlier = "In the Residence A district, the minimum lot size is described in a separate schedule."
    matched = "In the Residence B district the minimum front setback is 20 feet, subject to other provisions."
    town_guidance = "Check the town’s zoning amendment register for the latest adopted amendments."
    payload = {
        "sources": [source],
        "candidates": [
            {"source_index": 0, "topic": "dimensional standards", "excerpt": earlier, "district_text_match": False},
            {"source_index": 0, "topic": "dimensional standards", "excerpt": matched, "district_text_match": True},
            {"source_index": 0, "topic": "allowed uses", "excerpt": matched, "district_text_match": True},
            {"source_index": 0, "topic": "overlay and amendments", "excerpt": town_guidance,
             "district_text_match": False},
        ],
        "rule_candidates": [
            {"excerpt_index": 1, "field": "setback", "source_text": "20 feet"},
            {"excerpt_index": 0, "field": "lot area", "source_text": "9000 sq ft"},
        ],
    }
    row = cache.save("CHICOPEE", "RESB", payload)
    report = zr.public_report(row, "on demand")
    assert report["passage_scope"] == "district_mentions"
    assert report["passages"] == [{
        "topic": "dimensional standards", "excerpt": matched, "source_url": source["url"],
        "source_title": source["title"],
    }]
    general = cache.save("CHICOPEE", "", payload)
    different_district = cache.save("CHICOPEE", "RESC", {
        **payload, "candidates": [dict(entry, district_text_match=False) for entry in payload["candidates"]],
    })
    for lookup in (general, different_district):
        report = zr.public_report(lookup, "on demand")
        assert report["passage_scope"] == "municipal_guidance"
        assert [p["excerpt"] for p in report["passages"]] == [town_guidance]
    assert not zr.public_report({**row, "refresh_failed": 1}, "cache")["passages"]
    assert not zr.public_report({**row, "decision": "rejected"}, "cache")["passages"]
