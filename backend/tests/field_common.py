"""Shared helpers for the field-reception tests (style of tests/test_cell_sites.py:
SQLite in memory with StaticPool, dependency overrides for get_db/get_current_user).
Device calls go through the REAL device-token path (enroll → Bearer fdt_…)."""
import gzip
import json
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import ratelimit
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.main import app
from app.models.user import User, UserRole
from app.services.field_config import seed_field_defaults

USERS = {UserRole.ADMIN: 1, UserRole.OPERATOR: 2, UserRole.VIEWER: 3}


def make_factory():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    f = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    db = f()
    for role, uid in USERS.items():
        db.add(User(id=uid, username=role.value.lower(), full_name="U", hashed_pw="x", role=role, is_active=True))
    db.commit()
    seed_field_defaults(db)
    db.close()
    return f, eng


@pytest.fixture
def factory():
    ratelimit.reset()
    f, eng = make_factory()
    yield f
    app.dependency_overrides.clear()
    ratelimit.reset()
    eng.dispose()


def use_db(factory):
    def _get_db():
        s = factory()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _get_db


def as_role(factory, role):
    """TestClient acting as a user of `role` (the same TestClient also serves device calls)."""
    use_db(factory)
    app.dependency_overrides[get_current_user] = lambda: User(
        id=USERS[role], username=role.value.lower(), full_name="U", hashed_pw="x", role=role, is_active=True)
    return TestClient(app)


def iso(dt):
    return dt.isoformat(timespec="milliseconds") + "Z"


def create_code(client, ownership="ORG", **kw):
    r = client.post("/api/field/enrollment-codes", json={"ownership": ownership, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def enroll(client, code, ownership=None, device_uuid=None, consent=True, **kw):
    body = {"enrollment_code": code, "device_uuid": str(device_uuid or uuid.uuid4()), "model": "Pixel 8",
            "manufacturer": "Google", "os_version": "14 (API 34)", "app_version": "1.0.0 (100)"}
    if ownership:
        body["ownership"] = ownership
    if consent:
        body["consent_version"] = "he-1.0"
        body["consented_at"] = iso(datetime.utcnow())
    body.update(kw)
    return client.post("/api/field/devices/enroll", json=body)


class Device:
    def __init__(self, client, device_id, token, ownership):
        self.client, self.id, self.token, self.ownership = client, device_id, token, ownership
        self.h = {"Authorization": f"Bearer {token}"}

    def batch(self, samples, gz=False, device_id=None, sent_at=None, raw=None):
        body = raw if raw is not None else {"device_id": device_id or self.id,
                                            "sent_at": sent_at or iso(datetime.utcnow()),
                                            "config_version": 1, "samples": samples}
        data = json.dumps(body).encode()
        headers = {**self.h, "Content-Type": "application/json"}
        if gz:
            data = gzip.compress(data)
            headers["Content-Encoding"] = "gzip"
        return self.client.post("/api/field/measurements/batch", content=data, headers=headers)

    def heartbeat(self, **kw):
        body = {"ts": iso(datetime.utcnow()), "battery_pct": 80, "is_charging": False, "app_version": "1.0.0",
                "queue_size": 0, "monitoring_state": "active",
                "permissions": {"location": "precise", "background_location": True, "phone_state": True,
                                "notifications": True, "battery_optimization_exempt": True}}
        body.update(kw)
        return self.client.post("/api/field/devices/heartbeat", json=body, headers=self.h)


def new_device(client, ownership="ORG", label=None):
    c = create_code(client, ownership)
    r = enroll(client, c["code"], label=label)
    assert r.status_code == 201, r.text
    j = r.json()
    return Device(client, j["device_id"], j["device_token"], ownership)


def sample(ts=None, **kw):
    ts = ts if isinstance(ts, str) else iso(ts or datetime.utcnow() - timedelta(seconds=5))
    s = {"sample_id": str(uuid.uuid4()), "ts": ts,
         "lat": 32.0853, "lon": 34.7818, "loc_accuracy_m": 8.0, "loc_provider": "fused", "loc_age_ms": 1200,
         "network_type": "LTE", "mcc": "425", "mnc": "01", "operator_name": "Partner", "sim_slot": 0,
         "is_roaming": False, "cell_id": 123456, "pci": 101, "tac": 3001, "earfcn_arfcn": 1300, "band": 3,
         "rsrp_dbm": -92.0, "rsrq_db": -10.0, "sinr_db": 12.0, "rssi_dbm": None, "signal_level": 3,
         "cell_info_age_ms": 800, "data_state": "connected", "service_state": "in_service",
         "neighbors": [{"pci": 102, "earfcn_arfcn": 1300, "rsrp_dbm": -101.0, "rsrq_db": -14.0, "rat": "LTE"}],
         "battery_pct": 77, "is_charging": False, "screen_on": False, "trigger": "periodic"}
    s.update(kw)
    return s
