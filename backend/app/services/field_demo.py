"""
Field reception — DEMO data (synthetic, clearly labelled) and the signal model
shared with the HTTP simulator (app/scripts/field_simulator.py).

seed_demo() fills the database with ~20 demo devices in group "דמו" (labels
start with "דמו · "), ingesting through the same code path as the real API
(services/field_ingest) so every derived column is computed exactly as for real
data. It is idempotent: an existing demo fleet is removed first, then rebuilt
with history ending "now". remove_demo() deletes it (devices in group "דמו"
plus the fixed demo device UUIDs) and recomputes the affected aggregates.

NOTHING here is real measurement data. Routes are hand-drawn approximations of
real roads; the RSRP field is a deterministic synthetic model.
"""
import hashlib
import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.field import (
    FieldAggDirty, FieldAlert, FieldDevice, FieldDeviceCommand, FieldDeviceGroup, FieldHeartbeat,
    FieldMeasurement,
)
from app.services.field_config import bump_device, seed_field_defaults
from app.models.user import User, UserRole
from app.services.field_ingest import apply_heartbeat, ingest_batch, mark_dirty

DEMO_GROUP = "דמו"
DEMO_LABEL_PREFIX = "דמו · "
DEMO_NS = uuid.UUID("6f1c0e52-3c0b-4c8e-9a43-2f8a2b0d7e11")

# ── routes (lat, lon) — approximate polylines of real roads ──────────────────
ROUTES = {
    "r1": [(32.0636, 34.7920), (32.0420, 34.8020), (32.0180, 34.8340), (31.9980, 34.8790), (31.9580, 34.9350),
           (31.9000, 34.9620), (31.8380, 34.9800), (31.8157, 35.0216), (31.8080, 35.0800), (31.7990, 35.1500),
           (31.7930, 35.1680), (31.7880, 35.2050)],                                      # כביש 1 ת"א–ירושלים
    "r6": [(32.4300, 35.0100), (32.3400, 34.9750), (32.2350, 34.9850), (32.1300, 34.9600), (32.0500, 34.9530),
           (31.9600, 34.9450), (31.8800, 34.9300), (31.7700, 34.8700)],                  # כביש 6 מרכז
    "r90": [(29.5600, 34.9520), (29.7050, 34.9930), (29.8930, 35.0590), (29.9700, 35.0640), (30.0900, 35.1000),
            (30.2000, 35.1300), (30.3650, 35.1500), (30.5500, 35.1800)],                 # כביש 90 ערבה
    "r90dz": [(30.0000, 35.0800), (30.0900, 35.1000), (30.2000, 35.1300), (30.3300, 35.1460)],   # קטע 90 עם אזור מת
    "haifa": [(32.8190, 34.9990), (32.8130, 34.9880), (32.8050, 34.9830), (32.7940, 34.9890), (32.7880, 35.0060),
              (32.7990, 35.0150), (32.8100, 35.0080), (32.8190, 34.9990)],               # חיפה עירוני
    "tlv": [(32.0853, 34.7818), (32.0780, 34.7740), (32.0700, 34.7680), (32.0640, 34.7710), (32.0630, 34.7830),
            (32.0720, 34.7890), (32.0853, 34.7818)],                                     # ת"א עירוני
}
CITIES = [(32.0800, 34.7800), (31.7800, 35.2100), (32.8000, 35.0000), (31.2520, 34.7910), (29.5580, 34.9480),
          (32.3200, 34.8600), (31.9000, 34.8100)]
DEAD_ZONE = (30.10, 30.25)            # Arava stretch of route 90 — no coverage for any operator

OPERATORS = {
    "01": {"name": "Partner", "earfcn": 1300, "band": 3, "offset": 1.5, "phase": 0.3},
    "02": {"name": "Cellcom", "earfcn": 3100, "band": 7, "offset": -1.0, "phase": 1.7},
    "03": {"name": "Pelephone", "earfcn": 9310, "band": 28, "offset": 0.0, "phase": 2.9},
}


def _hav_km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def city_km(lat: float, lon: float) -> float:
    return min(_hav_km((lat, lon), c) for c in CITIES)


def signal(lat: float, lon: float, mnc: str, rng: random.Random) -> Optional[float]:
    """Synthetic RSRP (dBm) — None = no service. Deterministic field + small noise."""
    op = OPERATORS[mnc]
    d = city_km(lat, lon)
    x = -70.0 - 19.0 * math.log10(1.0 + d) + op["offset"]
    x += 6.0 * math.sin(lat * 157.0 + op["phase"]) * math.cos(lon * 131.0 + op["phase"])
    if mnc == "03" and _hav_km((lat, lon), (31.8157, 35.0216)) < 1.6:       # Sha'ar HaGai dip (Pelephone)
        x -= 14.0
    if DEAD_ZONE[0] <= lat <= DEAD_ZONE[1] and 34.9 <= lon <= 35.3:
        x -= 28.0
    x += rng.gauss(0, 2.5)
    if x < -126:
        return None
    return round(max(-140.0, min(-44.0, x)), 1)


def cell_for(lat: float, lon: float, mnc: str) -> dict:
    gx, gy = int(lon / 0.02), int(lat / 0.02)
    h = int(hashlib.sha1(f"{mnc}:{gx}:{gy}".encode()).hexdigest()[:7], 16)
    enb = 10000 + h % 90000
    op = OPERATORS[mnc]
    return {"cell_id": enb * 256 + (h % 3) + 1, "pci": h % 504, "tac": 3000 + (h % 400),
            "earfcn_arfcn": op["earfcn"], "band": op["band"]}


class Route:
    def __init__(self, pts):
        self.pts = pts
        self.cum = [0.0]
        for a, b in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + _hav_km(a, b) * 1000.0)
        self.length = self.cum[-1]

    def at(self, dist_m: float) -> tuple:
        """Position (lat, lon, bearing) at a distance travelled back and forth along the route."""
        L = self.length
        s = dist_m % (2 * L)
        back = s > L
        if back:
            s = 2 * L - s
        i = max(0, min(len(self.cum) - 2, next((k for k in range(len(self.cum) - 1) if self.cum[k + 1] >= s),
                                                len(self.cum) - 2)))
        seg = self.cum[i + 1] - self.cum[i] or 1.0
        f = (s - self.cum[i]) / seg
        a, b = self.pts[i], self.pts[i + 1]
        lat, lon = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        brg = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360
        return lat, lon, (brg + 180) % 360 if back else brg


def make_sample(ts: datetime, lat: Optional[float], lon: Optional[float], mnc: str, rng: random.Random, *,
                sim_slot: int = 0, nr: str = "auto", coarse: bool = False, speed: float = 0.0,
                bearing: Optional[float] = None, battery: int = 80, charging: bool = False,
                trigger: str = "periodic", stale_p: float = 0.06) -> dict:
    """One contract Sample (v2) from the synthetic model. nr: auto | sa | never | umts."""
    op = OPERATORS[mnc]
    rsrp = signal(lat, lon, mnc, rng) if lat is not None else signal(31.9, 34.9, mnc, rng)
    d_city = city_km(lat, lon) if lat is not None else 99
    cell = cell_for(lat or 31.9, lon or 34.9, mnc)
    s = {"sample_id": str(uuid.uuid4()), "ts": ts.isoformat(timespec="milliseconds") + "Z",
         "loc_provider": "fused", "mcc": "425", "mnc": mnc, "operator_name": op["name"], "sim_slot": sim_slot,
         "is_roaming": False, "data_state": "connected" if sim_slot == 0 else "disconnected",
         "battery_pct": battery, "is_charging": charging, "screen_on": False, "trigger": trigger,
         "signal_level": None, "neighbors": []}
    r = rng.random()
    s["cell_info_age_ms"] = None if r < 0.03 else (rng.randint(12000, 40000) if r < 0.03 + stale_p
                                                     else rng.randint(100, 3500))
    if lat is not None:
        if coarse:
            s.update(lat=round(round(lat / 0.01) * 0.01, 2), lon=round(round(lon / 0.01) * 0.01, 2),
                     loc_accuracy_m=1500.0, loc_age_ms=rng.randint(500, 8000))
        else:
            s.update(lat=round(lat, 6), lon=round(lon, 6), loc_accuracy_m=round(rng.uniform(4, 25), 1),
                     loc_age_ms=rng.randint(200, 4000), speed_mps=round(speed, 1) if speed else 0.0,
                     bearing_deg=round(bearing, 1) if bearing is not None and speed >= 1 else None,
                     altitude_m=round(rng.uniform(20, 800), 1))
    else:
        s.update(lat=None, lon=None, loc_accuracy_m=None, loc_provider="none")
    if rsrp is None:
        s.update(network_type="NONE", service_state="out_of_service", data_state="disconnected",
                 rsrp_dbm=None, rsrq_db=None, sinr_db=None, cell_id=None, pci=None, tac=None, earfcn_arfcn=None,
                 band=None, signal_level=0)
        return s
    s["service_state"] = "in_service"
    s["signal_level"] = max(0, min(4, int((rsrp + 125) / 10)))
    if nr == "umts":
        s.update(network_type="UMTS", rsrp_dbm=None, rsrq_db=None, sinr_db=None, rssi_dbm=round(rsrp + 28, 1),
                 cell_id=cell["cell_id"] % 65536, pci=cell["pci"], tac=cell["tac"], earfcn_arfcn=10700, band=1)
        return s
    s.update(cell, rsrp_dbm=rsrp, rsrq_db=round(max(-20.0, min(-3.0, -8 + (rsrp + 90) / 8 + rng.gauss(0, 1))), 1),
             sinr_db=round(max(-10.0, min(30.0, (rsrp + 110) / 2.2 + rng.gauss(0, 2))), 1))
    if speed == 0 and not coarse:
        s["timing_advance"] = rng.randint(1, 40)
    if nr == "sa" and d_city < 8:
        s.update(network_type="NR_SA", earfcn_arfcn=636666, band=78, rsrp_dbm=round(rsrp - 3, 1))
    elif nr == "auto" and d_city < 8:
        s["network_type"] = "NR_NSA"
        s["nr_leg"] = {"pci": (cell["pci"] * 2) % 1008, "arfcn": 636666, "band": 78,
                       "ss_rsrp_dbm": round(rsrp - 6 + rng.gauss(0, 2), 1), "ss_rsrq_db": -11.0,
                       "ss_sinr_db": round(max(-5.0, (rsrp + 105) / 2.5), 1)}
    else:
        s["network_type"] = "LTE"
    if rng.random() < 0.7:
        nb = cell_for((lat or 31.9) + 0.02, (lon or 34.9) + 0.02, mnc)
        s["neighbors"] = [{"pci": nb["pci"], "earfcn_arfcn": nb["earfcn_arfcn"],
                           "rsrp_dbm": round(max(-140.0, rsrp - rng.uniform(3, 15)), 1), "rsrq_db": -14.0,
                           "rat": "LTE"}]
    return s


# ── demo fleet ────────────────────────────────────────────────────────────────
@dataclass
class DemoSpec:
    key: str
    label: str
    ownership: str
    mnc: str
    route: Optional[str] = None
    point: Optional[tuple] = None
    speed: float = 22.0                 # m/s
    interval_s: int = 30
    nr: str = "auto"
    coarse: bool = False
    dual_mnc: Optional[str] = None
    behaviour: str = "active"           # active | offline | paused | no_contact | degraded | never_seen
    battery_start: int = 95
    battery_end: Optional[int] = None   # if set: battery reaches this value at "now" (low-battery demo)
    battery_drain_h: float = 4.0
    offset_m: float = 0.0
    extra: dict = field(default_factory=dict)


DEMO_FLEET = [
    DemoSpec("r1-a", "ניידת 1 · כביש 1", "ORG", "01", route="r1", offset_m=0),
    DemoSpec("r1-b", "ניידת 2 · כביש 1", "ORG", "02", route="r1", offset_m=9000),
    DemoSpec("r1-c", "ניידת 3 · כביש 1", "ORG", "03", route="r1", offset_m=48000, extra={"command": "upload_now"}),
    DemoSpec("r6-a", "ניידת 4 · כביש 6", "ORG", "01", route="r6", offset_m=5000, speed=27),
    DemoSpec("r6-b", "ניידת 5 · כביש 6", "ORG", "02", route="r6", offset_m=70000, speed=25),
    DemoSpec("r90-a", "סיור ערבה 1", "ORG", "01", route="r90dz", offset_m=0, speed=9, interval_s=15),
    DemoSpec("r90-b", "סיור ערבה 2", "ORG", "02", route="r90dz", offset_m=12000, speed=9, interval_s=15,
             extra={"data_loss": True}),
    DemoSpec("r90-c", "סיור ערבה 3", "ORG", "03", route="r90", offset_m=60000, speed=24),
    DemoSpec("r90-d", "סיור ערבה 4", "ORG", "01", route="r90dz", offset_m=25000, speed=9, interval_s=15),
    DemoSpec("r90-e", "סיור ערבה 5", "ORG", "01", route="r90dz", offset_m=6000, speed=8, interval_s=15),
    DemoSpec("hfa-a", "חיפה 1", "ORG", "03", route="haifa", speed=9),
    DemoSpec("hfa-b", "חיפה 2 · dual SIM", "ORG", "01", route="haifa", speed=8, offset_m=2500, dual_mnc="02"),
    DemoSpec("tlv-a", "ת\"א 1 · 5G SA", "ORG", "02", route="tlv", speed=7, nr="sa"),
    DemoSpec("tlv-b", "ת\"א 2", "ORG", "01", route="tlv", speed=6, offset_m=3000, extra={"config_pending": True}),
    DemoSpec("tlv-fix", "ת\"א · נייח", "ORG", "03", point=(32.0700, 34.7930), speed=0, interval_s=300),
    DemoSpec("bs-fix", "באר שבע · נייח", "ORG", "01", point=(31.2520, 34.7910), speed=0, interval_s=300,
             battery_end=12, battery_drain_h=6.0),
    DemoSpec("jlm-3g", "ירושלים · 3G", "ORG", "02", route="r1", offset_m=60000, speed=12, nr="umts"),
    DemoSpec("p-paused", "אישי · מושהה", "PERSONAL", "01", route="tlv", speed=5, behaviour="paused"),
    DemoSpec("p-coarse", "אישי · מיקום משוער", "PERSONAL", "03", route="r6", offset_m=40000, speed=25, coarse=True),
    DemoSpec("off", "ניידת 6 · לא מקוון", "ORG", "03", route="r6", offset_m=20000, behaviour="offline"),
    DemoSpec("deg", "ניידת 7 · ניטור מוגבל", "ORG", "02", route="haifa", speed=7, offset_m=5000, behaviour="degraded"),
    DemoSpec("p-nocontact", "אישי · ללא קשר", "PERSONAL", "02", point=(31.7780, 35.2350), speed=0, interval_s=300,
             behaviour="no_contact"),
    DemoSpec("never", "ניידת 8 · טרם התחבר", "ORG", "01", behaviour="never_seen"),
]


def demo_uuid(key: str, kind: str = "device") -> uuid.UUID:
    return uuid.uuid5(DEMO_NS, f"{kind}:{key}")


def _demo_devices(db: Session) -> list:
    g = db.query(FieldDeviceGroup).filter(FieldDeviceGroup.name == DEMO_GROUP).first()
    uuids = [demo_uuid(s.key) for s in DEMO_FLEET]
    q = db.query(FieldDevice).filter(FieldDevice.device_uuid.in_(uuids))
    devs = {d.id: d for d in q.all()}
    if g is not None:
        for d in db.query(FieldDevice).filter(FieldDevice.group_id == g.id).all():
            devs[d.id] = d
    return list(devs.values())


def remove_demo(db: Session, recompute: bool = True) -> dict:
    """Delete all demo devices and their data; recompute the affected aggregates."""
    from app.services.field_agg import process_dirty
    devs = _demo_devices(db)
    now = datetime.utcnow()
    n_rows = 0
    for d in devs:
        cols = (FieldMeasurement.ts, FieldMeasurement.gx, FieldMeasurement.gy, FieldMeasurement.gx25,
                FieldMeasurement.gy25, FieldMeasurement.operator_key, FieldMeasurement.meas_tech)
        rows = [dict(r) for r in db.execute(select(*cols).where(FieldMeasurement.device_pk == d.id)).mappings()]
        mark_dirty(db, rows, now)            # so their contribution leaves the aggregates
        n_rows += len(rows)
        db.execute(delete(FieldMeasurement).where(FieldMeasurement.device_pk == d.id))
        db.execute(delete(FieldHeartbeat).where(FieldHeartbeat.device_pk == d.id))
        db.execute(delete(FieldDeviceCommand).where(FieldDeviceCommand.device_pk == d.id))
        db.execute(delete(FieldAlert).where(FieldAlert.device_pk == d.id))
        db.delete(d)
    db.commit()
    g = db.query(FieldDeviceGroup).filter(FieldDeviceGroup.name == DEMO_GROUP).first()
    if g is not None and not db.query(FieldDevice.id).filter(FieldDevice.group_id == g.id).first():
        db.delete(g)
        db.commit()
    if recompute and db.execute(select(FieldAggDirty.level).limit(1)).first():
        for _ in range(50):
            if process_dirty(db, datetime.utcnow() + timedelta(seconds=1))["buckets"] == 0:
                break
    return {"devices": len(devs), "samples": n_rows}


def _demo_group(db: Session) -> FieldDeviceGroup:
    g = db.query(FieldDeviceGroup).filter(FieldDeviceGroup.name == DEMO_GROUP).first()
    if g is None:
        g = FieldDeviceGroup(name=DEMO_GROUP, config={"upload_interval_s": 300},
                             description="נתוני הדגמה סינתטיים (לא מדידות אמת) — הסרה: "
                                         "python -m app.scripts.field_simulator --remove-demo")
        db.add(g)
        db.flush()
    return g


def _hb(state: str, battery: int, charging: bool, ts: datetime, cfg_applied: int, **kw) -> dict:
    hb = {"ts": ts, "battery_pct": battery, "is_charging": charging, "app_version": "1.0.0 (100)",
          "queue_size": 0, "monitoring_state": state, "degraded_reasons": [],
          "permissions": {"location": "precise", "background_location": True, "phone_state": True,
                          "notifications": True, "battery_optimization_exempt": True},
          "effective_location_mode": "precise", "location_services_enabled": True,
          "applied_config_version": cfg_applied, "evicted_samples_total": 0, "last_sample_ts": None,
          "acked_commands": []}
    hb.update(kw)
    return hb


def seed_demo(db: Session, hours: float = 8.0, now: Optional[datetime] = None, seed: int = 7) -> dict:
    """(Re)build the demo fleet. Idempotent: removes the previous demo first."""
    from app.services.field_maintenance import run_maintenance
    seed_field_defaults(db)
    removed = remove_demo(db, recompute=False)
    now = now or datetime.utcnow()
    start = now - timedelta(hours=hours)
    g = _demo_group(db)
    admin = db.execute(select(User.id).where(User.role == UserRole.ADMIN).limit(1)).scalar()
    n_samples = n_hb = 0
    for spec in DEMO_FLEET:
        rng = random.Random(f"{seed}:{spec.key}")
        d = FieldDevice(public_id=demo_uuid(spec.key, "public"), device_uuid=demo_uuid(spec.key),
                        label=DEMO_LABEL_PREFIX + spec.label, ownership=spec.ownership, group_id=g.id,
                        consent_version="he-demo-1" if spec.ownership == "PERSONAL" else None,
                        consented_at=start - timedelta(days=2) if spec.ownership == "PERSONAL" else None,
                        config_version=1, config_changed_at=start - timedelta(days=2),
                        model=rng.choice(["Galaxy XCover7", "Galaxy A55", "Pixel 8", "Redmi Note 13"]),
                        manufacturer="demo", os_version=rng.choice(["13 (API 33)", "14 (API 34)", "15 (API 35)"]),
                        app_version="1.0.0 (100)", status="active", enrolled_at=start - timedelta(days=2),
                        notes="נתוני הדגמה")
        db.add(d)
        db.flush()
        if spec.behaviour == "never_seen":
            continue
        route = Route(ROUTES[spec.route]) if spec.route else None
        stop_at = now
        if spec.behaviour == "offline":
            stop_at = now - timedelta(minutes=45)
        elif spec.behaviour == "paused":
            stop_at = now - timedelta(minutes=25)
        elif spec.behaviour == "no_contact":
            stop_at = now - timedelta(hours=7, minutes=30)
        upload_every = timedelta(minutes=5)
        pending: list = []
        t = start + timedelta(seconds=rng.randint(0, spec.interval_s))
        next_upload = start + upload_every
        next_hb = start + timedelta(seconds=rng.randint(0, 120))
        evicted = 0
        while t <= stop_at:
            hours_in = (t - start).total_seconds() / 3600
            if spec.battery_end is not None:
                battery = int(min(100, spec.battery_end + spec.battery_drain_h * (hours - hours_in)))
            else:
                battery = int(max(3, spec.battery_start - spec.battery_drain_h * hours_in))
            if route is not None:
                lat, lon, brg = route.at(spec.offset_m + spec.speed * (t - start).total_seconds())
            else:
                lat, lon = spec.point
                lat += rng.gauss(0, 0.00005)
                lon += rng.gauss(0, 0.00005)
                brg = None
            pending.append(make_sample(t, lat, lon, spec.mnc, rng, nr=spec.nr, coarse=spec.coarse, speed=spec.speed,
                                       bearing=brg, battery=battery, charging=False))
            if spec.dual_mnc:
                pending.append(make_sample(t, lat, lon, spec.dual_mnc, rng, sim_slot=1, nr="never",
                                           coarse=spec.coarse, speed=spec.speed, bearing=brg, battery=battery))
            t += timedelta(seconds=spec.interval_s)
            if t >= next_upload or t > stop_at:
                if pending:
                    r = ingest_batch(db, d, pending, sent_at=next_upload, now=min(next_upload, now))
                    n_samples += r["accepted"]
                    pending = []
                next_upload += upload_every
            while next_hb <= min(t, stop_at):
                state = "degraded" if spec.behaviour == "degraded" else "active"
                kw = {}
                if spec.behaviour == "degraded":
                    kw = {"degraded_reasons": ["background_location_missing", "battery_optimization_not_exempt"],
                          "permissions": {"location": "precise", "background_location": False, "phone_state": True,
                                          "notifications": True, "battery_optimization_exempt": False}}
                if spec.coarse:
                    kw["effective_location_mode"] = "coarse"
                if spec.extra.get("data_loss") and now - next_hb < timedelta(minutes=40):
                    evicted += 120
                    kw["degraded_reasons"] = ["queue_evicting"]
                    state = "degraded"
                kw["evicted_samples_total"] = evicted
                apply_heartbeat(db, d, _hb(state, battery, False, next_hb, d.config_version, **kw), now=next_hb)
                n_hb += 1
                next_hb += timedelta(minutes=5)
        # after the active period
        if spec.behaviour == "paused":
            hb_t = stop_at
            while hb_t <= now - timedelta(minutes=1):
                apply_heartbeat(db, d, _hb("paused_by_user", 70, False, hb_t, d.config_version), now=hb_t)
                n_hb += 1
                hb_t += timedelta(minutes=15)
        elif spec.behaviour == "no_contact":
            for k in (0, 1):
                hb_t = stop_at + timedelta(minutes=15 * k)
                apply_heartbeat(db, d, _hb("paused_schedule", 64, False, hb_t, d.config_version), now=hb_t)
                n_hb += 1
        if spec.extra.get("config_pending"):
            bump_device(d, now - timedelta(minutes=45))
        if spec.extra.get("command"):
            db.add(FieldDeviceCommand(id=demo_uuid(spec.key, "command"), device_pk=d.id, type=spec.extra["command"],
                                      status="queued", issued_by=admin, issued_at=now - timedelta(minutes=2),
                                      expires_at=now + timedelta(minutes=18), note="דמו"))
        db.commit()
    maint = run_maintenance(db, ["aggregate", "alerts"], now=now + timedelta(seconds=1))
    return {"removed_previous": removed, "devices": len(DEMO_FLEET), "samples": n_samples, "heartbeats": n_hb,
            "hours": hours, "group": DEMO_GROUP, "maintenance": maint}
