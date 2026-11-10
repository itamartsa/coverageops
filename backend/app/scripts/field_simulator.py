"""
Field probe simulator — synthetic devices for developing/demoing the /field dashboard.

Two modes:

1. HTTP mode (default) — speaks the REAL API of a running server, like the app:
   logs in as an admin, creates enrollment codes, enrolls N devices, then sends
   gzip batches, heartbeats and command acks in (accelerated or real) time.

       python -m app.scripts.field_simulator --server http://localhost:8000 \\
           --user adminuser --password <password> --devices 3 --duration 120

   Credentials may also come from FIELD_SIM_USER / FIELD_SIM_PASSWORD.

2. Demo seed — fills the configured database (DATABASE_URL, as the API uses)
   with a ~20-device DEMO fleet (group "דמו", labels "דמו · …", several hours of
   history ending now). Idempotent: re-running replaces the previous demo.

       python -m app.scripts.field_simulator --seed-demo [--hours 8]
       python -m app.scripts.field_simulator --remove-demo

All data produced by this script is synthetic.
"""
import argparse
import gzip
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

from app.services.field_demo import DEMO_GROUP, ROUTES, Route, make_sample


# ── tiny HTTP client (stdlib only — httpx is a dev dependency) ────────────────
class Api:
    def __init__(self, server: str, verbose: bool = False):
        self.server = server.rstrip("/")
        self.verbose = verbose

    def call(self, method: str, path: str, body=None, token=None, form=None, gz=False):
        url = self.server + path
        headers = {}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            if gz:
                data = gzip.compress(data)
                headers["Content-Encoding"] = "gzip"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None), dict(r.headers)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = raw.decode(errors="replace")
            return e.code, payload, dict(e.headers)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds") + "Z"


class SimDevice:
    def __init__(self, api: Api, idx: int, mnc: str, route: str, ownership: str, rng: random.Random):
        self.api, self.idx, self.mnc, self.ownership, self.rng = api, idx, mnc, ownership, rng
        self.route = Route(ROUTES[route])
        self.offset = rng.uniform(0, self.route.length)
        self.speed = rng.uniform(15, 28)
        self.device_id = self.token = None
        self.queue: list = []
        self.acks: list = []
        self.battery = rng.randint(70, 100)
        self.config_version = 1
        self.stats = {"accepted": 0, "duplicates": 0, "rejected": 0, "heartbeats": 0, "commands": 0, "errors": 0}

    def enroll(self, code: str) -> None:
        body = {"enrollment_code": code, "device_uuid": str(uuid.uuid4()), "model": "Simulator",
                "manufacturer": "CoverageOps", "os_version": "14 (API 34)", "app_version": "sim-1.0",
                "ownership": self.ownership, "label": f"סימולטור · {self.idx + 1}"}
        if self.ownership == "PERSONAL":
            body.update(consent_version="sim-1", consented_at=_iso(datetime.utcnow()))
        st, j, _ = self.api.call("POST", "/api/field/devices/enroll", body)
        if st != 201:
            raise SystemExit(f"enroll failed: {st} {j}")
        self.device_id, self.token = j["device_id"], j["device_token"]
        self.config_version = j["config"]["config_version"]

    def sample(self, t0: datetime, now: datetime, trigger: str = "periodic") -> None:
        lat, lon, brg = self.route.at(self.offset + self.speed * (now - t0).total_seconds())
        self.queue.append(make_sample(now, lat, lon, self.mnc, self.rng, speed=self.speed, bearing=brg,
                                      battery=self.battery, trigger=trigger))

    def upload(self) -> None:
        while self.queue:
            chunk, self.queue = self.queue[:500], self.queue[500:]
            env = {"device_id": self.device_id, "sent_at": _iso(datetime.utcnow()),
                   "config_version": self.config_version, "samples": chunk}
            st, j, h = self.api.call("POST", "/api/field/measurements/batch", env, self.token, gz=True)
            if st == 200:
                self.stats["accepted"] += j["accepted"]
                self.stats["duplicates"] += j["duplicates"]
                self.stats["rejected"] += len(j["rejected"])
                self.config_version = j["config_version"]
            elif st in (429, 503):
                time.sleep(min(int(h.get("Retry-After", "5")), 30))
                self.queue = chunk + self.queue
                return
            else:
                self.stats["errors"] += 1
                print(f"  device {self.idx + 1}: batch → {st} {j}", file=sys.stderr)
                return

    def heartbeat(self, t0: datetime, now: datetime) -> None:
        body = {"ts": _iso(now), "battery_pct": self.battery, "is_charging": False, "app_version": "sim-1.0",
                "queue_size": len(self.queue), "monitoring_state": "active", "degraded_reasons": [],
                "permissions": {"location": "precise", "background_location": True, "phone_state": True,
                                "notifications": True, "battery_optimization_exempt": True},
                "effective_location_mode": "precise", "location_services_enabled": True,
                "applied_config_version": self.config_version, "evicted_samples_total": 0,
                "last_sample_ts": self.queue[-1]["ts"] if self.queue else None, "acked_commands": self.acks}
        st, j, _ = self.api.call("POST", "/api/field/devices/heartbeat", body, self.token)
        if st != 200:
            self.stats["errors"] += 1
            print(f"  device {self.idx + 1}: heartbeat → {st} {j}", file=sys.stderr)
            return
        self.stats["heartbeats"] += 1
        self.acks = []
        self.config_version = max(self.config_version, j["config_version"])
        for c in j["commands"]:            # execute: sample_now → manual sample, upload_now → upload
            if c["type"] == "sample_now":
                self.sample(t0, now, trigger="manual")
            self.upload()
            self.acks.append({"id": c["id"], "status": "done"})
            self.stats["commands"] += 1


def run_http(args) -> int:
    user = args.user or os.environ.get("FIELD_SIM_USER")
    password = args.password or os.environ.get("FIELD_SIM_PASSWORD")
    if not user or not password:
        print("נדרשים --user ו---password (או FIELD_SIM_USER / FIELD_SIM_PASSWORD) של משתמש ADMIN", file=sys.stderr)
        return 2
    api = Api(args.server)
    st, j, _ = api.call("POST", "/api/auth/login", form={"username": user, "password": password})
    if st != 200:
        print(f"login failed: {st} {j}", file=sys.stderr)
        return 1
    admin = j["access_token"]
    st, groups, _ = api.call("GET", "/api/field/groups", token=admin)
    gid = next((g["id"] for g in groups if g["name"] == DEMO_GROUP), None) if st == 200 else None
    if gid is None:
        st, g, _ = api.call("POST", "/api/field/groups", {"name": DEMO_GROUP, "description": "נתוני הדגמה סינתטיים",
                                                          "config": {}}, admin)
        gid = g["id"] if st == 201 else None
    st, code, _ = api.call("POST", "/api/field/enrollment-codes",
                           {"ownership": args.ownership, "max_uses": args.devices, "ttl_hours": 1,
                            "label": "סימולטור", "group_id": gid}, admin)
    if st != 201:
        print(f"create code failed: {st} {code}", file=sys.stderr)
        return 1
    rng = random.Random(args.seed)
    mncs, routes = ["01", "02", "03"], ["r1", "r6", "haifa", "tlv"]
    devs = [SimDevice(api, i, mncs[i % 3], routes[i % 4], args.ownership, rng) for i in range(args.devices)]
    for d in devs:
        d.enroll(code["code"])
        print(f"enrolled {d.device_id} (סימולטור · {d.idx + 1}, 425-{d.mnc})")
    if args.issue_command and devs:
        st, c, _ = api.call("POST", f"/api/field/devices/{devs[0].device_id}/commands", {"type": "sample_now"}, admin)
        print(f"issued sample_now to device 1 → {st} {c.get('id') if isinstance(c, dict) else c}")
    t0 = datetime.utcnow()
    tick = 0
    end = time.monotonic() + args.duration
    while time.monotonic() < end:
        now = datetime.utcnow()
        for d in devs:
            d.sample(t0, now)
            if tick % args.upload_every == 0:
                d.upload()
            if tick % args.heartbeat_every == 0:
                d.heartbeat(t0, now)
        tick += 1
        time.sleep(args.interval)
    for d in devs:
        d.upload()
        d.heartbeat(t0, datetime.utcnow())
    total = {k: sum(d.stats[k] for d in devs) for k in devs[0].stats} if devs else {}
    print(json.dumps({"devices": len(devs), **total}, ensure_ascii=False))
    if args.issue_command and devs:
        st, lst, _ = api.call("GET", f"/api/field/devices/{devs[0].device_id}/commands", token=admin)
        print("device 1 commands:", json.dumps(lst, ensure_ascii=False))
    return 0 if total.get("errors", 0) == 0 else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="CoverageOps field probe simulator (synthetic data)")
    p.add_argument("--seed-demo", action="store_true", help="fill the local database with the demo fleet")
    p.add_argument("--remove-demo", action="store_true", help="remove the demo fleet from the local database")
    p.add_argument("--hours", type=float, default=8.0, help="demo history length (hours, ending now)")
    p.add_argument("--server", default="http://localhost:8000")
    p.add_argument("--user")
    p.add_argument("--password")
    p.add_argument("--devices", type=int, default=3)
    p.add_argument("--ownership", choices=("ORG", "PERSONAL"), default="ORG")
    p.add_argument("--duration", type=float, default=60.0, help="seconds to run (HTTP mode)")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between samples (HTTP mode)")
    p.add_argument("--upload-every", type=int, default=5, help="ticks between batches")
    p.add_argument("--heartbeat-every", type=int, default=5, help="ticks between heartbeats")
    p.add_argument("--issue-command", action="store_true", help="issue sample_now to device 1 and show its ack")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    if args.seed_demo or args.remove_demo:
        from app.core.database import Base, SessionLocal, engine
        from app.models import cell_site, field, user  # noqa: F401  (register tables for create_all)
        from app.services.field_demo import remove_demo, seed_demo
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            res = remove_demo(db) if args.remove_demo else seed_demo(db, hours=args.hours, seed=args.seed)
        finally:
            db.close()
        print(json.dumps(res, ensure_ascii=False, default=str, indent=1))
        return 0
    return run_http(args)


if __name__ == "__main__":
    sys.exit(main())
