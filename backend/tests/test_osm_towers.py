"""
API tests for /api/cell-sites/osm (OpenStreetMap towers via Overpass, proxied + cached).

Run with: pytest tests/test_osm_towers.py -v

No network: urllib.request.urlopen is monkeypatched in the osm_towers module;
get_current_user is overridden with a fake ADMIN user.
"""
import io
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app.api import osm_towers
from app.core.security import get_current_user
from app.main import app
from app.models.user import User, UserRole

BBOX = {"south": 32.05, "west": 34.75, "north": 32.15, "east": 34.85}   # Tel Aviv

OVERPASS_PAYLOAD = {
    "elements": [
        {"type": "node", "id": 1, "lat": 32.10, "lon": 34.80,
         "tags": {"tower:type": "communication", "operator": "Cellcom", "height": "30",
                  "irrelevant": "x"}},
        {"type": "node", "id": 2, "lat": 32.12, "lon": 34.82,
         "tags": {"man_made": "mast", "communication:mobile_phone": "yes", "operator:he": "פרטנר"}},
        # inside the snapped bbox (32.0–32.2) but outside the requested one → filtered out
        {"type": "node", "id": 3, "lat": 32.18, "lon": 34.80, "tags": {"tower:type": "communication"}},
        {"type": "way", "id": 4, "tags": {}},   # no coordinates → skipped
    ]
}


class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self._body, self.status = body, status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(b""))


class FakeUrlopen:
    """Plays a script of outcomes (one per call): dict → JSON body, int → HTTPError, bytes → raw body."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append((req.full_url, timeout, req.get_header("User-agent"), req.data))
        outcome = self.script.pop(0)
        if isinstance(outcome, int):
            raise _http_error(req.full_url, outcome)
        if isinstance(outcome, bytes):
            return _Resp(outcome)
        return _Resp(json.dumps(outcome).encode())


@pytest.fixture
def client():
    def _user():
        return User(id=1, username="admin", full_name="Admin", hashed_pw="x",
                    role=UserRole.ADMIN, is_active=True)

    app.dependency_overrides[get_current_user] = _user
    osm_towers.clear_cache()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        osm_towers.clear_cache()


def _install(monkeypatch, fake):
    monkeypatch.setattr(osm_towers.urllib.request, "urlopen", fake)
    return fake


def test_success(client, monkeypatch):
    fake = _install(monkeypatch, FakeUrlopen(OVERPASS_PAYLOAD))
    r = client.get("/api/cell-sites/osm", params=BBOX)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["count"] == 2 and [e["id"] for e in d["elements"]] == [1, 2]
    assert d["source"] == "overpass-api.de" and d["cached"] is False
    assert d["bbox"] == {"south": 32.0, "west": 34.7, "north": 32.2, "east": 34.9}
    e1, e2 = d["elements"]
    assert e1["operator"] == "Cellcom" and e1["height"] == "30" and "irrelevant" not in e1["tags"]
    assert e2["operator"] == "פרטנר" and e2["height"] is None
    url, timeout, ua, body = fake.calls[0]
    assert url == osm_towers.OVERPASS_MIRRORS[0] and timeout == 45 and ua == "CoverageOps/1.0"
    assert b"timeout%3A40" in body and b"32.0%2C34.7%2C32.2%2C34.9" in body


def test_mirror_fallback_after_504(client, monkeypatch):
    fake = _install(monkeypatch, FakeUrlopen(504, OVERPASS_PAYLOAD))
    r = client.get("/api/cell-sites/osm", params=BBOX)
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "overpass.kumi.systems"
    assert [c[0] for c in fake.calls] == osm_towers.OVERPASS_MIRRORS[:2]


def test_non_json_falls_through(client, monkeypatch):
    _install(monkeypatch, FakeUrlopen(b"<html>busy</html>", 429, OVERPASS_PAYLOAD))
    r = client.get("/api/cell-sites/osm", params=BBOX)
    assert r.status_code == 200 and r.json()["source"] == "overpass.private.coffee"


def test_cache_hit(client, monkeypatch):
    fake = _install(monkeypatch, FakeUrlopen(OVERPASS_PAYLOAD))
    assert client.get("/api/cell-sites/osm", params=BBOX).json()["cached"] is False
    # slightly different viewport, same snapped grid cell → served from cache
    r = client.get("/api/cell-sites/osm", params={**BBOX, "north": 32.13})
    assert r.status_code == 200
    d = r.json()
    assert d["cached"] is True and [e["id"] for e in d["elements"]] == [1, 2]
    assert len(fake.calls) == 1


def test_bbox_too_large(client, monkeypatch):
    fake = _install(monkeypatch, FakeUrlopen())
    r = client.get("/api/cell-sites/osm",
                   params={"south": 29.5, "west": 34.2, "north": 33.3, "east": 35.9})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("התקרב למפה")
    assert fake.calls == []


def test_all_mirrors_fail(client, monkeypatch):
    _install(monkeypatch, FakeUrlopen(504, 429, 502))
    r = client.get("/api/cell-sites/osm", params=BBOX)
    assert r.status_code == 503
    assert r.json()["detail"] == "שרתי OpenStreetMap (Overpass) עמוסים כרגע – נסה שוב בעוד דקה"
    # failures are not cached: next call queries again
    _install(monkeypatch, FakeUrlopen(OVERPASS_PAYLOAD))
    assert client.get("/api/cell-sites/osm", params=BBOX).status_code == 200
