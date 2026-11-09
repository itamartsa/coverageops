"""Receiver (device) profiles — seeding + /api/device-profiles CRUD."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.main import app
from app.models.device_profile import DEFAULT_DEVICE_PROFILES, DeviceProfile, seed_device_profiles
from app.models.user import User, UserRole


@pytest.fixture
def factory():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    f = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db = f()
    seed_device_profiles(db)
    db.close()
    yield f
    eng.dispose()


def _client(factory, role):
    def _get_db():
        s = factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username=role.value.lower(), full_name="U", hashed_pw="x", role=role, is_active=True)
    return TestClient(app)


@pytest.fixture
def admin(factory):
    yield _client(factory, UserRole.ADMIN)
    app.dependency_overrides.clear()


@pytest.fixture
def viewer(factory):
    yield _client(factory, UserRole.VIEWER)
    app.dependency_overrides.clear()


def _by_key(client):
    return {p["key"]: p for p in client.get("/api/device-profiles/").json()}


def test_seed_values_and_only_into_empty_table(factory):
    db = factory()
    db.query(DeviceProfile).filter_by(key="vehicle").update({"rx_sensitivity_dbm": -100.0})
    db.query(DeviceProfile).filter_by(key="handheld_radio").delete()
    db.commit()
    seed_device_profiles(db)
    rows = {p.key: p for p in db.query(DeviceProfile).all()}
    assert set(rows) == {"handset", "vehicle", "cpe"}                  # deleted one not re-added
    assert set(DEFAULT_DEVICE_PROFILES) == {"handset", "vehicle", "cpe", "handheld_radio"}
    assert rows["vehicle"].rx_sensitivity_dbm == -100.0               # admin edit kept
    h = rows["handset"]
    assert (h.name_he, h.rx_height_m, h.rx_sensitivity_dbm, h.antenna_gain_dbi, h.is_default) == \
        ("טלפון נייד", 1.5, -105.0, 0.0, True)
    assert (rows["cpe"].rx_height_m, rows["cpe"].rx_sensitivity_dbm, rows["cpe"].antenna_gain_dbi) == (6.0, -110.0, 9.0)
    assert sum(p.is_default for p in rows.values()) == 1
    db.close()


def test_list_any_user(viewer):
    r = viewer.get("/api/device-profiles/")
    assert r.status_code == 200
    lst = r.json()
    assert lst[0]["key"] == "handset" and lst[0]["is_default"] is True
    assert set(lst[0]) == {"id", "key", "name_he", "rx_height_m", "rx_sensitivity_dbm",
                           "antenna_gain_dbi", "description_he", "is_default", "created_at"}


def test_list_seeds_empty_table():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    f = sessionmaker(bind=eng)
    try:
        c = _client(f, UserRole.VIEWER)
        assert len(c.get("/api/device-profiles/").json()) == 4
    finally:
        app.dependency_overrides.clear()
        eng.dispose()


def test_viewer_and_operator_cannot_write(factory):
    try:
        for role in (UserRole.VIEWER, UserRole.OPERATOR):
            c = _client(factory, role)
            pid = _by_key(c)["cpe"]["id"]
            body = {"key": "x", "name_he": "x", "rx_height_m": 1, "rx_sensitivity_dbm": -100}
            assert c.post("/api/device-profiles/", json=body).status_code == 403
            assert c.put(f"/api/device-profiles/{pid}", json={"rx_height_m": 3}).status_code == 403
            assert c.delete(f"/api/device-profiles/{pid}").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_crud(admin):
    body = {"key": "drone", "name_he": "רחפן", "rx_height_m": 120, "rx_sensitivity_dbm": -100,
            "antenna_gain_dbi": 2, "description_he": "מודם על רחפן"}
    r = admin.post("/api/device-profiles/", json=body)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["key"] == "drone" and p["is_default"] is False and p["rx_height_m"] == 120
    assert admin.post("/api/device-profiles/", json=body).status_code == 409
    assert admin.post("/api/device-profiles/", json={**body, "key": "Bad Key"}).status_code == 422
    assert admin.post("/api/device-profiles/", json={**body, "key": "d2", "rx_sensitivity_dbm": 0}).status_code == 422

    r = admin.put(f"/api/device-profiles/{p['id']}", json={"rx_height_m": 80, "name_he": "רחפן נמוך"})
    assert r.status_code == 200 and r.json()["rx_height_m"] == 80 and r.json()["name_he"] == "רחפן נמוך"
    assert admin.put(f"/api/device-profiles/{p['id']}", json={"key": "cpe"}).status_code == 409
    assert admin.put("/api/device-profiles/9999", json={"rx_height_m": 1}).status_code == 404

    assert admin.delete(f"/api/device-profiles/{p['id']}").status_code == 204
    assert "drone" not in _by_key(admin)
    assert admin.delete(f"/api/device-profiles/{p['id']}").status_code == 404


def test_default_is_exclusive_and_protected(admin):
    ps = _by_key(admin)
    handset, cpe = ps["handset"]["id"], ps["cpe"]["id"]
    assert admin.delete(f"/api/device-profiles/{handset}").status_code == 400
    assert admin.put(f"/api/device-profiles/{handset}", json={"is_default": False}).status_code == 400

    r = admin.put(f"/api/device-profiles/{cpe}", json={"is_default": True})
    assert r.status_code == 200 and r.json()["is_default"] is True
    ps = _by_key(admin)
    assert [k for k, p in ps.items() if p["is_default"]] == ["cpe"]
    assert admin.delete(f"/api/device-profiles/{handset}").status_code == 204   # no longer default

    r = admin.post("/api/device-profiles/", json={"key": "new", "name_he": "חדש", "rx_height_m": 2,
                                                  "rx_sensitivity_dbm": -100, "is_default": True})
    assert r.status_code == 201 and r.json()["is_default"] is True
    assert [k for k, p in _by_key(admin).items() if p["is_default"]] == ["new"]
