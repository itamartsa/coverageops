"""
API tests for /api/link (point-to-point link + best servers + Word report).

DB: in-memory SQLite. DEM: synthetic ElevationGrid (monkeypatched
fetch_elevation_grid_ex in app.api.link) with an east-west ridge.
"""
import io
import math
import time
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import link as link_api
from app.api import sites as sites_api
from app.api import coverage as cov_api
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.main import app
from app.models.cell_site import CellSite
from app.models.device_profile import seed_device_profiles, DeviceProfile
from app.models.link_result import LinkResult as LinkRow
from app.models.user import User, UserRole
from app.services.terrain import ElevationGrid

BASE = 100.0
RIDGE_LAT = 32.03


def ridge(lat, lon):
    return (BASE + 300.0 * np.exp(-((np.asarray(lat) - RIDGE_LAT) / 0.003) ** 2)).astype(np.float32)


def make_fetcher(fn=ridge, calls=None):
    def _fetch(sw_lat, sw_lon, ne_lat, ne_lon, out_width, out_height, max_tiles=None):
        if calls is not None:
            calls.append((out_width, out_height))
        lats = ne_lat - (np.arange(out_height) + 0.5) * (ne_lat - sw_lat) / out_height
        lons = sw_lon + (np.arange(out_width) + 0.5) * (ne_lon - sw_lon) / out_width
        lon_g, lat_g = np.meshgrid(lons, lats)
        elev = fn(lat_g, lon_g)
        return ElevationGrid(elev=elev, zoom=12, m_per_px=32.4,
                             valid_pct=float(np.isfinite(elev).mean() * 100), source="terrarium/z12")
    return _fetch


def _user(uid=1, role=UserRole.ADMIN):
    return User(id=uid, username=f"u{uid}", full_name="U", hashed_pw="x", role=role, is_active=True)


@pytest.fixture
def factory():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    f = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db = f()
    db.add(_user())
    db.add(_user(2, UserRole.OPERATOR))
    db.commit()
    seed_device_profiles(db)
    db.close()
    yield f
    eng.dispose()


@pytest.fixture
def setup(factory, monkeypatch):
    state = {"user": _user()}

    def _get_db():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    monkeypatch.setattr(link_api, "fetch_elevation_grid_ex", make_fetcher())
    monkeypatch.setattr(sites_api, "fetch_elevation", lambda lat, lon: BASE)
    monkeypatch.setattr(cov_api, "fetch_elevation", lambda lat, lon: BASE)
    try:
        yield TestClient(app), state
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(setup):
    return setup[0]


def add_cell(factory, **kw):
    d = dict(status="active", operator="PELEPHONE", operator_he="פלאפון", site_code="P1",
             city="חדרה", address="רחוב 1", site_type="ground_mast", site_type_he="תורן קרקעי",
             technologies="2G,4G", lat=32.0, lon=35.0, ant_height_m=25.0, height_source="estimated",
             tx_power_dbm=43.0, frequency_mhz=900, rf_source="estimated")
    d.update(kw)
    db = factory()
    try:
        c = CellSite(**d)
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def create_site(client, **kw):
    body = dict(name="אתר בדיקה", lat=32.0, lon=35.0, ant_height=20.0,
                frequency=900, tx_power=43.0, rx_threshold=-90.0, max_radius=30.0)
    body.update(kw)
    r = client.post("/api/sites/", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def link(client, tx, rx, mode="DTM", save=True, status=200):
    r = client.post("/api/link/", json=dict(transmitter=tx, receiver=rx, mode=mode, save=save))
    assert r.status_code == status, r.text
    return r.json()


RX_BEHIND_RIDGE = dict(lat=32.06, lon=35.0)
RX_CLEAR        = dict(lat=32.0, lon=35.03)


# ── POST / ────────────────────────────────────────────────────────────────────

def test_link_from_site_saves_and_returns_fields(client, factory):
    site = create_site(client)
    body = link(client, {"kind": "site", "id": site["id"]}, RX_BEHIND_RIDGE)
    for k in ("distance_km", "bearing_deg", "fspl_db", "diffraction_db", "rssi_dbm", "margin_db",
              "level", "is_los", "fresnel_clearance_pct", "worst_obstacle", "profile",
              "required_rx_height_m", "dem_source", "dem_valid_pct", "recommendations",
              "transmitter", "receiver", "warnings", "id", "created_at"):
        assert k in body, k
    assert body["id"] is not None and body["created_at"].endswith("Z")
    assert body["dem_source"] == "terrarium"
    assert abs(body["distance_km"] - 6.67) < 0.1
    assert body["bearing_deg"] < 1 or body["bearing_deg"] > 359
    assert body["is_los"] is False and body["diffraction_db"] > 0
    assert abs(body["worst_obstacle"]["lat"] - RIDGE_LAT) < 0.003
    assert len(body["profile"]) >= 81 and {"dist_km", "terrain_m", "los_m", "fresnel_m"} <= set(body["profile"][0])
    tx = body["transmitter"]
    assert tx["kind"] == "site" and tx["frequency_mhz"] == 900 and tx["params_source"] == "manual"
    assert tx["estimated_fields"] == []
    rx = body["receiver"]
    assert rx["profile_key"] == "handset" and rx["height_agl"] == 1.5 and rx["sensitivity_dbm"] == -105
    db = factory()
    row = db.query(LinkRow).one()
    assert row.tx_kind == "site" and row.is_los is False and row.rx_profile_key == "handset"
    db.close()


def test_link_from_cell_site_default_band_and_estimated(client, factory):
    cid = add_cell(factory, technologies="4G,5G", frequency_mhz=None, tx_power_dbm=None,
                   ant_height_m=None)
    body = link(client, {"kind": "cell_site", "id": cid}, RX_CLEAR)
    tx = body["transmitter"]
    assert tx["frequency_mhz"] == 1800                 # lowest of 4G 1800 / 5G 3500
    assert tx["ant_height_m"] == 15.0 and tx["tx_power_dbm"] == 40.0
    assert tx["params_source"] == "estimated"
    assert set(tx["estimated_fields"]) == {"ant_height_m", "tx_power_dbm", "frequency_mhz"}
    assert tx["bands"] == [1800, 3500]
    assert any("משוער" in w for w in body["warnings"])
    assert body["tx_ground_asl"] == pytest.approx(BASE, abs=1)


def test_cell_site_manual_rf_and_explicit_freq(client, factory):
    cid = add_cell(factory, rf_source="manual", tx_power_source="manual", frequency_source="manual",
                   frequency_mhz=2100, tx_power_dbm=45.0,
                   height_source="manual", ant_height_m=30.0)
    tx = link(client, {"kind": "cell_site", "id": cid}, RX_CLEAR)["transmitter"]
    assert tx["frequency_mhz"] == 2100 and tx["params_source"] == "manual"
    tx = link(client, {"kind": "cell_site", "id": cid, "frequency_mhz": 700}, RX_CLEAR)["transmitter"]
    assert tx["frequency_mhz"] == 700
    link(client, {"kind": "cell_site", "id": cid, "frequency_mhz": 7000}, RX_CLEAR, status=422)


def test_explicit_receiver_overrides_profile(client, factory):
    db = factory()
    cpe = db.query(DeviceProfile).filter(DeviceProfile.key == "cpe").one().id
    db.close()
    site = create_site(client)
    body = link(client, {"kind": "site", "id": site["id"]},
                dict(RX_CLEAR, device_profile_id=cpe, height_agl=12.0, name="נקודה"))
    rx = body["receiver"]
    assert rx["profile_key"] == "cpe" and rx["height_agl"] == 12.0
    assert rx["sensitivity_dbm"] == -110.0 and rx["antenna_gain_dbi"] == 9.0
    assert rx["name"] == "נקודה"
    assert body["rx_gain_db"] == 9.0
    link(client, {"kind": "site", "id": site["id"]}, dict(RX_CLEAR, device_profile_id=999), status=404)


def test_viewer_forbidden(setup):
    client, state = setup
    site = create_site(client)
    state["user"] = _user(3, UserRole.VIEWER)
    link(client, {"kind": "site", "id": site["id"]}, RX_CLEAR, status=403)
    r = client.post("/api/link/best-servers", json={"receiver": RX_CLEAR})
    assert r.status_code == 403
    assert client.get("/api/link/").status_code == 200


def test_too_far_400(client):
    site = create_site(client)
    link(client, {"kind": "site", "id": site["id"]}, dict(lat=33.5, lon=35.0), status=400)


def test_dem_too_large_413(client, monkeypatch):
    from app.services.terrain import DemAreaTooLarge

    def boom(*a, **k):
        raise DemAreaTooLarge("x", tiles=1, max_tiles=0, zoom=8, pixels=0, max_pixels=0)
    monkeypatch.setattr(link_api, "fetch_elevation_grid_ex", boom)
    site = create_site(client)
    link(client, {"kind": "site", "id": site["id"]}, RX_CLEAR, status=413)


def test_no_dem_warns(client, factory, monkeypatch):
    monkeypatch.setattr(link_api, "fetch_elevation_grid_ex", lambda *a, **k: None)
    cid = add_cell(factory)
    body = link(client, {"kind": "cell_site", "id": cid}, RX_CLEAR)
    assert body["dem_source"] == "none"
    assert any("טופוגרפיה" in w for w in body["warnings"])
    assert any("גובה הקרקע במיקום המשדר" in w for w in body["warnings"])


def test_save_false_no_row(client, factory):
    site = create_site(client)
    body = link(client, {"kind": "site", "id": site["id"]}, RX_CLEAR, save=False)
    assert body["id"] is None
    db = factory()
    assert db.query(LinkRow).count() == 0
    db.close()


def test_missing_transmitter_404(client):
    link(client, {"kind": "site", "id": 999}, RX_CLEAR, status=404)
    link(client, {"kind": "cell_site", "id": 999}, RX_CLEAR, status=404)


# ── list / get / delete / docx ────────────────────────────────────────────────

def test_list_get_delete(setup):
    client, state = setup
    site = create_site(client)
    a = link(client, {"kind": "site", "id": site["id"]}, RX_CLEAR)
    b = link(client, {"kind": "site", "id": site["id"]}, RX_BEHIND_RIDGE, mode="DSM")
    lst = client.get("/api/link/").json()
    assert [x["id"] for x in lst] == [b["id"], a["id"]]
    assert "result_json" not in lst[0] and "profile" not in lst[0]
    assert lst[0]["mode"] == "DSM" and lst[0]["tx_name"] == "אתר בדיקה"
    full = client.get(f"/api/link/{a['id']}").json()
    assert full == a
    assert client.get("/api/link/999").status_code == 404

    # operator (not owner) cannot delete admin's result
    state["user"] = _user(2, UserRole.OPERATOR)
    assert client.delete(f"/api/link/{a['id']}").status_code == 403
    state["user"] = _user()
    assert client.delete(f"/api/link/{a['id']}").status_code == 204
    assert client.get(f"/api/link/{a['id']}").status_code == 404
    assert [x["id"] for x in client.get("/api/link/").json()] == [b["id"]]


def test_docx_is_valid_zip(client):
    site = create_site(client, name="אתר עברי")
    body = link(client, {"kind": "site", "id": site["id"]}, RX_BEHIND_RIDGE)
    r = client.get(f"/api/link/{body['id']}/docx")
    assert r.status_code == 200
    assert zipfile.is_zipfile(io.BytesIO(r.content))
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert any(n.startswith("word/media/") and n.endswith(".png") for n in z.namelist())
    cd = r.headers["content-disposition"]
    assert "filename=" in cd and "filename*=UTF-8''" in cd


# ── best servers ──────────────────────────────────────────────────────────────

def test_best_servers_rank_filter_radius(client, factory):
    near   = add_cell(factory, lat=32.0, lon=35.01, operator="PHI", site_code="N")       # ~0.95 km
    far    = add_cell(factory, lat=32.0, lon=35.06, operator="PELEPHONE", site_code="F")  # ~5.7 km
    behind = add_cell(factory, lat=32.06, lon=35.0, operator="CELLCOM", site_code="B")  # behind ridge
    indoor = add_cell(factory, lat=32.0, lon=35.005, site_type="small_indoor", site_code="I")
    out    = add_cell(factory, lat=32.0, lon=35.2, site_code="O")                        # ~19 km
    constr = add_cell(factory, lat=32.0, lon=35.004, status="construction", site_code="C")

    r = client.post("/api/link/best-servers",
                    json={"receiver": {"lat": 32.0, "lon": 35.0}, "radius_km": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [x["cell_site"]["id"] for x in body["results"]]
    assert body["candidates_evaluated"] == 3
    assert set(ids) == {near, far, behind}
    assert ids[0] == near
    rssis = [x["link"]["rssi_dbm"] for x in body["results"]]
    assert rssis == sorted(rssis, reverse=True)
    assert [x["rank"] for x in body["results"]] == [1, 2, 3]
    assert indoor not in ids and out not in ids and constr not in ids
    first = body["results"][0]
    assert {"operator_he", "site_type_he", "city", "address", "height_source",
            "rf_source", "bands", "frequency_mhz"} <= set(first["cell_site"])
    assert {"distance_km", "bearing_deg", "margin_db", "level", "is_los",
            "diffraction_db", "fresnel_clearance_pct"} <= set(first["link"])
    assert body["dem_source"] == "terrarium"

    r = client.post("/api/link/best-servers",
                    json={"receiver": {"lat": 32.0, "lon": 35.0}, "radius_km": 10,
                          "operator": "PELEPHONE"}).json()
    assert [x["cell_site"]["id"] for x in r["results"]] == [far]

    r = client.post("/api/link/best-servers",
                    json={"receiver": {"lat": 32.0, "lon": 35.0}, "radius_km": 25, "top_n": 2,
                          "statuses": ["active", "construction"]}).json()
    assert r["candidates_evaluated"] == 5 and len(r["results"]) == 2

    r = client.post("/api/link/best-servers",
                    json={"receiver": {"lat": 32.0, "lon": 35.0}, "radius_km": 10,
                          "band_policy": "highest"}).json()
    assert all(x["cell_site"]["frequency_mhz"] == 1800 for x in r["results"])

    assert client.post("/api/link/best-servers",
                       json={"receiver": {"lat": 32.0, "lon": 35.0}, "radius_km": 40}).status_code == 422


def test_best_servers_empty(client):
    r = client.post("/api/link/best-servers", json={"receiver": {"lat": 31.0, "lon": 34.9}}).json()
    assert r["results"] == [] and r["candidates_evaluated"] == 0 and r["warnings"]


def test_best_servers_performance(client, factory, monkeypatch):
    calls = []
    monkeypatch.setattr(link_api, "fetch_elevation_grid_ex", make_fetcher(calls=calls))
    rng = np.random.default_rng(7)
    db = factory()
    for i in range(100):
        a = rng.uniform(0, 2 * math.pi)
        d = rng.uniform(0.5, 9.5) / 111.32
        db.add(CellSite(status="active", operator="PHI", site_type="ground_mast", technologies="2G,4G",
                        lat=32.0 + d * math.sin(a), lon=35.0 + d * math.cos(a) / math.cos(math.radians(32)),
                        ant_height_m=25.0, tx_power_dbm=43.0))
    db.commit()
    db.close()
    t0 = time.time()
    r = client.post("/api/link/best-servers",
                    json={"receiver": {"lat": 32.0, "lon": 35.0, "sensitivity_dbm": -50},
                          "radius_km": 10, "top_n": 10})
    dt = time.time() - t0
    assert r.status_code == 200, r.text
    assert r.json()["candidates_evaluated"] == 100
    assert len(calls) == 1 and max(calls[0]) <= 500
    assert dt < 5.0, f"best-servers took {dt:.2f}s"
