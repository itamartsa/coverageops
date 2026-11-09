"""Civil cellular sites layer — ITM conversion + record normalization."""
from app.services.itm import itm_to_wgs84
from app.services.cell_sites_import import _to_site, _techs, _date, _operator


def test_itm_known_points():
    # Jerusalem (Ramot) & Netanya — reference values from PROJ (EPSG:2039 -> 4326), within ~5 m
    lat, lon = itm_to_wgs84(217831, 630534)
    assert abs(lat - 31.76747) < 5e-5 and abs(lon - 35.18728) < 5e-5
    lat, lon = itm_to_wgs84(185808, 687241)
    assert abs(lat - 32.27835) < 5e-5 and abs(lon - 34.84727) < 5e-5


def test_normalizers():
    assert _techs("דור  2 3 4 5") == "2G,3G,4G,5G"
    assert _date("05/12/2021") == "2021-12-05"
    assert _date("2025-12-03 00:00:00") == "2025-12-03"
    assert _operator("PHI (משרת את הוט ופרטנר)") == "PHI"
    assert _operator("פלאפון") == "PELEPHONE"


def test_to_site_active_and_bad_coords():
    rec = {"ID": 1, "חברה": "סלקום", "מס' אתר": "123", "עיר": "חיפה", "X_ITM": 200000, "Y_ITM": 745000,
           "סוג אתר": "תורן קרקעי", "טכנולוגיית שידור": "דור 4 5", "תאריך היתר הפעלה": "01/02/2024"}
    s = _to_site(rec, "active")
    assert s.operator == "CELLCOM" and s.site_type == "ground_mast" and s.technologies == "4G,5G"
    assert 32.7 < s.lat < 32.9 and s.operation_permit_date == "2024-02-01"
    assert _to_site({**rec, "X_ITM": 0}, "active") is None
    assert _to_site({**rec, "X_ITM": 900000}, "active") is None


# ── Estimated / manual antenna heights ───────────────────────────────────────
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.main import app
from app.models.cell_site import CellSite, CellSiteTypeDefault
from app.models.user import User, UserRole
from app.services.cell_sites_import import (
    HEIGHT_DEFAULTS, backfill_estimated_heights, estimated_height_for, import_data, seed_type_defaults,
)


def _dataset(n=1000):
    """n active ground masts + 1 rooftop + 1 construction site (import needs >=1000 active)."""
    active = [{"ID": i, "חברה": "סלקום", "מס' אתר": str(i), "עיר": "חיפה",
               "X_ITM": 200000 + i, "Y_ITM": 745000, "סוג אתר": "תורן קרקעי"} for i in range(n)]
    active.append({"ID": 99999, "חברה": "פלאפון", "מס' אתר": "R1", "עיר": "חיפה",
                   "X_ITM": 201000, "Y_ITM": 746000, "סוג אתר": "תורן על הגג"})
    construction = [{"ID": 5, "חברה": "פלאפון", "מספר האתר": "C1", "ישוב": "חדרה",
                     "X_ITM": 190000, "Y_ITM": 705000, "סוג המוקד": "אתר זעיר פנימי"}]
    return {"source": "snapshot", "fetched_at": "2026-01-01", "active": active, "construction": construction}


@pytest.fixture
def factory():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    f = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db = f()
    seed_type_defaults(db)
    import_data(db, _dataset())
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


def _site(factory, code):
    db = factory()
    try:
        return db.query(CellSite).filter(CellSite.site_code == code).one()
    finally:
        db.close()


def test_estimated_height_helper(factory):
    db = factory()
    assert estimated_height_for("ground_mast", db) == 25.0
    assert estimated_height_for("small_indoor", db) == 3.0
    assert estimated_height_for("nonexistent", db) == HEIGHT_DEFAULTS["other"][0]
    assert estimated_height_for(None) == 15.0
    db.close()


def test_estimate_assigned_on_import(factory):
    g, r, c = _site(factory, "7"), _site(factory, "R1"), _site(factory, "C1")
    assert (g.ant_height_m, g.height_source) == (25.0, "estimated")
    assert (r.ant_height_m, r.height_source) == (18.0, "estimated")
    assert (c.ant_height_m, c.height_source) == (3.0, "estimated")


def test_seed_is_idempotent_and_keeps_admin_edits(factory):
    db = factory()
    db.query(CellSiteTypeDefault).filter_by(site_type="sting").update({"default_height_m": 12.0})
    db.commit()
    seed_type_defaults(db)
    assert db.query(CellSiteTypeDefault).count() == len(HEIGHT_DEFAULTS)
    assert db.query(CellSiteTypeDefault).get("sting").default_height_m == 12.0
    db.close()


def test_backfill_fills_null_heights(factory):
    db = factory()
    db.query(CellSite).update({CellSite.ant_height_m: None})
    db.commit()
    assert backfill_estimated_heights(db) > 1000
    assert db.query(CellSite).filter(CellSite.ant_height_m.is_(None)).count() == 0
    db.close()
    assert _site(factory, "R1").ant_height_m == 18.0


def test_patch_sets_manual_and_survives_reimport(admin, factory):
    sid = _site(factory, "7").id
    r = admin.patch(f"/api/cell-sites/{sid}/height", json={"ant_height_m": 32, "note": "מדידה בשטח"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ant_height_m"] == 32 and d["height_source"] == "manual"
    assert d["height_note"] == "מדידה בשטח" and d["height_updated_by"] == "admin"
    assert d["height_updated_at"].endswith("Z")

    db = factory()
    import_data(db, _dataset())
    db.close()
    s = _site(factory, "7")
    assert (s.ant_height_m, s.height_source, s.height_note) == (32, "manual", "מדידה בשטח")
    assert _site(factory, "8").height_source == "estimated"


def test_patch_validates_range(admin, factory):
    sid = _site(factory, "7").id
    assert admin.patch(f"/api/cell-sites/{sid}/height", json={"ant_height_m": 0.1}).status_code == 422
    assert admin.patch(f"/api/cell-sites/{sid}/height", json={"ant_height_m": 301}).status_code == 422
    assert admin.patch("/api/cell-sites/999999/height", json={"ant_height_m": 20}).status_code == 404


def test_delete_reverts_to_estimate(admin, factory):
    sid = _site(factory, "R1").id
    admin.patch(f"/api/cell-sites/{sid}/height", json={"ant_height_m": 40})
    r = admin.delete(f"/api/cell-sites/{sid}/height")
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["ant_height_m"], d["height_source"], d["height_note"]) == (18.0, "estimated", None)


def test_put_default_updates_only_estimated_rows(admin, factory):
    manual_id = _site(factory, "1").id
    admin.patch(f"/api/cell-sites/{manual_id}/height", json={"ant_height_m": 33})
    r = admin.put("/api/cell-sites/type-defaults/ground_mast", json={"default_height_m": 28})
    assert r.status_code == 200, r.text
    assert r.json()["updated_sites"] == 999
    assert _site(factory, "2").ant_height_m == 28
    assert _site(factory, "1").ant_height_m == 33
    assert _site(factory, "R1").ant_height_m == 18.0       # other type untouched
    defaults = {d["site_type"]: d for d in admin.get("/api/cell-sites/type-defaults").json()}
    assert defaults["ground_mast"]["default_height_m"] == 28
    assert defaults["ground_mast"]["label_he"] == "תורן קרקעי"
    assert admin.put("/api/cell-sites/type-defaults/nope", json={"default_height_m": 5}).status_code == 404


def test_meta_and_list_include_heights(admin, factory):
    sid = _site(factory, "3").id
    admin.patch(f"/api/cell-sites/{sid}/height", json={"ant_height_m": 30})
    he = admin.get("/api/cell-sites/meta").json()["height_estimation"]
    assert he["manual_count"] == 1 and he["estimated_count"] == 1001
    assert "משוערים" in he["note_he"] and len(he["defaults"]) == len(HEIGHT_DEFAULTS)
    lst = admin.get("/api/cell-sites/").json()
    assert lst["fields"][7:11] == ["ant_height_m", "height_source", "height_note", "height_updated_at"]
    row = next(r for r in lst["rows"] if r[0] == sid)
    assert row[7:9] == [30, "manual"] and row[10].endswith("Z")
    det = admin.get(f"/api/cell-sites/{sid}").json()
    assert det["height_source"] == "manual" and det["ant_height_m"] == 30


def test_viewer_cannot_edit_heights(viewer, factory):
    sid = _site(factory, "7").id
    assert viewer.patch(f"/api/cell-sites/{sid}/height", json={"ant_height_m": 20}).status_code == 403
    assert viewer.delete(f"/api/cell-sites/{sid}/height").status_code == 403
    assert viewer.put("/api/cell-sites/type-defaults/ground_mast",
                      json={"default_height_m": 20}).status_code == 403
    assert viewer.get("/api/cell-sites/type-defaults").status_code == 200


def test_operator_can_edit_height_but_not_defaults(factory):
    c = _client(factory, UserRole.OPERATOR)
    try:
        sid = _site(factory, "7").id
        assert c.patch(f"/api/cell-sites/{sid}/height", json={"ant_height_m": 20}).status_code == 200
        assert c.put("/api/cell-sites/type-defaults/ground_mast",
                     json={"default_height_m": 20}).status_code == 403
    finally:
        app.dependency_overrides.clear()


# ── Estimated / manual RF (EIRP + frequency) ─────────────────────────────────
from app.api import sites as sites_api
from app.models.cell_site import CellSiteBandDefault
from app.models.user import Site
from app.services.cell_sites_import import (
    BAND_DEFAULTS, TX_POWER_DEFAULTS, backfill_estimated_rf, bands_for, default_band,
    estimated_tx_power_for,
)


def _dataset_rf(n=1000):
    """Like _dataset, with technologies: ground masts 4G+5G, rooftop 2G..5G, sting 3G."""
    d = _dataset(n)
    for r in d["active"]:
        r["טכנולוגיית שידור"] = "דור 4 5"
    d["active"][-1]["טכנולוגיית שידור"] = "דור 2 3 4 5"
    d["active"].append({"ID": 88888, "חברה": "סלקום", "מס' אתר": "S1", "עיר": "עכו",
                        "X_ITM": 202000, "Y_ITM": 747000, "סוג אתר": "אנטנת עוקץ",
                        "טכנולוגיית שידור": "דור 3"})
    return d


@pytest.fixture
def rf_factory(factory):
    db = factory()
    import_data(db, _dataset_rf())
    db.close()
    return factory


@pytest.fixture
def rf_admin(rf_factory):
    yield _client(rf_factory, UserRole.ADMIN)
    app.dependency_overrides.clear()


def test_bands_parsing_and_default_band():
    assert bands_for("2G,3G,4G,5G") == [900, 1800, 2100, 3500]
    assert bands_for("5G,4G,4G") == [1800, 3500]
    assert bands_for(" 3g ") == [2100]
    assert bands_for("") == [] and bands_for(None) == [] and bands_for("6G") == []
    assert default_band("4G,5G") == 1800
    assert default_band("2G,3G") == 900
    assert default_band("3G") == 2100
    assert default_band("") == 900 and default_band(None) == 900
    assert bands_for("4G", _cache={"4G": 2600}) == [2600]
    assert estimated_tx_power_for("small_indoor") == 20.0
    assert estimated_tx_power_for(None) == TX_POWER_DEFAULTS["other"] == 40.0


def test_seed_band_and_tx_defaults(factory):
    db = factory()
    assert {r.generation: r.frequency_mhz for r in db.query(CellSiteBandDefault).all()} == \
        {"2G": 900, "3G": 2100, "4G": 1800, "5G": 3500}
    assert {r.site_type: r.default_tx_power_dbm for r in db.query(CellSiteTypeDefault).all()} == TX_POWER_DEFAULTS
    # NULL tx power (column just added) is filled, admin edits kept
    db.query(CellSiteTypeDefault).filter_by(site_type="sting").update({"default_tx_power_dbm": None})
    db.query(CellSiteTypeDefault).filter_by(site_type="tilted").update({"default_tx_power_dbm": 37.0})
    db.query(CellSiteBandDefault).filter_by(generation="4G").update({"frequency_mhz": 2600})
    db.commit()
    seed_type_defaults(db)
    assert db.query(CellSiteTypeDefault).get("sting").default_tx_power_dbm == 40.0
    assert db.query(CellSiteTypeDefault).get("tilted").default_tx_power_dbm == 37.0
    assert db.query(CellSiteBandDefault).get("4G").frequency_mhz == 2600
    assert db.query(CellSiteBandDefault).count() == 4
    db.close()


def test_estimated_rf_on_import(rf_factory):
    g, r, st, c = (_site(rf_factory, k) for k in ("7", "R1", "S1", "C1"))
    assert (g.tx_power_dbm, g.frequency_mhz, g.rf_source) == (43.0, 1800, "estimated")
    assert (r.tx_power_dbm, r.frequency_mhz) == (43.0, 900)
    assert (st.tx_power_dbm, st.frequency_mhz) == (40.0, 2100)
    assert (c.tx_power_dbm, c.frequency_mhz) == (20.0, 900)       # construction: no techs


def test_backfill_fills_null_rf(rf_factory):
    db = rf_factory()
    db.query(CellSite).update({CellSite.tx_power_dbm: None, CellSite.frequency_mhz: None})
    db.commit()
    assert backfill_estimated_rf(db) > 0
    assert db.query(CellSite).filter(CellSite.tx_power_dbm.is_(None)
                                     | CellSite.frequency_mhz.is_(None)).count() == 0
    db.close()
    s = _site(rf_factory, "S1")
    assert (s.tx_power_dbm, s.frequency_mhz) == (40.0, 2100)


def test_patch_rf_manual_survives_reimport(rf_admin, rf_factory):
    sid = _site(rf_factory, "7").id
    r = rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"tx_power_dbm": 46.5, "note": "דף נתוני מפעיל"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["tx_power_dbm"], d["frequency_mhz"], d["rf_source"]) == (46.5, 1800, "manual")
    assert d["rf_note"] == "דף נתוני מפעיל" and d["rf_updated_by"] == "admin"
    assert d["rf_updated_at"].endswith("Z") and d["bands"] == [1800, 3500]
    r = rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"frequency_mhz": 3500})
    assert (r.json()["tx_power_dbm"], r.json()["frequency_mhz"]) == (46.5, 3500)

    db = rf_factory()
    import_data(db, _dataset_rf())
    db.close()
    s = _site(rf_factory, "7")
    assert (s.tx_power_dbm, s.frequency_mhz, s.rf_source) == (46.5, 3500, "manual")
    assert s.height_source == "estimated"                    # independent of height
    assert _site(rf_factory, "8").rf_source == "estimated"


def test_patch_rf_validation(rf_admin, rf_factory):
    sid = _site(rf_factory, "7").id
    assert rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={}).status_code == 422
    assert rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"note": "x"}).status_code == 422
    assert rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"tx_power_dbm": 71}).status_code == 422
    assert rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"frequency_mhz": 50}).status_code == 422
    assert rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"frequency_mhz": 6001}).status_code == 422
    assert rf_admin.patch("/api/cell-sites/999999/rf", json={"tx_power_dbm": 40}).status_code == 404


def test_delete_rf_reverts_to_estimate(rf_admin, rf_factory):
    sid = _site(rf_factory, "R1").id
    rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"tx_power_dbm": 50, "frequency_mhz": 2600})
    r = rf_admin.delete(f"/api/cell-sites/{sid}/rf")
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["tx_power_dbm"], d["frequency_mhz"], d["rf_source"], d["rf_note"]) == (43.0, 900, "estimated", None)


def test_rf_permissions(rf_factory):
    sid = _site(rf_factory, "7").id
    try:
        v = _client(rf_factory, UserRole.VIEWER)
        assert v.patch(f"/api/cell-sites/{sid}/rf", json={"tx_power_dbm": 40}).status_code == 403
        assert v.delete(f"/api/cell-sites/{sid}/rf").status_code == 403
        assert v.put("/api/cell-sites/band-defaults/4G", json={"frequency_mhz": 2600}).status_code == 403
        assert v.get("/api/cell-sites/band-defaults").status_code == 200
        o = _client(rf_factory, UserRole.OPERATOR)
        assert o.patch(f"/api/cell-sites/{sid}/rf", json={"tx_power_dbm": 40}).status_code == 200
        assert o.delete(f"/api/cell-sites/{sid}/rf").status_code == 200
        assert o.put("/api/cell-sites/band-defaults/4G", json={"frequency_mhz": 2600}).status_code == 403
        assert o.put("/api/cell-sites/type-defaults/sting",
                     json={"default_tx_power_dbm": 30}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_band_default_put_updates_only_estimated(rf_admin, rf_factory):
    manual = _site(rf_factory, "1").id
    tx_only = _site(rf_factory, "4").id
    rf_admin.patch(f"/api/cell-sites/{manual}/rf", json={"frequency_mhz": 1800})   # freq manual
    rf_admin.patch(f"/api/cell-sites/{tx_only}/rf", json={"tx_power_dbm": 44})     # freq still estimated
    r = rf_admin.put("/api/cell-sites/band-defaults/4g", json={"frequency_mhz": 2600})
    assert r.status_code == 200, r.text
    assert r.json()["generation"] == "4G" and r.json()["frequency_mhz"] == 2600
    assert r.json()["label_he"] == "4G – 2600 MHz"
    # 4G+5G masts: lowest band now 2600 (999 estimated); rooftop has 2G (900) -> unchanged
    assert r.json()["updated_sites"] == 999
    assert _site(rf_factory, "2").frequency_mhz == 2600
    t = _site(rf_factory, "4")
    assert (t.frequency_mhz, t.frequency_source, t.tx_power_dbm, t.tx_power_source) == (2600, "estimated", 44, "manual")
    assert _site(rf_factory, "1").frequency_mhz == 1800
    assert _site(rf_factory, "R1").frequency_mhz == 900
    assert _site(rf_factory, "S1").frequency_mhz == 2100
    det = rf_admin.get(f"/api/cell-sites/{_site(rf_factory, '2').id}").json()
    assert det["bands"] == [2600, 3500]
    bd = {b["generation"]: b for b in rf_admin.get("/api/cell-sites/band-defaults").json()}
    assert bd["4G"]["frequency_mhz"] == 2600 and bd["2G"]["frequency_mhz"] == 900
    assert bd["2G"]["label_he"] == BAND_DEFAULTS["2G"][1]                     # untouched label
    assert rf_admin.put("/api/cell-sites/band-defaults/6G", json={"frequency_mhz": 4000}).status_code == 404
    assert rf_admin.put("/api/cell-sites/band-defaults/4G", json={"frequency_mhz": 50}).status_code == 422


def test_type_default_tx_power_put(rf_admin, rf_factory):
    manual = _site(rf_factory, "1").id
    rf_admin.patch(f"/api/cell-sites/{manual}/rf", json={"tx_power_dbm": 44})
    r = rf_admin.put("/api/cell-sites/type-defaults/ground_mast", json={"default_tx_power_dbm": 45})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["default_tx_power_dbm"] == 45 and d["default_height_m"] == 25.0
    assert (d["updated_rf_sites"], d["updated_sites"]) == (999, 0)
    assert _site(rf_factory, "2").tx_power_dbm == 45 and _site(rf_factory, "2").ant_height_m == 25.0
    assert _site(rf_factory, "1").tx_power_dbm == 44
    assert rf_admin.put("/api/cell-sites/type-defaults/ground_mast", json={}).status_code == 422


def test_meta_and_list_include_rf(rf_admin, rf_factory):
    sid = _site(rf_factory, "3").id
    rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"tx_power_dbm": 41})
    rf = rf_admin.get("/api/cell-sites/meta").json()["rf_estimation"]
    assert rf["manual_count"] == 1 and rf["estimated_count"] == 1002
    assert len(rf["band_defaults"]) == 4 and rf["band_defaults"][0]["generation"] == "2G"
    assert {t["site_type"]: t["default_tx_power_dbm"] for t in rf["type_defaults"]} == TX_POWER_DEFAULTS
    assert "EIRP" in rf["note_he"]
    lst = rf_admin.get("/api/cell-sites/").json()
    assert lst["fields"][11:] == ["tx_power_dbm", "frequency_mhz", "rf_source", "rf_note",
                                 "rf_updated_at", "bands", "tx_power_source", "frequency_source"]
    row = next(r for r in lst["rows"] if r[0] == sid)
    assert len(row) == len(lst["fields"])
    assert row[11:14] == [41, 1800, "manual"] and row[15].endswith("Z") and row[16] == [1800, 3500]
    assert row[17:] == ["manual", "estimated"]
    assert (rf["tx_power_manual_count"], rf["frequency_manual_count"]) == (1, 0)
    tdefs = {d["site_type"]: d for d in rf_admin.get("/api/cell-sites/type-defaults").json()}
    assert tdefs["sting"]["default_tx_power_dbm"] == 40.0


# ── Promote a cell site to an analysis Site ──────────────────────────────────
@pytest.fixture
def no_dem(monkeypatch):
    monkeypatch.setattr(sites_api, "fetch_elevation", lambda lat, lon: 123.0)


def test_promote_creates_once(rf_admin, rf_factory, no_dem):
    c = _site(rf_factory, "7")
    r = rf_admin.post(f"/api/cell-sites/{c.id}/promote", json={})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["name"] == "סלקום – חיפה (7)"
    assert (s["lat"], s["lon"]) == (c.lat, c.lon)
    assert (s["ant_height"], s["tx_power"], s["frequency"]) == (25.0, 43.0, 1800)
    assert (s["rx_threshold"], s["max_radius"], s["elevation_m"]) == (-105.0, 30.0, 123.0)
    assert s["cell_site_id"] == c.id and s["params_source"] == "estimated"
    assert s["ant_height_is_default"] is False
    assert "גובה אנטנה" in s["source_note"] and "תדר" in s["source_note"]
    r2 = rf_admin.post(f"/api/cell-sites/{c.id}/promote", json={"frequency_mhz": 3500})
    assert r2.status_code == 200 and r2.json()["id"] == s["id"]
    db = rf_factory()
    assert db.query(Site).filter(Site.cell_site_id == c.id).count() == 1
    db.close()
    # visible through /api/sites with provenance
    got = rf_admin.get(f"/api/sites/{s['id']}").json()
    assert got["cell_site_id"] == c.id and got["params_source"] == "estimated"


def test_promote_body_overrides_and_manual_params(rf_admin, rf_factory, no_dem):
    c = _site(rf_factory, "R1")
    rf_admin.patch(f"/api/cell-sites/{c.id}/height", json={"ant_height_m": 31})
    rf_admin.patch(f"/api/cell-sites/{c.id}/rf", json={"tx_power_dbm": 47, "frequency_mhz": 1800})
    r = rf_admin.post(f"/api/cell-sites/{c.id}/promote", json={"frequency_mhz": 2100, "rx_threshold": -95})
    assert r.status_code == 201, r.text
    s = r.json()
    assert (s["ant_height"], s["tx_power"], s["frequency"], s["rx_threshold"]) == (31, 47, 2100, -95)
    assert s["params_source"] == "manual" and s["name"] == "פלאפון – חיפה (R1)"
    assert rf_admin.post(f"/api/cell-sites/{_site(rf_factory, '9').id}/promote",
                         json={"frequency_mhz": 50}).status_code == 422
    assert rf_admin.post("/api/cell-sites/999999/promote", json={}).status_code == 404


def test_promote_without_body(rf_admin, rf_factory, no_dem):
    c = _site(rf_factory, "S1")
    r = rf_admin.post(f"/api/cell-sites/{c.id}/promote")
    assert r.status_code == 201, r.text
    assert (r.json()["frequency"], r.json()["tx_power"]) == (2100, 40.0)


def test_promote_permissions_and_scope(rf_factory, no_dem):
    cid = _site(rf_factory, "7").id
    try:
        v = _client(rf_factory, UserRole.VIEWER)
        assert v.post(f"/api/cell-sites/{cid}/promote", json={}).status_code == 403
        app.dependency_overrides.clear()
        o = _client(rf_factory, UserRole.OPERATOR)     # user id 1
        first = o.post(f"/api/cell-sites/{cid}/promote", json={})
        assert first.status_code == 201
        app.dependency_overrides[get_current_user] = lambda: User(
            id=2, username="op2", full_name="U2", hashed_pw="x", role=UserRole.OPERATOR, is_active=True)
        other = o.post(f"/api/cell-sites/{cid}/promote", json={})
        assert other.status_code == 201 and other.json()["id"] != first.json()["id"]   # per-owner
        assert other.json()["owner_id"] == 2
    finally:
        app.dependency_overrides.clear()


def test_promoted_link_survives_reimport(rf_admin, rf_factory, no_dem):
    c = _site(rf_factory, "7")
    sid = rf_admin.post(f"/api/cell-sites/{c.id}/promote", json={}).json()["id"]
    data = _dataset_rf()
    data["active"].reverse()               # rows get different ids on re-import
    db = rf_factory()
    import_data(db, data)
    site = db.get(Site, sid)
    new = db.query(CellSite).filter(CellSite.site_code == "7").one()
    assert new.id != c.id and site.cell_site_id == new.id
    db.close()


# ── Per-field RF provenance ──────────────────────────────────────────────────
def test_patch_rf_marks_only_sent_fields(rf_admin, rf_factory):
    sid = _site(rf_factory, "7").id
    d = rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"tx_power_dbm": 45}).json()
    assert (d["tx_power_source"], d["frequency_source"], d["rf_source"]) == ("manual", "estimated", "manual")
    d = rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"frequency_mhz": 3500}).json()
    assert (d["tx_power_source"], d["frequency_source"]) == ("manual", "manual")
    assert (d["tx_power_dbm"], d["frequency_mhz"]) == (45, 3500)
    d = rf_admin.delete(f"/api/cell-sites/{sid}/rf").json()
    assert (d["tx_power_source"], d["frequency_source"], d["rf_source"]) == ("estimated",) * 3
    d = rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"frequency_mhz": 900}).json()
    assert (d["tx_power_source"], d["frequency_source"], d["rf_source"]) == ("estimated", "manual", "manual")


def test_type_default_tx_skips_only_manual_tx(rf_admin, rf_factory):
    freq_only = _site(rf_factory, "5").id
    rf_admin.patch(f"/api/cell-sites/{freq_only}/rf", json={"frequency_mhz": 3500})
    r = rf_admin.put("/api/cell-sites/type-defaults/ground_mast", json={"default_tx_power_dbm": 46})
    assert r.json()["updated_rf_sites"] == 1000
    s = _site(rf_factory, "5")
    assert (s.tx_power_dbm, s.tx_power_source, s.frequency_mhz, s.frequency_source) == (46, "estimated", 3500, "manual")
    assert s.rf_source == "manual"


def test_reimport_preserves_rf_per_field(rf_admin, rf_factory):
    sid = _site(rf_factory, "7").id
    rf_admin.patch(f"/api/cell-sites/{sid}/rf", json={"tx_power_dbm": 47, "note": "n"})
    db = rf_factory()
    db.query(CellSiteBandDefault).filter_by(generation="4G").update({"frequency_mhz": 2300})
    db.commit()
    import_data(db, _dataset_rf())
    db.close()
    s = _site(rf_factory, "7")
    assert (s.tx_power_dbm, s.tx_power_source) == (47, "manual")
    assert (s.frequency_mhz, s.frequency_source) == (2300, "estimated")   # new estimate applied
    assert (s.rf_source, s.rf_note) == ("manual", "n")


# ── Re-import identity ───────────────────────────────────────────────────────
def test_construction_to_active_keeps_manual_values_and_link(rf_admin, rf_factory, no_dem):
    c = _site(rf_factory, "C1")
    assert c.status == "construction"
    rf_admin.patch(f"/api/cell-sites/{c.id}/height", json={"ant_height_m": 22})
    rf_admin.patch(f"/api/cell-sites/{c.id}/rf", json={"tx_power_dbm": 38, "frequency_mhz": 2100})
    site_id = rf_admin.post(f"/api/cell-sites/{c.id}/promote", json={}).json()["id"]

    data = _dataset_rf()
    moved = data.pop("construction")[0]
    data["construction"] = []
    data["active"].insert(0, {"ID": 77, "חברה": moved["חברה"], "מס' אתר": "C1", "עיר": "חדרה",
                              "X_ITM": moved["X_ITM"], "Y_ITM": moved["Y_ITM"],
                              "סוג אתר": "אתר זעיר פנימי", "טכנולוגיית שידור": "דור 4"})
    db = rf_factory()
    import_data(db, data)
    new = db.query(CellSite).filter(CellSite.site_code == "C1").one()
    assert new.status == "active"
    assert (new.ant_height_m, new.height_source) == (22, "manual")
    assert (new.tx_power_dbm, new.frequency_mhz, new.tx_power_source, new.frequency_source) == \
        (38, 2100, "manual", "manual")
    assert db.get(Site, site_id).cell_site_id == new.id
    db.close()


def _collision_dataset():
    d = _dataset_rf()
    d["active"].append({"ID": 70, "חברה": "פלאפון", "מס' אתר": "X1", "עיר": "חדרה",
                        "X_ITM": 190500, "Y_ITM": 705500, "סוג אתר": "תורן קרקעי"})
    d["construction"].append({"ID": 71, "חברה": "פלאפון", "מספר האתר": "X1", "ישוב": "חדרה",
                              "X_ITM": 190600, "Y_ITM": 705600, "סוג המוקד": "תורן קרקעי"})
    return d


def test_colliding_site_codes_use_status(rf_admin, rf_factory):
    db = rf_factory()
    import_data(db, _collision_dataset())
    ids = {s.status: s.id for s in db.query(CellSite).filter(CellSite.site_code == "X1").all()}
    db.close()
    rf_admin.patch(f"/api/cell-sites/{ids['active']}/height", json={"ant_height_m": 40})
    rf_admin.patch(f"/api/cell-sites/{ids['construction']}/height", json={"ant_height_m": 12})
    db = rf_factory()
    import_data(db, _collision_dataset())
    got = {s.status: s.ant_height_m for s in db.query(CellSite).filter(CellSite.site_code == "X1").all()}
    assert got == {"active": 40, "construction": 12}
    # construction row disappears -> the active row (now unique code) keeps ITS value
    d = _collision_dataset()
    d["construction"].pop()
    import_data(db, d)
    rows = db.query(CellSite).filter(CellSite.site_code == "X1").all()
    assert [(r.status, r.ant_height_m) for r in rows] == [("active", 40)]
    db.close()


# ── Promote: uniqueness / race / provenance on edit ──────────────────────────
from sqlalchemy.exc import IntegrityError
from app.api import cell_sites as cell_sites_api


def test_unique_owner_cell_site_index(rf_factory):
    cid = _site(rf_factory, "7").id
    db = rf_factory()
    kw = dict(name="a", lat=32, lon=35, frequency=900, owner_id=1, cell_site_id=cid)
    db.add(Site(**kw)); db.commit()
    db.add(Site(**kw))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(Site(**{**kw, "cell_site_id": None})); db.add(Site(**{**kw, "cell_site_id": None}))
    db.commit()                                     # NULL links are unconstrained
    db.close()


def test_promote_race_returns_existing(rf_admin, rf_factory, no_dem, monkeypatch):
    cid = _site(rf_factory, "7").id
    first = rf_admin.post(f"/api/cell-sites/{cid}/promote", json={}).json()
    real = cell_sites_api._existing_promoted
    calls = {"n": 0}

    def racy(db, cell_id, user):          # the pre-check misses the row created "concurrently"
        calls["n"] += 1
        return None if calls["n"] == 1 else real(db, cell_id, user)
    monkeypatch.setattr(cell_sites_api, "_existing_promoted", racy)
    r = rf_admin.post(f"/api/cell-sites/{cid}/promote", json={})
    assert r.status_code == 200 and r.json()["id"] == first["id"] and calls["n"] == 2


def test_promote_explicit_freq_is_still_estimated(rf_admin, rf_factory, no_dem):
    c = _site(rf_factory, "R1")
    rf_admin.patch(f"/api/cell-sites/{c.id}/height", json={"ant_height_m": 31})
    rf_admin.patch(f"/api/cell-sites/{c.id}/rf", json={"tx_power_dbm": 47})
    s = rf_admin.post(f"/api/cell-sites/{c.id}/promote", json={"frequency_mhz": 1800}).json()
    assert s["frequency"] == 1800 and s["params_source"] == "estimated"
    assert "תדר" in s["source_note"] and "גובה" not in s["source_note"]


def test_editing_promoted_site_marks_manual(rf_admin, rf_factory, no_dem):
    a = rf_admin.post(f"/api/cell-sites/{_site(rf_factory, '7').id}/promote", json={}).json()
    b = rf_admin.post(f"/api/cell-sites/{_site(rf_factory, '8').id}/promote", json={}).json()
    r = rf_admin.put(f"/api/sites/{a['id']}", json={"tx_power": 46})
    assert r.status_code == 200, r.text
    assert r.json()["params_source"] == "manual"
    assert r.json()["source_note"].startswith(a["source_note"])
    assert "עודכן ידנית ב-" in r.json()["source_note"] and len(r.json()["source_note"]) <= 256
    r = rf_admin.put(f"/api/sites/{b['id']}", json={"name": "שם", "rx_threshold": -100})
    assert r.json()["params_source"] == "estimated" and r.json()["source_note"] == b["source_note"]


# ── Migrations ───────────────────────────────────────────────────────────────
from sqlalchemy import text as _sql
from app.core.migrations import run_startup_migrations


def test_migration_splits_rf_source_once():
    eng = create_engine("sqlite://", poolclass=StaticPool)
    with eng.begin() as c:
        c.execute(_sql("CREATE TABLE cell_sites (id INTEGER PRIMARY KEY, rf_source VARCHAR(16))"))
        c.execute(_sql("INSERT INTO cell_sites VALUES (1, 'manual'), (2, 'estimated')"))
        c.execute(_sql("CREATE TABLE sites (id INTEGER PRIMARY KEY, owner_id INTEGER)"))
    run_startup_migrations(eng)
    q = "SELECT id, tx_power_source, frequency_source FROM cell_sites ORDER BY id"
    with eng.connect() as c:
        assert list(c.execute(_sql(q))) == [(1, "manual", "manual"), (2, "estimated", "estimated")]
        idx = {r[1] for r in c.execute(_sql("PRAGMA index_list(sites)"))}
    assert {"ix_sites_cell_site_id", "uq_sites_owner_cell_site"} <= idx
    with eng.begin() as c:                       # a later per-field edit is not undone on restart
        c.execute(_sql("UPDATE cell_sites SET frequency_source = 'estimated' WHERE id = 1"))
    run_startup_migrations(eng)
    with eng.connect() as c:
        assert list(c.execute(_sql(q)))[0] == (1, "manual", "estimated")
    eng.dispose()
