"""Demo fleet seed (app.scripts.field_simulator --seed-demo) and startup migrations."""
from datetime import datetime

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.migrations import run_startup_migrations
from app.models.field import (
    FieldAggDaily, FieldAggHourly, FieldAlert, FieldDevice, FieldDeviceGroup, FieldMeasurement,
)
from app.models.user import UserRole
from app.services.field_demo import DEMO_FLEET, DEMO_GROUP, remove_demo, seed_demo
from app.services.field_status import compute_status
from tests.field_common import as_role, factory, new_device, sample  # noqa: F401


def _counts(db):
    return (db.query(FieldDevice).count(), db.query(FieldMeasurement).count(), db.query(FieldAggHourly).count(),
            db.query(FieldAggDaily).count(), db.query(FieldDeviceGroup).filter_by(name=DEMO_GROUP).count())


def test_demo_seed_is_idempotent_labelled_and_removable(factory):
    admin = as_role(factory, UserRole.ADMIN)
    real = new_device(admin, label="מכשיר אמיתי")              # must survive seed / re-seed / remove
    real.batch([sample()])
    db = factory()
    r1 = seed_demo(db, hours=1.0)
    c1 = _counts(db)
    assert r1["devices"] == len(DEMO_FLEET) >= 20 and r1["samples"] > 1000
    r2 = seed_demo(db, hours=1.0)
    c2 = _counts(db)
    assert r2["removed_previous"]["devices"] == len(DEMO_FLEET)
    assert c2[0] == c1[0] == len(DEMO_FLEET) + 1 and c2[4] == 1 and abs(c2[1] - c1[1]) <= len(DEMO_FLEET)
    demo = db.query(FieldDevice).join(FieldDeviceGroup, FieldDevice.group_id == FieldDeviceGroup.id) \
        .filter(FieldDeviceGroup.name == DEMO_GROUP).all()
    assert len(demo) == len(DEMO_FLEET) and all(d.label.startswith("דמו · ") for d in demo)
    now = datetime.utcnow()
    statuses = {compute_status(d, now)[0] for d in demo}
    assert {"active", "offline", "paused", "degraded", "no_contact", "never_seen"} <= statuses
    assert {d.ownership for d in demo} == {"ORG", "PERSONAL"}
    nts = {m.network_type for m in db.query(FieldMeasurement).all()}
    assert {"LTE", "NR_NSA", "NR_SA", "UMTS", "NONE"} <= nts
    assert {m.operator_key for m in db.query(FieldMeasurement).all()} >= {"425-01", "425-02", "425-03"}
    assert db.query(FieldMeasurement).filter(FieldMeasurement.flags.op("&")(32) > 0).count() > 0      # coarse
    assert db.query(FieldMeasurement).filter(FieldMeasurement.lat < 30.3, FieldMeasurement.lat > 30.1,
                                             FieldMeasurement.tier == 0).count() > 0             # dead zone
    assert {a.type for a in db.query(FieldAlert).all()} >= {"device_offline", "monitoring_degraded", "no_contact",
                                                            "low_battery"}
    s = admin.get("/api/field/summary").json()
    assert s["fleet"]["total"] == len(DEMO_FLEET) + 1 and s["kpis"]["samples"] > 1000
    res = remove_demo(db)
    assert res["devices"] == len(DEMO_FLEET)
    assert db.query(FieldDevice).count() == 1 and db.query(FieldDeviceGroup).filter_by(name=DEMO_GROUP).count() == 0
    assert db.query(FieldMeasurement).count() == 1
    assert sum(r.n for r in db.query(FieldAggHourly).all()) == 1          # only the real device's sample remains
    db.close()


def test_startup_migrations_twice_with_field_tables():
    eng = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(eng)
    run_startup_migrations(eng)
    run_startup_migrations(eng)
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert {"field_devices", "field_measurements", "field_heartbeats", "field_device_commands", "field_agg_hourly",
            "field_agg_daily", "field_agg_daily_coarse", "field_agg_dirty", "field_alerts", "field_alert_rules",
            "field_enrollment_codes", "field_device_groups", "field_operator_map"} <= tables
    with eng.connect() as c:
        idx = {r[1] for r in c.execute(text("PRAGMA index_list(field_measurements)"))}
        cmd_idx = {r[1] for r in c.execute(text("PRAGMA index_list(field_device_commands)"))}
        alert_idx = {r[1] for r in c.execute(text("PRAGMA index_list(field_alerts)"))}
    assert {"ix_fm_ts", "ix_fm_device_sample"} <= idx
    assert "uq_cmd_open" in cmd_idx and "uq_field_alert_open" in alert_idx
    eng.dispose()


def test_demo_api_admin_only_seed_and_remove(factory, monkeypatch):
    from app.core.config import settings
    admin = as_role(factory, UserRole.ADMIN)
    assert admin.get("/api/field/demo").json() == {"enabled": True, "group": DEMO_GROUP, "devices": 0}
    r = admin.post("/api/field/demo/seed?hours=1")
    assert r.status_code == 200 and r.json()["devices"] == len(DEMO_FLEET)
    assert admin.get("/api/field/demo").json()["devices"] == len(DEMO_FLEET)
    assert len(admin.get("/api/field/devices").json()["rows"]) == len(DEMO_FLEET)
    for role in (UserRole.OPERATOR, UserRole.VIEWER):
        other = as_role(factory, role)
        assert other.post("/api/field/demo/seed").status_code == 403
        assert other.delete("/api/field/demo").status_code == 403
    admin = as_role(factory, UserRole.ADMIN)
    assert admin.delete("/api/field/demo").status_code == 200
    assert admin.get("/api/field/demo").json()["devices"] == 0
    monkeypatch.setattr(settings, "FIELD_DEMO_ENABLED", False)
    assert admin.post("/api/field/demo/seed").status_code == 404
    assert admin.get("/api/field/demo").json()["enabled"] is False


def test_enrollment_code_without_segno_falls_back_to_manual_code(factory, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_segno(name, *a, **kw):
        if name == "segno":
            raise ImportError("segno missing")
        return real_import(name, *a, **kw)

    admin = as_role(factory, UserRole.ADMIN)
    monkeypatch.setattr(builtins, "__import__", no_segno)
    r = admin.post("/api/field/enrollment-codes", json={"ownership": "ORG"})
    assert r.status_code == 201 and r.json()["qr_svg"] is None and r.json()["code"]
