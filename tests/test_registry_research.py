import json

import httpx
from fastapi.testclient import TestClient

import app as server
import registry
import registry_research
import zoning_research


def test_reviewer_finds_cited_sources_without_confirming_deed(monkeypatch, tmp_path):
    cache = zoning_research.ResearchCache(tmp_path / "research.db")
    monkeypatch.setattr(server.zoning_research, "cache", lambda: cache)
    monkeypatch.setenv("ZONING_REVIEW_TOKEN", "review-token")
    address = "367 Main St Unit 2"
    evidence = registry.check("Barnstable", address, (123, 456))
    registry.enqueue(cache.connect, "Barnstable", [{"address": address, "deed_verification": evidence}])

    real_client = httpx.AsyncClient
    calls = []
    registry_url = "https://www.masslandrecords.com/Barnstable/record"
    listing_url = "https://www.zillow.com/homedetails/example/"
    municipal_url = "https://townofbarnstable.us/assessing/parcel"

    async def mock_api(request):
        assert str(request.url) == registry_research.RESPONSES
        calls.append(json.loads(request.content))
        urls = [listing_url, registry_url, municipal_url, listing_url,
                "https://zillow.com.example.net/sale", "https://user@zillow.com/sale", "http://zillow.com/sale"]
        return httpx.Response(200, json={"status": "completed", "output": [{
            "type": "message", "content": [{
                "type": "output_text", "text": "Unsupported price $1,234,567 and https://redfin.com/uncited",
                "annotations": [{"type": "url_citation", "url": url} for url in urls],
            }],
        }]})

    monkeypatch.setattr(server.httpx, "AsyncClient",
                        lambda: real_client(transport=httpx.MockTransport(mock_api)))
    monkeypatch.setitem(server.zoning_research.WEBSITES, "BARNSTABLE", "https://www.townofbarnstable.us/")
    client = TestClient(server.app)
    auth = {"Authorization": "Bearer review-token"}
    review_id = client.get("/api/internal/registry-review", headers=auth).json()["pending"][0]["id"]
    endpoint = f"/api/internal/registry-review/{review_id}/research"

    assert client.post(endpoint).status_code == 401
    assert client.post(f"/api/internal/registry-review/{review_id + 1}/research", headers=auth).status_code == 404
    monkeypatch.delenv("REGISTRY_RESEARCH_MODEL", raising=False)
    disabled = client.post(endpoint, headers=auth).json()
    assert disabled["status"] == "AI search disabled"
    assert disabled["registry_url"] == evidence["url"]
    assert not calls

    monkeypatch.setenv("OPENAI_API_KEY", "mock-api-key")
    monkeypatch.setenv("REGISTRY_RESEARCH_MODEL", "mock-model")
    response = client.post(endpoint, headers=auth)
    assert response.status_code == 200
    result = response.json()
    assert result["requires_registry_review"] is True
    assert result["sources"] == [
        {"url": registry_url, "kind": "Registry source"},
        {"url": municipal_url, "kind": "Public record lead"},
        {"url": listing_url, "kind": "Listing-site lead"},
    ]
    assert "1,234,567" not in response.text
    assert "uncited" not in response.text
    assert json.loads(calls[0]["input"]) == {
        "municipality": "BARNSTABLE", "address": address, "district": "Barnstable", "book": 123, "page": 456,
    }
    assert calls[0]["max_tool_calls"] == 2
    assert client.get("/api/internal/registry-review", headers=auth).json()["pending"][0]["deed_verification"] == evidence
    assert registry.check("Barnstable", address, (123, 456))["status"] == "pending"
