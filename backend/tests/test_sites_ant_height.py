"""
Default antenna height (AGL)
────────────────────────────
A site created without ant_height gets DEFAULT_ANT_HEIGHT_M (10 m) and is
flagged ant_height_is_default=True so the UI/report can mark it. Any
explicitly entered height (on create or update) clears the flag.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import sites as sites_api
from app.api.sites import DEFAULT_ANT_HEIGHT_M
from app.core.database import Base, get_db
from app.core.migrations import run_startup_migrations
from app.core.security import get_current_user
from app.main import app
from app.models.user import User, UserRole


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def client(engine, monkeypatch):
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = factory()
    db.add(User(id=1, username="admin", full_name="Admin", hashed_pw="x",
                role=UserRole.ADMIN, is_active=True))
    db.commit()
    db.close()

    def _get_db():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="admin", full_name="Admin", hashed_pw="x",
        role=UserRole.ADMIN, is_active=True)
    monkeypatch.setattr(sites_api, "fetch_elevation", lambda lat, lon: 100.0)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


BASE = dict(name="אתר", lat=32.0, lon=35.0, frequency=900)


def test_default_value_is_10m():
    assert DEFAULT_ANT_HEIGHT_M == 10.0


def test_create_without_height_uses_flagged_default(client):
    r = client.post("/api/sites/", json=BASE)
    assert r.status_code == 201, r.text
    site = r.json()
    assert site["ant_height"] == 10.0
    assert site["ant_height_is_default"] is True


def test_create_with_explicit_null_uses_default(client):
    r = client.post("/api/sites/", json={**BASE, "ant_height": None})
    assert r.status_code == 201, r.text
    assert r.json()["ant_height_is_default"] is True


def test_create_with_height_is_not_default(client):
    r = client.post("/api/sites/", json={**BASE, "ant_height": 25})
    site = r.json()
    assert site["ant_height"] == 25
    assert site["ant_height_is_default"] is False


def test_entering_10_explicitly_is_not_default(client):
    """The flag tracks *who set it*, not the number."""
    site = client.post("/api/sites/", json={**BASE, "ant_height": 10}).json()
    assert site["ant_height_is_default"] is False


def test_update_height_clears_flag(client):
    site = client.post("/api/sites/", json=BASE).json()
    r = client.put(f"/api/sites/{site['id']}", json={"ant_height": 30})
    assert r.status_code == 200, r.text
    assert r.json()["ant_height"] == 30
    assert r.json()["ant_height_is_default"] is False


def test_update_other_field_keeps_flag(client):
    site = client.post("/api/sites/", json=BASE).json()
    r = client.put(f"/api/sites/{site['id']}", json={"name": "שם חדש"})
    assert r.json()["ant_height_is_default"] is True
    assert r.json()["ant_height"] == 10.0


@pytest.mark.parametrize("bad", [0, 0.2, 500])
def test_height_out_of_range_rejected(client, bad):
    assert client.post("/api/sites/", json={**BASE, "ant_height": bad}).status_code == 422


def test_legacy_null_flag_reads_as_false(client, engine):
    site = client.post("/api/sites/", json={**BASE, "ant_height": 6}).json()
    with engine.begin() as c:
        c.execute(text("UPDATE sites SET ant_height_is_default = NULL WHERE id = :i"),
                  {"i": site["id"]})
    r = client.get(f"/api/sites/{site['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["ant_height_is_default"] is False


def test_migration_adds_column_to_old_table():
    eng = create_engine("sqlite://", poolclass=StaticPool)
    with eng.begin() as c:   # pre-feature schema: no ant_height_is_default
        c.execute(text("CREATE TABLE sites (id INTEGER PRIMARY KEY, name VARCHAR(128), "
                       "lat FLOAT, lon FLOAT, ant_height FLOAT, frequency INTEGER)"))
    run_startup_migrations(eng)
    with eng.connect() as c:
        cols = [row[1] for row in c.execute(text("PRAGMA table_info(sites)"))]
    assert "ant_height_is_default" in cols
