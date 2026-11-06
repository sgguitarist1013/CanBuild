from unittest.mock import patch
import httpx
import pytest
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


def test_health_and_home():
    assert client.get("/api/health").json()["ok"] is True
    assert "Property address" in client.get("/").text


def test_invalid_input():
    assert client.get("/api/property", params={"address": "x"}).status_code == 422
    assert client.get("/api/suggest", params={"q": "x"}).status_code == 422


@pytest.mark.parametrize("query,expected", [
    ("95 Mag", ["95 Magazine St, Cambridge, MA"]),
    ("95", ["95 Magazine St, Cambridge, MA", "95 Main St, Cambridge, MA"]),
])
def test_suggestions_never_substitute_neighbor(query, expected, monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    payload = {"suggestions": [{"text": x} for x in [
        "91 Magazine St, Cambridge, MA", "95 Magazine St, Cambridge, MA",
        "195 Magazine St, Cambridge, MA", "95 Main St, Cambridge, MA"]]}
    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs):
            return httpx.Response(200, json=payload, request=httpx.Request("GET", "https://example.com"))
    with patch("app.httpx.AsyncClient", FakeClient):
        result = client.get("/api/suggest", params={"q": query})
    assert result.status_code == 200
    assert result.json()["suggestions"] == expected


def test_monitoring_scrubs_search_data():
    from monitoring import scrub_event
    assert scrub_event({"request": {"query_string": "address=private"}, "user": {},
                        "breadcrumbs": [], "exception": {"values": []}}, {}) == {"exception": {"values": []}}
