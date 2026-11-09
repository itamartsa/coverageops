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
    assert lst["fields"][7:] == ["ant_height_m", "height_source", "height_note", "height_updated_at"]
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
