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
