"""Field probe device API — enroll / measurements batch / heartbeat / config /
withdraw (contract v2 + v2.1), through the real device-token path."""
import gzip
import json
import re
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from app.core import ratelimit
from app.core.database import get_db
from app.main import app
from app.models.field import (
    FieldAggDirty, FieldDevice, FieldDeviceCommand, FieldEnrollmentCode, FieldHeartbeat, FieldMeasurement,
)
from app.models.user import ActivityLog, UserRole
from app.services.field_maintenance import run_maintenance
from app.services.field_quality import REJECT_REASON_RE
from tests.field_common import (  # noqa: F401  (factory is a fixture)
    Device, as_role, create_code, enroll, factory, iso, new_device, sample,
)


@pytest.fixture
def admin(factory):
    return as_role(factory, UserRole.ADMIN)


def _dev(factory, public_id):
    db = factory()
    try:
        return db.query(FieldDevice).filter(FieldDevice.public_id == uuid.UUID(public_id)).one()
    finally:
        db.close()


# ── enroll ────────────────────────────────────────────────────────────────────
def test_enroll_ok_returns_code_ownership_config_and_audit(admin, factory):
    c = create_code(admin, "ORG")
    r = enroll(admin, c["code"].lower().replace("-", " "))          # normalised: case / dashes / spaces
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["ownership"] == "ORG" and j["device_token"].startswith("fdt_") and len(j["device_token"]) == 47
    assert j["config"]["config_version"] == 1 and j["config"]["sampling_interval_s"] == 30
    d = _dev(factory, j["device_id"])
    assert d.token_hash and j["device_token"] not in (d.token_hash or "")        # stored hashed only
    db = factory()
    log = db.query(ActivityLog).filter(ActivityLog.action == "FIELD_DEVICE_ENROLLED").one()
    assert json.loads(log.detail)["device_id"] == j["device_id"] and log.user_id == 1
    db.close()


def test_enroll_error_codes(admin, factory):
    assert enroll(admin, "NOPE-NOPE-NO").json()["detail_code"] == "code_invalid"
    assert enroll(admin, "NOPE-NOPE-NO").status_code == 400
    # revoked
    c = create_code(admin, "ORG")
    assert admin.delete(f"/api/field/enrollment-codes/{c['id']}").status_code == 204
    r = enroll(admin, c["code"])
    assert (r.status_code, r.json()["detail_code"]) == (400, "code_invalid")
    # expired
    c = create_code(admin, "ORG")
    db = factory()
    db.query(FieldEnrollmentCode).filter_by(id=c["id"]).update(
        {"expires_at": datetime.utcnow() - timedelta(seconds=1)})
    db.commit()
    db.close()
    r = enroll(admin, c["code"])
    assert (r.status_code, r.json()["detail_code"]) == (410, "code_expired")
    # used
    c = create_code(admin, "ORG")
    assert enroll(admin, c["code"]).status_code == 201
    r = enroll(admin, c["code"])
    assert (r.status_code, r.json()["detail_code"]) == (409, "code_used")
    ratelimit.reset()                     # 5 failures per IP would now be blocked (tested separately)
    # ownership mismatch
    c = create_code(admin, "ORG")
    r = enroll(admin, c["code"], ownership="PERSONAL")
    assert (r.status_code, r.json()["detail_code"]) == (409, "ownership_mismatch")
    # PERSONAL without consent
    c = create_code(admin, "PERSONAL")
    r = enroll(admin, c["code"], consent=False)
    assert (r.status_code, r.json()["detail_code"]) == (422, "consent_required")
    r = enroll(admin, c["code"], consent_version="he-1", consented_at=iso(datetime.utcnow() + timedelta(hours=1)))
    assert (r.status_code, r.json()["detail_code"]) == (422, "consent_required")
    r = enroll(admin, c["code"])
    assert r.status_code == 201 and r.json()["ownership"] == "PERSONAL"
    d = _dev(factory, r.json()["device_id"])
    assert d.consent_version == "he-1.0" and d.consented_at is not None
    # envelope error, and an identifier field is refused (privacy guard)
    assert admin.post("/api/field/devices/enroll", json={"enrollment_code": "X"}).status_code == 422
    c = create_code(admin, "ORG")
    assert enroll(admin, c["code"], imei="123456789012345").status_code == 422


def test_enroll_retry_is_idempotent_within_15_min(admin, factory):
    c = create_code(admin, "ORG")
    du = uuid.uuid4()
    r1 = enroll(admin, c["code"], device_uuid=du)
    r2 = enroll(admin, c["code"], device_uuid=du)
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["device_id"] == r2.json()["device_id"]
    assert r1.json()["device_token"] != r2.json()["device_token"]
    db = factory()
    assert db.get(FieldEnrollmentCode, c["id"]).use_count == 1
    db.close()
    old = Device(admin, r1.json()["device_id"], r1.json()["device_token"], "ORG")
    new = Device(admin, r2.json()["device_id"], r2.json()["device_token"], "ORG")
    assert old.heartbeat().status_code == 401                    # previous token invalidated
    assert new.heartbeat().status_code == 200
    # once the token was used, the same (code, uuid) is no longer a retry
    r3 = enroll(admin, c["code"], device_uuid=du)
    assert (r3.status_code, r3.json()["detail_code"]) == (409, "code_used")


def test_enroll_retry_after_15_min_is_code_used(admin, factory):
    c = create_code(admin, "ORG")
    du = uuid.uuid4()
    r1 = enroll(admin, c["code"], device_uuid=du)
    db = factory()
    db.query(FieldDevice).filter_by(public_id=uuid.UUID(r1.json()["device_id"])).update(
        {"enrolled_at": datetime.utcnow() - timedelta(minutes=16)})
    db.commit()
    db.close()
    assert enroll(admin, c["code"], device_uuid=du).json()["detail_code"] == "code_used"


def test_concurrent_enroll_one_time_code_single_winner(admin, factory, monkeypatch):
    """Race: the pre-check still sees a free code, but a concurrent enroll took it
    first — the atomic `UPDATE … WHERE use_count < max_uses` decides (→ 409)."""
    from app.api import field_ingest as fi
    c = create_code(admin, "ORG")
    real_update = fi.update

    def racy_update(model):
        s = factory()                          # the concurrent winner commits first
        s.query(FieldEnrollmentCode).filter_by(id=c["id"]).update({"use_count": 1})
        s.commit()
        s.close()
        return real_update(model)

    monkeypatch.setattr(fi, "update", racy_update)
    r = enroll(admin, c["code"])
    assert (r.status_code, r.json()["detail_code"]) == (409, "code_used")
    db = factory()
    assert db.query(FieldDevice).count() == 0 and db.get(FieldEnrollmentCode, c["id"]).use_count == 1
    db.close()


def test_enroll_rate_limit_after_failures(admin):
    codes = [enroll(admin, f"BAD{i}").status_code for i in range(6)]
    assert codes[:5] == [400] * 5 and codes[5] == 429


# ── auth separation & DB failure ──────────────────────────────────────────────
def test_token_types_rejected_on_the_other_side(admin, factory):
    d = new_device(admin)
    from app.core.security import create_access_token, get_current_user
    jwt = create_access_token({"sub": "1"})
    r = admin.post("/api/field/measurements/batch", json={}, headers={"Authorization": f"Bearer {jwt}"})
    assert r.status_code == 401 and r.json()["detail_code"] == "token_invalid"
    assert admin.post("/api/field/devices/heartbeat", json={}).status_code == 401      # no token at all
    app.dependency_overrides.pop(get_current_user)                      # real user auth for /api/sites
    assert admin.get("/api/sites/", headers=d.h).status_code == 401
    assert admin.get("/api/field/summary", headers=d.h).status_code == 401


def test_revoked_token_401(admin, factory):
    d = new_device(admin)
    assert admin.post(f"/api/field/devices/{d.id}/revoke").json() == {"status": "revoked"}
    r = d.heartbeat()
    assert r.status_code == 401 and r.json()["detail_code"] == "token_invalid"


def test_db_failure_in_device_auth_is_503_not_401(admin, factory):
    d = new_device(admin)

    class Broken:
        def query(self, *a, **k):
            raise OperationalError("SELECT 1", {}, Exception("db down"))

        def rollback(self):
            pass

    app.dependency_overrides[get_db] = lambda: Broken()
    r = d.batch([sample()])
    assert r.status_code == 503 and r.headers["Retry-After"] == "30"
    assert d.heartbeat().status_code == 503


# ── batch ─────────────────────────────────────────────────────────────────────
def test_batch_gzip_plain_idempotent_and_invariant(admin, factory):
    d = new_device(admin)
    ss = [sample(), sample(), sample()]
    r = d.batch(ss, gz=True)
    assert r.status_code == 200, r.text
    assert r.json() == {"accepted": 3, "duplicates": 0, "rejected": [], "normalized": 0, "config_version": 1}
    r = d.batch(ss + [ss[0]])                                 # plain JSON, all duplicates (+ repeat in batch)
    assert r.json()["accepted"] == 0 and r.json()["duplicates"] == 4
    db = factory()
    assert db.query(FieldMeasurement).count() == 3
    m = db.query(FieldMeasurement).first()
    assert (m.tier, m.meas_tech, m.operator_key) == (3, "LTE", "425-01") and m.gx is not None
    assert db.query(FieldAggDirty).count() >= 2              # g100 + g2500 buckets marked
    db.close()


def test_batch_rejections_are_from_the_closed_list(admin):
    d = new_device(admin)
    now = datetime.utcnow()
    ss = [sample(sample_id="not-a-uuid"), sample(ts="yesterday"), sample(ts=iso(now + timedelta(minutes=10))),
          sample(ts=iso(now - timedelta(days=91))), sample(network_type="6G"), sample(service_state=None),
          sample(trigger="boom"), sample(neighbors=[{"pci": 1, "rat": "WIFI"}]), sample(lat=95.0),
          sample(lat=None, lon=34.8), sample(), sample(rsrp_dbm=-20.0, rsrq_db=99)]
    r = d.batch(ss)
    j = r.json()
    reasons = [x["reason"] for x in j["rejected"]]
    assert reasons == ["invalid_sample_id", "invalid_ts", "ts_in_future", "ts_too_old",
                       "invalid_enum:network_type", "invalid_enum:service_state", "invalid_enum:trigger",
                       "invalid_enum:neighbors.rat", "out_of_range:lat", "out_of_range:lat"]
    assert all(re.match(REJECT_REASON_RE, x) for x in reasons)
    assert j["accepted"] + j["duplicates"] + len(j["rejected"]) == len(ss)
    assert j["accepted"] == 2 and j["normalized"] == 1        # radio values out of range → nulled, accepted


def test_out_of_range_radio_values_are_nulled(admin, factory):
    d = new_device(admin)
    s = sample(rsrp_dbm=-160.0, sinr_db=55.0, battery_pct=140, timing_advance=5000)
    assert d.batch([s]).json()["normalized"] == 1
    db = factory()
    m = db.query(FieldMeasurement).one()
    assert (m.rsrp_dbm, m.sinr_db, m.battery_pct, m.timing_advance) == (None, None, None, None)
    assert m.rsrq_db == -10.0 and m.flags & 512 and m.tier is None
    db.close()


def test_sentinel_is_null_not_rejection(admin, factory):
    d = new_device(admin)
    r = d.batch([sample(rsrp_dbm=2147483647, cell_id=9223372036854775807)]).json()
    assert r["accepted"] == 1 and r["normalized"] == 0
    db = factory()
    m = db.query(FieldMeasurement).one()
    assert m.rsrp_dbm is None and m.cell_id is None and m.flags & 128 and m.tier is None
    db.close()


def test_envelope_errors(admin):
    d = new_device(admin)
    assert d.batch([sample()] * 501).status_code == 413
    assert d.batch([]).status_code == 422
    assert d.batch(None, raw={"device_id": d.id, "sent_at": "x", "config_version": 1, "samples": [sample()]}) \
        .status_code == 422
    assert d.batch([sample(imei="35")]).status_code == 422               # unknown field → whole batch 422
    r = d.batch([sample()], device_id=str(uuid.uuid4()))
    assert (r.status_code, r.json()["detail_code"]) == (403, "device_mismatch")
    big = gzip.compress(b'{"device_id":"' + d.id.encode() + b'","samples":["' + b"a" * 6_000_000 + b'"]}')
    r = admin.post("/api/field/measurements/batch", content=big,
                   headers={**d.h, "Content-Encoding": "gzip", "Content-Type": "application/json"})
    assert r.status_code == 413                                          # decompressed size cap
    r = admin.post("/api/field/measurements/batch", content=b"not gzip",
                   headers={**d.h, "Content-Encoding": "gzip"})
    assert r.status_code == 422


def test_staleness_flags_and_filters(admin, factory):
    """v2.1 rule 3: stale = cell_info_age_ms > 10000; stored raw, flagged; null age → age_unknown."""
    d = new_device(admin)
    t = datetime.utcnow() - timedelta(minutes=2)
    d.batch([sample(ts=t, cell_info_age_ms=10000, rsrp_dbm=-80.0),
             sample(ts=t + timedelta(seconds=30), cell_info_age_ms=10001, rsrp_dbm=-120.0),
             sample(ts=t + timedelta(seconds=60), cell_info_age_ms=None, rsrp_dbm=-90.0)])
    db = factory()
    rows = {m.rsrp_dbm: m for m in db.query(FieldMeasurement).all()}
    assert not rows[-80.0].flags & 16 and rows[-120.0].flags & 16 and rows[-90.0].flags & 256
    assert rows[-120.0].tier == 0                          # classification is pure; filtering happens on use
    dev = db.query(FieldDevice).one()
    assert dev.last_rsrp_dbm == -90.0 and dev.last_tier == 3 and dev.last_tier_stale is False
    db.close()
    run_maintenance(factory(), ["aggregate"])
    s = admin.get("/api/field/summary").json()["kpis"]
    assert s["rsrp"]["lte"]["n"] == 2 and -120.0 not in (s["rsrp"]["lte"]["p5"], s["rsrp"]["lte"]["p50"])
    assert s["stale_excluded_pct"] == pytest.approx(33.3, abs=0.1)
    assert s["age_unknown_pct"] == pytest.approx(33.3, abs=0.1)
    from app.models.field import FieldAggHourly
    db = factory()
    h = db.query(FieldAggHourly).filter(FieldAggHourly.meas_tech == "LTE").all()
    assert sum(r.n for r in h) == 3 and sum(r.n_rsrp for r in h) == 2 and sum(r.n_stale_excluded for r in h) == 1
    db.close()


def test_stale_oos_is_still_counted_as_oos(admin):
    """v2.1 rule 3 — service_state and time without service are always counted."""
    d = new_device(admin)
    t0 = datetime.utcnow() - timedelta(minutes=10)
    d.batch([sample(ts=t0, service_state="out_of_service", network_type="NONE", rsrp_dbm=None,
                    cell_info_age_ms=60000),
             sample(ts=t0 + timedelta(seconds=120), rsrp_dbm=-80.0)])
    k = admin.get("/api/field/summary", params={"from": iso(t0), "to": iso(t0 + timedelta(seconds=240))}).json()
    assert k["kpis"]["oos_minutes"] == 2.0


def test_coarse_and_old_location_grid(admin, factory):
    d = new_device(admin, "PERSONAL")
    d.batch([sample(lat=32.08, lon=34.78, loc_accuracy_m=1000.0, timing_advance=12, speed_mps=10.0),
             sample(lat=32.0853, lon=34.7818, loc_accuracy_m=10.0, loc_age_ms=30001),
             sample(lat=32.0853, lon=34.7818, loc_accuracy_m=150.0)])
    db = factory()
    rows = db.query(FieldMeasurement).order_by(FieldMeasurement.ts).all()
    coarse = [m for m in rows if m.flags & 32][0]
    assert coarse.gx is None and coarse.gx25 is not None and coarse.timing_advance is None and coarse.speed_mps is None
    old = [m for m in rows if m.flags & 64][0]
    assert old.gx is None and old.gx25 is None
    acc150 = [m for m in rows if m.loc_accuracy_m == 150.0][0]
    assert acc150.gx is None and acc150.gx25 is not None
    dev = db.query(FieldDevice).one()
    assert dev.last_coarse_lat == 32.08 and dev.last_lat == 32.0853     # coarse kept apart from precise
    db.close()


def test_nsa_tier_from_anchor_and_nr_leg_stored(admin, factory):
    d = new_device(admin)
    d.batch([sample(network_type="NR_NSA", rsrp_dbm=-110.0,
                    nr_leg={"pci": 7, "arfcn": 636666, "band": 78, "ss_rsrp_dbm": -80.0, "ss_rsrq_db": -10.0,
                            "ss_sinr_db": 15.0})])
    db = factory()
    m = db.query(FieldMeasurement).one()
    assert (m.tier, m.meas_tech, m.nr_ss_rsrp_dbm, m.nr_band) == (1, "LTE", -80.0, 78)
    dev = db.query(FieldDevice).one()
    assert dev.last_rsrp_dbm == -110.0 and dev.last_nr_ss_rsrp_dbm == -80.0 and dev.last_nr_leg["band"] == 78
    db.close()


def test_last_state_only_from_newer_samples(admin, factory):
    d = new_device(admin)
    t = datetime.utcnow() - timedelta(minutes=5)
    d.batch([sample(ts=t, rsrp_dbm=-80.0)])
    d.batch([sample(ts=t - timedelta(minutes=2), rsrp_dbm=-120.0)])   # late (offline queue) sample
    assert _dev(factory, d.id).last_rsrp_dbm == -80.0


def test_batch_rate_limit(admin):
    d = new_device(admin)
    codes = [d.batch([sample()]).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    r = d.batch([sample()])
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    ratelimit.reset()
    assert d.batch([sample()]).status_code == 200


# ── heartbeat / commands ──────────────────────────────────────────────────────
def test_heartbeat_updates_state_and_response_shape(admin, factory):
    d = new_device(admin)
    r = d.heartbeat(monitoring_state="degraded", degraded_reasons=["background_location_missing", "new_code_x"],
                    effective_location_mode="coarse", applied_config_version=1, evicted_samples_total=5,
                    location_services_enabled=True, last_sample_ts=iso(datetime.utcnow()), queue_size=12)
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == {"config_version", "server_time", "commands"} and j["commands"] == []
    assert j["server_time"].endswith("Z")
    dev = _dev(factory, d.id)
    assert dev.last_monitoring_state == "degraded" and dev.last_degraded_reasons[1] == "new_code_x"
    assert dev.last_effective_location_mode == "coarse" and dev.last_queue_size == 12
    assert dev.last_permissions["location"] == "precise"
    det = admin.get(f"/api/field/devices/{d.id}").json()
    assert det["degraded_reasons"][1] == {"code": "new_code_x", "label_he": "סיבה לא מוכרת: new_code_x",
                                          "known": False}
    db = factory()
    assert db.query(FieldHeartbeat).count() == 1
    db.close()


def test_heartbeat_validation(admin):
    d = new_device(admin)
    perms = {"location": True, "background_location": True, "phone_state": True, "notifications": True,
             "battery_optimization_exempt": True}
    assert d.heartbeat(permissions=perms).status_code == 422          # v2: location is an enum, not bool
    assert d.heartbeat(monitoring_state="sleeping").status_code == 422
    assert d.heartbeat(acked_commands=[str(uuid.uuid4())]).status_code == 422     # [id] form dropped (v2.1)
    assert d.heartbeat(acked_commands=[{"id": str(uuid.uuid4()), "status": "unsupported"}]).status_code == 422


def test_command_lifecycle(admin, factory):
    d = new_device(admin)
    other = new_device(admin)
    op = as_role(factory, UserRole.OPERATOR)
    r = op.post(f"/api/field/devices/{d.id}/commands", json={"type": "sample_now"})
    assert r.status_code == 201, r.text
    cmd = r.json()
    assert cmd["status"] == "queued" and "warning_he" not in cmd
    r = op.post(f"/api/field/devices/{d.id}/commands", json={"type": "sample_now"})
    assert (r.status_code, r.json()["detail_code"]) == (409, "command_pending")
    viewer = as_role(factory, UserRole.VIEWER)
    assert viewer.post(f"/api/field/devices/{d.id}/commands", json={"type": "upload_now"}).status_code == 403
    admin2 = as_role(factory, UserRole.ADMIN)
    up = admin2.post(f"/api/field/devices/{d.id}/commands", json={"type": "upload_now"}).json()
    assert admin2.delete(f"/api/field/commands/{up['id']}").json()["status"] == "cancelled"
    # delivered in the heartbeat response, then acked
    hb = d.heartbeat().json()
    assert [c["id"] for c in hb["commands"]] == [cmd["id"]] and hb["commands"][0]["type"] == "sample_now"
    assert d.heartbeat().json()["commands"][0]["id"] == cmd["id"]                # re-delivered until acked
    assert admin2.delete(f"/api/field/commands/{cmd['id']}").json()["detail_code"] == "command_not_cancellable"
    other.heartbeat(acked_commands=[{"id": cmd["id"], "status": "done"}])      # another device: ignored
    lst = admin2.get(f"/api/field/devices/{d.id}/commands").json()
    assert {c["id"]: c["status"] for c in lst}[cmd["id"]] == "delivered"
    hb = d.heartbeat(acked_commands=[{"id": cmd["id"], "status": "skipped_paused"}]).json()
    assert hb["commands"] == []
    lst = {c["id"]: c for c in admin2.get(f"/api/field/devices/{d.id}/commands").json()}
    assert lst[cmd["id"]]["status"] == "skipped_paused" and lst[cmd["id"]]["issued_by"] == "operator"
    assert lst[cmd["id"]]["acked_at"] and lst[up["id"]]["status"] == "cancelled"
    db = factory()
    issued = [json.loads(a.detail) for a in db.query(ActivityLog).filter(ActivityLog.action == "FIELD_COMMAND_ISSUED")]
    by = {a.user_id for a in db.query(ActivityLog).filter(ActivityLog.action == "FIELD_COMMAND_ISSUED")}
    assert {i["type"] for i in issued} == {"sample_now", "upload_now"} and by == {1, 2}
    assert db.query(ActivityLog).filter(ActivityLog.action == "FIELD_COMMAND_CANCELLED").count() == 1
    db.close()


def test_command_expiry_and_paused_warning(admin, factory):
    d = new_device(admin)
    d.heartbeat(monitoring_state="paused_by_user")
    r = admin.post(f"/api/field/devices/{d.id}/commands", json={"type": "upload_now"}).json()
    assert "skipped_paused" in r["warning_he"]
    db = factory()
    db.query(FieldDeviceCommand).update({"expires_at": datetime.utcnow() - timedelta(seconds=1)})
    db.commit()
    db.close()
    assert d.heartbeat(monitoring_state="paused_by_user").json()["commands"] == []    # expired: not returned
    run_maintenance(factory(), ["commands"])
    lst = admin.get(f"/api/field/devices/{d.id}/commands").json()
    assert lst[0]["status"] == "expired"
    alerts = admin.get("/api/field/alerts", params={"status": "resolved", "type": "command_expired"}).json()
    assert len(alerts) == 1
    # inactive device
    admin.post(f"/api/field/devices/{d.id}/revoke")
    r = admin.post(f"/api/field/devices/{d.id}/commands", json={"type": "upload_now"})
    assert (r.status_code, r.json()["detail_code"]) == (409, "device_inactive")


# ── config ────────────────────────────────────────────────────────────────────
def test_config_etag_and_merge(admin, factory):
    d = new_device(admin, "PERSONAL")
    r = admin.get("/api/field/devices/me/config", headers=d.h)
    assert r.status_code == 200 and r.headers["ETag"] == '"1"' and r.json()["allow_user_pause"] is True
    assert admin.get("/api/field/devices/me/config", headers={**d.h, "If-None-Match": '"1"'}).status_code == 304
    gid = admin.get("/api/field/groups").json()[0]["id"]
    r = admin.put(f"/api/field/groups/{gid}", json={"config": {"upload_interval_s": 300, "allow_user_pause": False}})
    assert r.status_code == 200, r.text
    assert admin.patch(f"/api/field/devices/{d.id}", json={"config_override": {"sampling_interval_s": 10}}) \
        .status_code == 200
    cfg = admin.get("/api/field/devices/me/config", headers=d.h).json()
    assert (cfg["config_version"], cfg["upload_interval_s"], cfg["sampling_interval_s"]) == (3, 300, 10)
    assert cfg["allow_user_pause"] is True                                   # PERSONAL: always true
    assert d.batch([sample()]).json()["config_version"] == 3
    assert d.heartbeat().json()["config_version"] == 3
    bad = admin.put(f"/api/field/groups/{gid}", json={"config": {"sampling_interval_s": 2}})
    assert bad.status_code == 422
    bad = admin.put(f"/api/field/groups/{gid}",
                    json={"config": {"schedule": [{"days": [0, 0], "start": "08:00", "end": "17:00"}]}})
    assert bad.status_code == 422
    ok = admin.put(f"/api/field/groups/{gid}",
                   json={"config": {"schedule": [{"days": [0, 1, 2, 3, 4], "start": "22:00", "end": "06:00"}]}})
    assert ok.status_code == 200


# ── withdraw ──────────────────────────────────────────────────────────────────
def test_withdraw_personal_deletes_after_purge(admin, factory):
    d = new_device(admin, "PERSONAL")
    d.batch([sample(), sample()])
    d.heartbeat()
    admin.post(f"/api/field/devices/{d.id}/commands", json={"type": "sample_now"})
    r = admin.post("/api/field/devices/me/withdraw", json={"reason": "עזבתי"}, headers=d.h)
    assert r.status_code == 200 and r.json()["status"] == "withdrawn" and r.json()["data_action"] == "delete"
    assert r.json()["completes_by"].endswith("Z")
    assert d.batch([sample()]).status_code == 401
    assert admin.post("/api/field/devices/me/withdraw", headers=d.h).status_code == 401
    run_maintenance(factory(), ["aggregate"])
    assert admin.get("/api/field/summary").json()["kpis"]["samples"] == 0       # hidden before the purge
    db = factory()
    assert db.query(FieldDeviceCommand).one().status == "cancelled"
    db.close()
    run_maintenance(factory(), ["purge"])
    db = factory()
    dev = db.query(FieldDevice).one()
    assert db.query(FieldMeasurement).count() == 0 and db.query(FieldHeartbeat).count() == 0
    assert dev.status == "withdrawn" and dev.label is None and dev.model is None and dev.purged_at
    acts = [a.action for a in db.query(ActivityLog).all()]
    assert "FIELD_DEVICE_WITHDRAWN" in acts and "FIELD_PURGE_DONE" in acts
    w = [a for a in db.query(ActivityLog).filter(ActivityLog.action == "FIELD_DEVICE_WITHDRAWN")][0]
    assert w.user_id is None
    db.close()


def test_withdraw_org_is_forbidden(admin):
    d = new_device(admin, "ORG")
    r = admin.post("/api/field/devices/me/withdraw", headers=d.h)
    assert (r.status_code, r.json()["detail_code"]) == (403, "withdraw_not_allowed")
    assert d.heartbeat().status_code == 200
