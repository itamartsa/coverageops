"""Field dashboard API — roles (incl. VIEWER rules), live status, KPI maths,
aggregates/heatmap/dead zones, alerts, privacy (delete/anonymise/retention),
audit logging, enrollment codes/QR, groups, device requirements."""
import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.field import (
    FieldAggDaily, FieldAggDailyCoarse, FieldAggHourly, FieldAlert, FieldDevice, FieldHeartbeat, FieldMeasurement,
)
from app.models.user import ActivityLog, UserRole
from app.services import field_kpi
from app.services.field_alerts import evaluate_device_alerts
from app.services.field_ingest import ingest_batch
from app.services.field_maintenance import retention, run_maintenance
from app.services.field_status import compute_status
from tests.field_common import (  # noqa: F401  (factory is a fixture)
    as_role, create_code, factory, iso, new_device, sample,
)


@pytest.fixture
def admin(factory):
    return as_role(factory, UserRole.ADMIN)


def _maint(factory, tasks=("aggregate",), now=None):
    db = factory()
    try:
        return run_maintenance(db, list(tasks), now=now)
    finally:
        db.close()


def _audit(factory, action):
    db = factory()
    try:
        return [json.loads(a.detail) | {"_user": a.user_id}
                for a in db.query(ActivityLog).filter(ActivityLog.action == action).all()]
    finally:
        db.close()


# ── role matrix ───────────────────────────────────────────────────────────────
def test_role_matrix(factory):
    a = as_role(factory, UserRole.ADMIN)
    d = new_device(a)
    d.batch([sample()])
    a_paths = ["/api/field/enrollment-codes", "/api/field/audit"]
    ao_paths = ["/api/field/devices", "/api/field/devices/live", f"/api/field/devices/{d.id}",
                f"/api/field/devices/{d.id}/track", f"/api/field/devices/{d.id}/timeseries",
                f"/api/field/devices/{d.id}/commands", "/api/field/alerts", "/api/field/alert-rules",
                "/api/field/groups", "/api/field/export/measurements.csv", "/api/field/export/measurements.geojson"]
    aov_paths = ["/api/field/summary", "/api/field/heatmap?bbox=29,34,34,36&measure=lte_rsrp",
                 "/api/field/dead-zones", "/api/field/breakdown?by=operator", "/api/field/timeseries?metric=samples&bucket=1d",
                 "/api/field/operators", "/api/field/meta/device-requirements", "/api/field/meta/tiers",
                 "/api/field/export/kpis.csv", "/api/field/export/heatmap.geojson?bbox=29,34,34,36&measure=lte_rsrp"]
    for role, allowed in ((UserRole.ADMIN, a_paths + ao_paths + aov_paths),
                          (UserRole.OPERATOR, ao_paths + aov_paths),
                          (UserRole.VIEWER, aov_paths)):
        c = as_role(factory, role)
        for p in a_paths + ao_paths + aov_paths:
            r = c.get(p)
            assert r.status_code == (200 if p in allowed else 403), (role, p, r.status_code, r.text[:200])
    for role in (UserRole.VIEWER, UserRole.OPERATOR):
        c = as_role(factory, role)            # dependency overrides are global: one role at a time
        assert c.post("/api/field/enrollment-codes", json={"ownership": "ORG"}).status_code == 403
        assert c.patch(f"/api/field/devices/{d.id}", json={"label": "x"}).status_code == 403
        assert c.post(f"/api/field/devices/{d.id}/revoke").status_code == 403
        assert c.delete(f"/api/field/devices/{d.id}").status_code == 403
        assert c.post("/api/field/groups", json={"name": "g"}).status_code == 403
        assert c.post("/api/field/maintenance/run").status_code == 403
    v = as_role(factory, UserRole.VIEWER)
    assert v.get("/api/field/summary?group_id=1").status_code == 403
    assert v.get("/api/field/breakdown?by=group").status_code == 403
    assert v.get("/api/field/timeseries?metric=samples&bucket=1h").status_code == 422     # day resolution only


# ── live status (controllable clock) ──────────────────────────────────────────
def _d(**kw):
    base = dict(status="active", withdrawn_at=None, revoked_at=None, deleted_at=None, enrolled_at=datetime(2026, 1, 1),
                last_heartbeat_at=None, last_sample_received_at=None, last_monitoring_state=None,
                state_changed_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_status_computation():
    now = datetime(2026, 11, 10, 12, 0)
    m = lambda **kw: now - timedelta(**kw)               # noqa: E731
    assert compute_status(_d(), now)[0] == "never_seen"
    assert compute_status(_d(status="withdrawn", last_heartbeat_at=m(minutes=1)), now)[0] == "withdrawn"
    assert compute_status(_d(status="revoked", last_heartbeat_at=m(minutes=1)), now)[0] == "revoked"
    act = dict(last_monitoring_state="active")
    assert compute_status(_d(last_heartbeat_at=m(minutes=14), **act), now)[0] == "active"
    assert compute_status(_d(last_heartbeat_at=m(minutes=16), **act), now) == ("offline", m(minutes=16))
    assert compute_status(_d(last_heartbeat_at=m(minutes=16), last_monitoring_state="degraded"), now)[0] == "offline"
    assert compute_status(_d(last_heartbeat_at=m(minutes=3), last_monitoring_state="degraded"), now)[0] == "degraded"
    # paused: no OFFLINE rule (v2) — "paused · last seen", then "no contact" after 6 h
    p = dict(last_monitoring_state="paused_by_user", state_changed_at=m(hours=5))
    assert compute_status(_d(last_heartbeat_at=m(hours=3), **p), now) == ("paused", m(hours=5))
    assert compute_status(_d(last_heartbeat_at=m(hours=5, minutes=59), **p), now)[0] == "paused"
    assert compute_status(_d(last_heartbeat_at=m(hours=6, minutes=1), **p), now)[0] == "no_contact"
    assert compute_status(_d(last_heartbeat_at=m(hours=7), last_monitoring_state="paused_schedule"), now)[0] == \
        "no_contact"
    # samples without any heartbeat count as contact
    assert compute_status(_d(last_sample_received_at=m(minutes=2)), now)[0] == "active"


def test_device_list_and_live(admin, factory):
    d1 = new_device(admin, label="פעיל")
    d2 = new_device(admin, label="מושהה")
    d1.batch([sample()])
    d1.heartbeat()
    d2.batch([sample(lat=32.08, lon=34.78, loc_accuracy_m=1500.0)])
    d2.heartbeat(monitoring_state="paused_by_user")
    j = admin.get("/api/field/devices", params={"sort": "label"}).json()
    rows = [dict(zip(j["fields"], r)) for r in j["rows"]]
    by = {r["label"]: r for r in rows}
    assert by["פעיל"]["status"] == "active" and by["מושהה"]["status"] == "paused"
    assert by["פעיל"]["tier"] == 3 and by["פעיל"]["group"] == "כללי"
    assert [r["label"] for r in rows] == ["מושהה", "פעיל"]
    assert len(admin.get("/api/field/devices", params={"status": "paused"}).json()["rows"]) == 1
    live = admin.get("/api/field/devices/live").json()
    ids = [dict(zip(live["fields"], r))["id"] for r in live["rows"]]
    assert ids == [d1.id]                                       # paused devices are not on the live map
    # a coarse fix is reported as precision=coarse
    d2.heartbeat(monitoring_state="active")
    live = admin.get("/api/field/devices/live").json()
    rows = {r[0]: dict(zip(live["fields"], r)) for r in live["rows"]}
    assert rows[d2.id]["precision"] == "coarse" and rows[d1.id]["precision"] == "precise"
    since = live["server_time"]
    assert admin.get("/api/field/devices/live", params={"since": since}).json()["rows"] == []


# ── KPI maths on a synthetic dataset with known answers ──────────────────────
def _ingest(factory, device_id, samples, now):
    db = factory()
    try:
        d = db.query(FieldDevice).filter(FieldDevice.public_id == uuid.UUID(device_id)).one()
        return ingest_batch(db, d, samples, now, now)
    finally:
        db.close()


def test_kpi_maths_known_answer(admin, factory):
    a = new_device(admin)
    b = new_device(admin)
    t0 = (datetime.utcnow() - timedelta(hours=2)).replace(microsecond=0)
    now = t0 + timedelta(minutes=10)
    _ingest(factory, a.id, [
        sample(ts=t0, rsrp_dbm=-80.0, cell_id=1),                                            # tier 4
        sample(ts=t0 + timedelta(seconds=60), rsrp_dbm=-90.0, cell_id=2),                    # tier 3, cell change
        sample(ts=t0 + timedelta(seconds=120), service_state="out_of_service", network_type="NONE",
               rsrp_dbm=None, cell_id=None),                                                 # tier 0 (OOS)
        sample(ts=t0 + timedelta(seconds=180), rsrp_dbm=-100.0, cell_id=2),                  # tier 2
        sample(ts=t0 + timedelta(seconds=240), rsrp_dbm=-120.0, cell_info_age_ms=20000),     # stale → unknown
    ], now)
    _ingest(factory, b.id, [
        sample(ts=t0, network_type="NR_SA", rsrp_dbm=-95.0, mnc="02", cell_id=9),           # tier 3
        sample(ts=t0 + timedelta(seconds=60), network_type="NR_SA", rsrp_dbm=-105.0, mnc="02", cell_id=9),  # tier 2
    ], now)
    to = t0 + timedelta(seconds=300)
    db = factory()
    k = field_kpi.compute_kpis(field_kpi.load_window(db, t0, to))
    db.close()
    # time weights: A = 60,60,60,60 (+60 stale);  B = 60 and min(600, 300-60)=240
    # classified: t4=60, t3=60+60, t2=60+240, t0=60 → 540 s
    assert k["time_s"] == 600
    assert k["pct_time_by_tier"] == [pytest.approx(100 * 60 / 540, abs=0.05), 0.0,
                                     pytest.approx(100 * 300 / 540, abs=0.05), pytest.approx(100 * 120 / 540, abs=0.05),
                                     pytest.approx(100 * 60 / 540, abs=0.05)]
    assert k["pct_time_unknown"] == pytest.approx(10.0)                     # 60 s stale of 600 s
    assert k["pct_time_unclassified"] == 0.0
    assert k["oos_minutes"] == 1.0
    assert k["rsrp"]["lte"] == {"p5": -99.0, "p50": -90.0, "n": 3}         # stale -120 excluded
    assert k["rsrp"]["nr_sa"] == {"p5": -104.5, "p50": -100.0, "n": 2}     # never mixed with LTE
    assert k["rsrp"]["nsa_nr_leg"]["n"] == 0
    assert k["cell_change_per_hour"] == pytest.approx(30.0)                 # 1 change over 120 s observed
    assert k["stale_excluded_pct"] == pytest.approx(100 / 7, abs=0.05)
    # the same through the API (and the operator breakdown keeps LTE / NR apart)
    s = admin.get("/api/field/summary", params={"from": iso(t0), "to": iso(to)}).json()
    assert s["kpis"]["oos_minutes"] == 1.0 and s["kpis"]["rsrp"]["nr_sa"]["p50"] == -100.0
    rows = {r["key"]: r for r in admin.get("/api/field/breakdown",
                                           params={"by": "operator", "from": iso(t0), "to": iso(to)}).json()["rows"]}
    assert rows["425-01"]["rsrp"]["nr_sa"]["n"] == 0 and rows["425-02"]["rsrp"]["lte"]["n"] == 0
    tech = {r["key"]: r for r in admin.get("/api/field/breakdown",
                                           params={"by": "tech", "from": iso(t0), "to": iso(to)}).json()["rows"]}
    assert set(tech) == {"LTE", "NR", "NONE"}


# ── aggregates, heatmap, k-anonymity, dead zones ─────────────────────────────
def _cell_samples(t, n, lat=32.08123, lon=34.78123, **kw):
    return [sample(ts=t + timedelta(seconds=30 * i), lat=lat + 0.00001 * i, lon=lon, **kw) for i in range(n)]


def test_aggregation_idempotent_and_no_tech_mixing(admin, factory):
    d = new_device(admin)
    t = datetime.utcnow() - timedelta(minutes=30)
    d.batch(_cell_samples(t, 4, rsrp_dbm=-90.0) +
            _cell_samples(t + timedelta(seconds=7), 3, network_type="NR_SA", rsrp_dbm=-70.0) +
            [sample(ts=t, lat=32.08, lon=34.78, loc_accuracy_m=1500.0)])            # coarse
    _maint(factory)
    db = factory()
    snap = sorted((r.gx, r.gy, r.meas_tech, r.n, r.n_rsrp, r.sum_rsrp) for r in db.query(FieldAggHourly).all())
    db.close()
    db = factory()
    from app.services.field_ingest import mark_dirty
    rows = [dict(ts=m.ts, gx=m.gx, gy=m.gy, gx25=m.gx25, gy25=m.gy25, operator_key=m.operator_key,
                 meas_tech=m.meas_tech) for m in db.query(FieldMeasurement).all()]
    mark_dirty(db, rows, datetime.utcnow())
    db.commit()
    db.close()
    _maint(factory)
    db = factory()
    assert sorted((r.gx, r.gy, r.meas_tech, r.n, r.n_rsrp, r.sum_rsrp) for r in db.query(FieldAggHourly).all()) == snap
    for r in db.query(FieldAggHourly).all():                  # a bucket never holds both LTE RSRP and SS-RSRP
        assert r.meas_tech in ("LTE", "NR")
        assert (r.meas_tech == "NR") == (r.sum_rsrp == pytest.approx(-70.0 * r.n_rsrp))
    assert sum(r.n for r in db.query(FieldAggHourly).all()) == 7                  # coarse not in G100
    assert sum(r.n for r in db.query(FieldAggDaily).all()) == 7
    assert sum(r.n for r in db.query(FieldAggDailyCoarse).all()) == 8              # 2.5 km includes coarse
    db.close()


def test_heatmap_viewer_k_anonymity(factory):
    a = as_role(factory, UserRole.ADMIN)
    devs = [new_device(a) for _ in range(3)]
    t = datetime.utcnow() - timedelta(minutes=20)
    for d in devs[:2]:
        d.batch(_cell_samples(t, 2, rsrp_dbm=-90.0))
    _maint(factory)
    params = {"bbox": "31.9,34.6,32.2,34.9", "measure": "lte_rsrp"}
    full = a.get("/api/field/heatmap", params=params).json()
    assert full["fields"][-1] == "n_devices" and full["rows"][0][-1] == 2
    assert full["rows"][0][3] == pytest.approx(-90.0, abs=2.0)          # percentile from 2-dB histogram
    v = as_role(factory, UserRole.VIEWER)
    hm = v.get("/api/field/heatmap", params=params).json()
    assert hm["rows"] == [] and hm["suppressed"] == 1 and "n_devices" not in hm["fields"]
    devs[2].batch(_cell_samples(t, 2, rsrp_dbm=-90.0))
    _maint(factory)
    hm = v.get("/api/field/heatmap", params=params).json()
    assert len(hm["rows"]) == 1 and hm["suppressed"] == 0 and len(hm["rows"][0]) == 5
    assert hm["source"] == "daily"                                   # day resolution for VIEWER
    assert v.get("/api/field/heatmap", params={**params, "level": "2500"}).json()["includes_coarse"] is True
    assert a.get("/api/field/heatmap", params={**params, "level": "500"}).json()["cell_deg"] == [0.0045, 0.0053]
    nr = a.get("/api/field/heatmap", params={**params, "measure": "nr_ss_rsrp"}).json()
    assert nr["rows"] == []                                           # LTE RSRP never shown as SS-RSRP


def test_dead_zone_cluster(admin, factory):
    d1, d2 = new_device(admin), new_device(admin)
    t = datetime.utcnow() - timedelta(minutes=40)
    oos = dict(service_state="out_of_service", network_type="NONE", rsrp_dbm=None, cell_id=None)
    for d in (d1, d2):          # two adjacent G100 cells, 6 OOS samples each per device
        d.batch(_cell_samples(t, 6, lat=30.15005, lon=35.12005, **oos) +
                _cell_samples(t + timedelta(minutes=5), 6, lat=30.15005, lon=35.12112, **oos))
    lone = new_device(admin)                                          # one device only → not "dead"
    lone.batch(_cell_samples(t, 12, lat=30.30005, lon=35.15005, **oos))
    _maint(factory)
    zones = admin.get("/api/field/dead-zones").json()
    assert len(zones) == 1
    z = zones[0]
    assert (z["operator_key"], z["tech"], z["cells"], z["oos_pct"], z["severity"]) == ("425-01", "LTE", 2, 100.0,
                                                                                         "critical")
    assert z["n"] == 24 and z["n_devices"] == 2 and z["polygon"]["type"] == "MultiPolygon"
    assert as_role(factory, UserRole.VIEWER).get("/api/field/dead-zones").json() == []   # < k devices


# ── alerts ────────────────────────────────────────────────────────────────────
def _set(factory, device_id, **kw):
    db = factory()
    db.query(FieldDevice).filter(FieldDevice.public_id == uuid.UUID(device_id)).update(kw)
    db.commit()
    db.close()


def _eval(factory, now=None):
    db = factory()
    try:
        return evaluate_device_alerts(db, now)
    finally:
        db.close()


def _open(admin):
    return {(a["type"], a["device_id"]): a for a in admin.get("/api/field/alerts").json()}


def test_alerts_offline_paused_no_contact(admin, factory):
    org, per, org_paused = new_device(admin, "ORG"), new_device(admin, "PERSONAL"), new_device(admin, "ORG")
    for d in (org, per, org_paused):
        d.heartbeat()
    per.heartbeat(monitoring_state="paused_by_user")
    org_paused.heartbeat(monitoring_state="paused_by_user")
    now = datetime.utcnow()
    _set(factory, org.id, last_heartbeat_at=now - timedelta(minutes=16))
    _set(factory, per.id, last_heartbeat_at=now - timedelta(hours=3))
    _eval(factory)
    o = _open(admin)
    assert o[("device_offline", org.id)]["severity"] == "warning"
    assert ("device_offline", per.id) not in o and ("no_contact", per.id) not in o
    assert ("monitoring_paused", per.id) not in o                     # never for PERSONAL
    assert o[("monitoring_paused", org_paused.id)]["severity"] == "warning"
    _set(factory, per.id, last_heartbeat_at=now - timedelta(hours=6, minutes=1))
    _eval(factory)
    o = _open(admin)
    assert o[("no_contact", per.id)]["severity"] == "info"
    # recovery resolves; the 10-minute cooldown prevents an immediate reopen
    org.heartbeat()
    _eval(factory)
    assert ("device_offline", org.id) not in _open(admin)
    _set(factory, org.id, last_heartbeat_at=datetime.utcnow() - timedelta(minutes=20))
    _eval(factory)
    assert ("device_offline", org.id) not in _open(admin)
    _eval(factory, datetime.utcnow() + timedelta(minutes=11))
    a = _open(admin)[("device_offline", org.id)]
    assert a["occurrences"] == 2 and "previous_id" in a["details"]


def test_alerts_battery_degraded_data_loss(admin, factory):
    org, per = new_device(admin, "ORG"), new_device(admin, "PERSONAL")
    org.heartbeat(battery_pct=4, monitoring_state="degraded", degraded_reasons=["location_permission_missing"],
                  evicted_samples_total=0)
    org.heartbeat(battery_pct=4, monitoring_state="degraded", degraded_reasons=["location_permission_missing"],
                  evicted_samples_total=50)
    per.heartbeat(monitoring_state="degraded", degraded_reasons=["location_permission_missing"])
    _eval(factory)
    o = _open(admin)
    assert o[("low_battery", org.id)]["severity"] == "critical"
    assert o[("monitoring_degraded", org.id)]["severity"] == "critical"
    assert o[("monitoring_degraded", per.id)]["severity"] == "info"
    assert o[("data_loss", org.id)]["details"]["evicted"] == 50
    org.heartbeat(battery_pct=18, monitoring_state="active")            # 18 < 15 + 5 → hysteresis keeps it
    _eval(factory)
    o = _open(admin)
    assert ("low_battery", org.id) in o and ("monitoring_degraded", org.id) not in o
    org.heartbeat(battery_pct=21)
    _eval(factory)
    assert ("low_battery", org.id) not in _open(admin)
    aid = _open(admin)[("data_loss", org.id)]["id"]
    acked = admin.post(f"/api/field/alerts/{aid}/ack", json={"note": "בטיפול"}).json()
    assert acked["status"] == "acked" and acked["acked_by"] == "admin"
    assert admin.post(f"/api/field/alerts/{aid}/resolve").json()["status"] == "resolved"


def test_dead_zone_entered_ignores_stale(admin, factory):
    d = new_device(admin)
    d.heartbeat()
    t = datetime.utcnow() - timedelta(minutes=5)
    oos = dict(service_state="in_service", rsrp_dbm=-125.0)
    d.batch([sample(ts=t + timedelta(seconds=40 * i), cell_info_age_ms=30000, **oos) for i in range(3)])
    _eval(factory)
    assert ("dead_zone_entered", d.id) not in _open(admin)
    d.batch([sample(ts=t + timedelta(seconds=150 + 40 * i), **oos) for i in range(3)])
    _eval(factory)
    assert ("dead_zone_entered", d.id) in _open(admin)


# ── track / export / audit ────────────────────────────────────────────────────
def test_track_is_audited_and_geojson(admin, factory):
    d = new_device(admin)
    t = datetime.utcnow() - timedelta(minutes=20)
    d.batch([sample(ts=t, rsrp_dbm=-80.0, lat=32.0801, lon=34.7801),
             sample(ts=t + timedelta(seconds=30), rsrp_dbm=-82.0, lat=32.0811, lon=34.7811),
             sample(ts=t + timedelta(seconds=60), rsrp_dbm=-110.0, lat=32.0821, lon=34.7821),
             sample(ts=t + timedelta(seconds=90), lat=32.09, lon=34.79, loc_accuracy_m=1500.0),
             sample(ts=t + timedelta(seconds=120), rsrp_dbm=-90.0, lat=32.0831, lon=34.7831, cell_info_age_ms=40000),
             sample(ts=t + timedelta(seconds=150), rsrp_dbm=-91.0, lat=32.0841, lon=34.7841)])
    v = as_role(factory, UserRole.VIEWER)
    assert v.get(f"/api/field/devices/{d.id}/track").status_code == 403
    assert _audit(factory, "FIELD_TRACK_VIEW") == []                    # nothing logged on 403
    a = as_role(factory, UserRole.ADMIN)
    g = a.get(f"/api/field/devices/{d.id}/track").json()
    kinds = [f["properties"]["kind"] for f in g["features"]]
    assert kinds.count("segment") == 2 and kinds.count("coarse_cell") == 1 and kinds.count("point") == 5
    seg = [f for f in g["features"] if f["properties"]["kind"] == "segment"]
    # tier-4 run (closed at the change point), coarse fix breaks the line, then a grey stale piece
    assert [(s["properties"]["tier"], s["properties"]["stale"]) for s in seg] == [(4, False), (None, True)]
    assert len(seg[0]["geometry"]["coordinates"]) == 3
    stale = [f for f in g["features"] if f["properties"]["kind"] == "point" and f["properties"]["stale"]]
    assert stale and stale[0]["properties"]["rsrp_dbm"] is None
    logs = _audit(factory, "FIELD_TRACK_VIEW")
    assert len(logs) == 1 and logs[0]["device_id"] == d.id and logs[0]["points"] == 6 and logs[0]["_user"] == 1
    assert a.get(f"/api/field/devices/{d.id}/track", params={"from": iso(t - timedelta(days=8))}).status_code == 422


def test_export_is_audited(admin, factory):
    d = new_device(admin)
    d.batch([sample(), sample(lat=32.08, lon=34.78, loc_accuracy_m=1500.0)])
    r = admin.get("/api/field/export/measurements.csv", params={"device_id": d.id})
    assert r.status_code == 200 and "filename*=UTF-8''" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert len(lines) == 3 and lines[0].lstrip("﻿").startswith("device_id,ts,lat")
    gj = admin.get("/api/field/export/measurements.geojson").json()
    assert sorted(f["geometry"]["type"] for f in gj["features"]) == ["Point", "Polygon"]
    logs = _audit(factory, "FIELD_EXPORT")
    assert [x["rows"] for x in logs] == [2, 2] and logs[0]["device_id"] == d.id
    v = as_role(factory, UserRole.VIEWER)
    assert v.get("/api/field/export/measurements.csv").status_code == 403
    audit = as_role(factory, UserRole.ADMIN).get("/api/field/audit").json()
    assert {a["action"] for a in audit} >= {"FIELD_EXPORT", "FIELD_DEVICE_ENROLLED", "FIELD_CODE_CREATED"}


def test_timeseries_series_split_and_gaps(admin):
    d = new_device(admin)
    t = (datetime.utcnow() - timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    d.batch([sample(ts=t, rsrp_dbm=-80.0), sample(ts=t + timedelta(seconds=30), network_type="NR_SA", rsrp_dbm=-70.0),
             sample(ts=t + timedelta(minutes=30), service_state="out_of_service", network_type="NONE", rsrp_dbm=None),
             sample(ts=t + timedelta(minutes=31), rsrp_dbm=-90.0)])
    j = admin.get(f"/api/field/devices/{d.id}/timeseries",
                  params={"metric": "rsrp", "from": iso(t), "to": iso(t + timedelta(hours=1))}).json()
    assert [s["meas_tech"] for s in j["series"]] == ["LTE"] and j["unit"] == "dBm"
    assert sorted(p[1] for p in j["series"][0]["points"]) == [-90.0, -80.0]
    reasons = [g[2] for g in j["gaps"]]
    assert reasons == ["no_data", "oos"]
    nr = admin.get(f"/api/field/devices/{d.id}/timeseries",
                   params={"metric": "nr_ss_rsrp", "from": iso(t), "to": iso(t + timedelta(hours=1))}).json()
    assert nr["series"][0]["meas_tech"] == "NR" and nr["series"][0]["points"][0][2] == -70.0


# ── revoke / delete / anonymise / retention ──────────────────────────────────
def test_delete_personal_and_anonymize_org(admin, factory):
    p, o = new_device(admin, "PERSONAL"), new_device(admin, "ORG")
    p.batch([sample(), sample()])
    o.batch([sample(lat=32.08537, lon=34.78183), sample()])
    o.heartbeat()
    _maint(factory)
    r = admin.delete(f"/api/field/devices/{o.id}", params={"mode": "delete"})
    assert (r.status_code, r.json()["detail_code"]) == (422, "org_anonymize_only")
    r = admin.delete(f"/api/field/devices/{o.id}")
    assert r.status_code == 202 and r.json()["data_action"] == "anonymize"
    assert admin.delete(f"/api/field/devices/{p.id}").json()["data_action"] == "delete"
    assert o.heartbeat().status_code == 401 and p.batch([sample()]).status_code == 401
    assert admin.get(f"/api/field/devices/{o.id}").status_code == 404
    _maint(factory, ["purge"])
    db = factory()
    rows = db.query(FieldMeasurement).all()
    assert len(rows) == 2 and all(m.flags & 8 for m in rows)                # ORG rows anonymised, PERSONAL deleted
    sink = db.query(FieldDevice).filter(FieldDevice.is_anon_sink.is_(True)).one()
    assert all(m.device_pk == sink.id and m.ts.minute == 0 and m.ts.second == 0 for m in rows)
    assert all(m.lat != 32.08537 for m in rows)
    assert db.query(FieldHeartbeat).count() == 0
    assert db.query(FieldAggHourly).count() > 0                              # aggregates kept
    db.close()
    assert {x["mode"] for x in _audit(factory, "FIELD_PURGE_DONE")} == {"delete", "anonymize"}
    assert len(_audit(factory, "FIELD_DEVICE_ANONYMIZED")) == 1 and len(_audit(factory, "FIELD_DEVICE_DELETED")) == 1
    j = admin.get("/api/field/devices").json()
    assert j["rows"] == []                                  # tombstones and the anonymous sink are not listed


def test_retention_boundaries(admin, factory):
    d = new_device(admin)
    d.heartbeat()
    now = datetime.utcnow()
    db = factory()
    dev = db.query(FieldDevice).one()
    from app.services.field_ingest import _insert_ignore
    base = {c.name: None for c in FieldMeasurement.__table__.columns}
    _insert_ignore(db, FieldMeasurement.__table__, [
        {**base, "device_pk": dev.id, "ts": now - timedelta(days=days), "sample_id": uuid.uuid4(),
         "received_at": now, "network_type": "LTE", "service_state": "in_service", "meas_tech": "LTE", "flags": 0}
        for days in (91, 90.01, 89)])
    db.query(FieldHeartbeat).update({"received_at": now - timedelta(days=31)})
    db.commit()
    res = retention(db, now)
    assert res["raw"] == 2 and res["heartbeats"] == 1
    assert db.query(FieldMeasurement).count() == 1
    db.close()


# ── codes, groups, requirements ───────────────────────────────────────────────
def test_enrollment_code_qr_and_list(admin, factory):
    c = create_code(admin, "PERSONAL", label="צוות צפון", max_uses=5, ttl_hours=48)
    assert len(c["code"]) == 12 and c["code"][4] == "-" and c["code_hint"] == c["code"].replace("-", "")[-4:]
    assert c["qr_payload"] == f"coverageops-enroll:v1?server=http://localhost:8000&code={c['code']}&ownership=PERSONAL"
    assert c["qr_svg"].startswith("<svg") and "</svg>" in c["qr_svg"]
    lst = admin.get("/api/field/enrollment-codes").json()
    assert lst[0]["code_hint"] == c["code_hint"] and "code" not in lst[0]          # plain code shown once only
    assert admin.post("/api/field/enrollment-codes", json={"ownership": "ANY"}).status_code == 422
    assert admin.post("/api/field/enrollment-codes", json={"ownership": "ORG", "max_uses": 201}).status_code == 422
    admin.delete(f"/api/field/enrollment-codes/{c['id']}")
    assert admin.get("/api/field/enrollment-codes").json() == []
    assert len(admin.get("/api/field/enrollment-codes", params={"include_expired": True}).json()) == 1
    assert len(_audit(factory, "FIELD_CODE_REVOKED")) == 1


def test_groups_crud_and_device_move(admin, factory):
    g = admin.post("/api/field/groups", json={"name": "צפון", "config": {"location_mode": "coarse"}}).json()
    assert g["effective_config"]["location_mode"] == "coarse" and g["device_count"] == 0
    assert admin.post("/api/field/groups", json={"name": "צפון"}).status_code == 409
    d = new_device(admin)
    r = admin.patch(f"/api/field/devices/{d.id}", json={"group_id": g["id"], "label": "חדש"}).json()
    assert r["group"] == "צפון" and r["config_version"] == 2 and r["config"]["location_mode"] == "coarse"
    assert admin.delete(f"/api/field/groups/{g['id']}").status_code == 409
    default = [x for x in admin.get("/api/field/groups").json() if x["is_default"]][0]
    assert admin.delete(f"/api/field/groups/{default['id']}").status_code == 409
    admin.patch(f"/api/field/devices/{d.id}", json={"group_id": None})
    assert admin.delete(f"/api/field/groups/{g['id']}").status_code == 204
    changes = _audit(factory, "FIELD_CONFIG_CHANGED")
    assert any(c.get("device_id") == d.id for c in changes)


def test_device_requirements(factory):
    v = as_role(factory, UserRole.VIEWER)
    j = v.get("/api/field/meta/device-requirements").json()
    assert "Android 11 (API 30)" in j["headline_he"]
    items = [i for s in j["sections"] for i in s["items"]]
    assert {i["status"] for i in items} == {"מאומת באפיון", "לאימות"}
    assert all(i["source"] and i["text_he"] for i in items)
    keys = [s["key"] for s in j["sections"]]
    assert {"os", "hardware", "permissions", "battery", "connectivity", "unsupported"} <= set(keys)


def test_maintenance_endpoint(admin):
    r = admin.post("/api/field/maintenance/run", params={"task": "aggregate,alerts"})
    assert r.status_code == 200 and set(r.json()) == {"aggregate", "alerts"}
    assert admin.post("/api/field/maintenance/run", params={"task": "partitions"}).status_code == 422


def test_alert_rule_update(admin, factory):
    rules = admin.get("/api/field/alert-rules").json()
    rid = [r for r in rules if r["type"] == "low_battery"][0]["id"]
    r = admin.put(f"/api/field/alert-rules/{rid}", json={"enabled": False, "params": {"critical_pct": 3}}).json()
    assert r["enabled"] is False and r["params"]["critical_pct"] == 3 and r["updated_by"] == "admin"
    d = new_device(admin)
    d.heartbeat(battery_pct=2)
    _eval(factory)
    assert ("low_battery", d.id) not in _open(admin)
    db = factory()
    assert db.query(FieldAlert).count() == 0
    db.close()
