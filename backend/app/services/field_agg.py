"""
Field reception — grid aggregates (spec 2.2/2.4/5.4): dirty-queue aggregation,
heatmap cells and dead-zone clusters.

Aggregation is idempotent: every dirty bucket is recomputed from raw.
Buckets keep all gridded samples (v2.1 rule 3: service state / OOS time is
always counted); stale-cell samples contribute to n / oos_* / n_stale_excluded
but never to RSRP, SINR, RSRQ or the in-service tier counts. LTE and NR RSRP
never share a bucket (meas_tech is part of the key).
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import and_, delete, select, tuple_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.field import (
    AGG_STAT_COLUMNS, FLAG_STALE_CELL, FieldAggDaily, FieldAggDailyCoarse, FieldAggDirty, FieldAggHourly,
    FieldDevice, FieldMeasurement,
)
from app.services.field_quality import (
    HIST_BINS, OOS_STATES, classify_tier, effective_tier, hist_index, hist_percentile,
)
from app.services.field_time import il_day, il_day_bounds

log = logging.getLogger(__name__)

DT_CAP_S = 600          # 2 × stationary_interval_s (contract default 300)
UNKNOWN_OPERATOR = "unknown"

SAMPLE_COLS = (FieldMeasurement.device_pk, FieldMeasurement.sim_slot, FieldMeasurement.ts,
               FieldMeasurement.flags, FieldMeasurement.tier, FieldMeasurement.service_state,
               FieldMeasurement.network_type, FieldMeasurement.meas_tech, FieldMeasurement.rsrp_dbm,
               FieldMeasurement.nr_ss_rsrp_dbm, FieldMeasurement.sinr_db, FieldMeasurement.rsrq_db,
               FieldMeasurement.operator_key, FieldMeasurement.gx, FieldMeasurement.gy,
               FieldMeasurement.gx25, FieldMeasurement.gy25)


def hidden_device_pks(db: Session) -> set:
    """Devices whose data must not be shown (withdrawn / deleted, until purged),
    except the shared anonymous sink devices."""
    return set(db.execute(select(FieldDevice.id).where(
        FieldDevice.status.in_(("withdrawn", "deleted")), FieldDevice.is_anon_sink.is_(False))).scalars())


def assign_dt(samples: list, horizon: datetime, cap_s: int = DT_CAP_S) -> None:
    """samples: list of dicts sorted by (device_pk, sim_slot, ts). Adds `dt` (s)
    = min(next − ts, cap) for the same device+SIM; the last one gets
    min(cap, horizon − ts). `gap` = time beyond the cap (→ "unknown")."""
    n = len(samples)
    for i, s in enumerate(samples):
        nxt = samples[i + 1] if i + 1 < n else None
        if nxt is not None and nxt["device_pk"] == s["device_pk"] and nxt["sim_slot"] == s["sim_slot"]:
            delta = (nxt["ts"] - s["ts"]).total_seconds()
        else:
            delta = max(0.0, (horizon - s["ts"]).total_seconds())
            delta = min(delta, cap_s)
        s["dt"] = max(0.0, min(delta, cap_s))
        s["gap"] = max(0.0, delta - cap_s)


def fetch_samples(db: Session, where, lookahead_from: Optional[datetime] = None) -> list:
    rows = db.execute(select(*SAMPLE_COLS).where(where).order_by(
        FieldMeasurement.device_pk, FieldMeasurement.sim_slot, FieldMeasurement.ts)).mappings().all()
    return [dict(r) for r in rows]


def empty_stats() -> dict:
    s = {c: 0 for c in AGG_STAT_COLUMNS}
    s.update(sum_rsrp=0.0, sumsq_rsrp=0.0, min_rsrp=None, max_rsrp=None, sum_sinr=0.0, sum_rsrq=0.0,
             rsrp_hist=[0] * HIST_BINS, ss_rsrp_hist=[0] * HIST_BINS, device_set=set())
    return s


def add_sample(st: dict, s: dict) -> None:
    dt = int(round(s.get("dt", 0)))
    st["n"] += 1
    st["device_set"].add(s["device_pk"])
    stale = bool(s["flags"] & FLAG_STALE_CELL)
    eff = effective_tier(s["tier"], s["flags"], s["service_state"])
    if eff == -1:
        st["n_stale_excluded"] += 1
    elif eff is None:
        st["t_unk"] += 1
        st["w_unk"] += dt
    else:
        st[f"t{eff}"] += 1
        st[f"w{eff}"] += dt
    if s["service_state"] in OOS_STATES:
        st["oos_n"] += 1
        st["oos_s"] += dt
    if s["network_type"] == "NR_NSA":
        st["n_nsa"] += 1
    if stale:
        return
    x = s["rsrp_dbm"]
    if x is not None and s["meas_tech"] in ("LTE", "NR"):
        st["n_rsrp"] += 1
        st["sum_rsrp"] += x
        st["sumsq_rsrp"] += x * x
        st["min_rsrp"] = x if st["min_rsrp"] is None else min(st["min_rsrp"], x)
        st["max_rsrp"] = x if st["max_rsrp"] is None else max(st["max_rsrp"], x)
        st["rsrp_hist"][hist_index(x)] += 1
    if s["network_type"] == "NR_NSA" and s.get("nr_ss_rsrp_dbm") is not None:
        st["n_ss"] += 1
        st["ss_rsrp_hist"][hist_index(s["nr_ss_rsrp_dbm"])] += 1
    if s.get("sinr_db") is not None:
        st["n_sinr"] += 1
        st["sum_sinr"] += s["sinr_db"]
    if s.get("rsrq_db") is not None:
        st["n_rsrq"] += 1
        st["sum_rsrq"] += s["rsrq_db"]


def merge_stats(st: dict, row) -> None:
    """Merge an aggregate row (ORM object or dict) into accumulator `st`."""
    g = (lambda k: row.get(k)) if isinstance(row, dict) else (lambda k: getattr(row, k))
    for c in AGG_STAT_COLUMNS:
        v = g(c)
        if c in ("rsrp_hist", "ss_rsrp_hist"):
            if v:
                st[c] = [a + b for a, b in zip(st[c], v)]
        elif c == "device_set":
            st[c].update(v or [])
        elif c == "min_rsrp":
            if v is not None:
                st[c] = v if st[c] is None else min(st[c], v)
        elif c == "max_rsrp":
            if v is not None:
                st[c] = v if st[c] is None else max(st[c], v)
        elif c == "n_devices":
            continue
        else:
            st[c] += v or 0


def finalize(st: dict) -> dict:
    out = dict(st)
    ds = sorted(st["device_set"])
    out["device_set"] = ds
    out["n_devices"] = len(ds)
    if not any(out["rsrp_hist"]):
        out["rsrp_hist"] = None
    if not any(out["ss_rsrp_hist"]):
        out["ss_rsrp_hist"] = None
    return out


# ── dirty-queue processing ────────────────────────────────────────────────────
def _upsert(db: Session, model, key_cols: tuple, stats_by_key: dict, keys: Iterable[tuple],
            now: datetime) -> int:
    """Replace the aggregate rows of `keys` (delete + bulk insert, Core statements)."""
    keys = list(keys)
    table = model.__table__
    kc = [table.c[c] for c in key_cols]
    for i in range(0, len(keys), 200):
        db.execute(delete(table).where(tuple_(*kc).in_(keys[i:i + 200])))
    rows = []
    for key in keys:
        st = stats_by_key.get(key)
        if st is not None and st["n"] > 0:
            row = finalize(st)
            vals = {c: row[c] for c in AGG_STAT_COLUMNS}
            vals.update(dict(zip(key_cols, key)))
            vals["updated_at"] = now
            rows.append(vals)
    if rows:
        db.execute(table.insert(), rows)
    return len(rows)


def process_dirty(db: Session, now: Optional[datetime] = None, limit: int = 20000) -> dict:
    """Recompute the dirty buckets. Idempotent; safe to run concurrently with
    ingest (only entries marked before the snapshot are removed)."""
    now = now or datetime.utcnow()
    dirty = db.execute(select(FieldAggDirty).where(FieldAggDirty.marked_at <= now)
                       .limit(limit)).scalars().all()
    if not dirty:
        return {"buckets": 0, "hourly": 0, "daily": 0, "coarse": 0}
    hidden = hidden_device_pks(db)
    raw_cutoff = now - timedelta(days=settings.FIELD_RAW_RETENTION_DAYS)
    cap = timedelta(seconds=DT_CAP_S)

    g100_by_hour: dict = defaultdict(set)
    g2500_by_day: dict = defaultdict(set)
    for d in dirty:
        if d.hour < raw_cutoff - timedelta(hours=1):
            continue                  # raw already gone — never recompute (keeps the aggregate)
        if d.level == "g100":
            g100_by_hour[d.hour].add((d.gx, d.gy, d.hour, d.operator_key, d.tech))
        else:
            g2500_by_day[il_day(d.hour)].add((d.gx, d.gy, d.operator_key, d.tech))

    hourly_n = daily_n = coarse_n = 0
    daily_keys: dict = defaultdict(set)
    for hour, keys in g100_by_hour.items():
        end = hour + timedelta(hours=1)
        devs = set(db.execute(select(FieldMeasurement.device_pk).where(
            FieldMeasurement.ts >= hour, FieldMeasurement.ts < end,
            FieldMeasurement.gx.isnot(None)).distinct()).scalars()) - hidden
        stats: dict = {}
        if devs:
            samples = fetch_samples(db, and_(FieldMeasurement.device_pk.in_(devs),
                                             FieldMeasurement.ts >= hour, FieldMeasurement.ts < end + cap))
            assign_dt(samples, min(now, end + cap))
            for s in samples:
                if s["ts"] >= end or s["gx"] is None:
                    continue
                k = (s["gx"], s["gy"], hour, s["operator_key"] or UNKNOWN_OPERATOR, s["meas_tech"])
                if k in keys:
                    add_sample(stats.setdefault(k, empty_stats()), s)
        hourly_n += _upsert(db, FieldAggHourly, ("gx", "gy", "hour", "operator_key", "meas_tech"),
                            stats, keys, now)
        for (gx, gy, _h, op, tech) in keys:
            daily_keys[il_day(hour)].add((gx, gy, op, tech))
    db.flush()

    for day, keys in daily_keys.items():
        start, end = il_day_bounds(day)
        cells = {(k[0], k[1]) for k in keys}
        stats = {}
        cell_list = list(cells)
        for i in range(0, len(cell_list), 300):
            rows = db.execute(select(FieldAggHourly).where(
                FieldAggHourly.hour >= start, FieldAggHourly.hour < end,
                tuple_(FieldAggHourly.gx, FieldAggHourly.gy).in_(cell_list[i:i + 300]))).scalars().all()
            for r in rows:
                k = (r.gx, r.gy, day, r.operator_key, r.meas_tech)
                if (r.gx, r.gy, r.operator_key, r.meas_tech) in keys:
                    merge_stats(stats.setdefault(k, empty_stats()), r)
        daily_n += _upsert(db, FieldAggDaily, ("gx", "gy", "day", "operator_key", "meas_tech"), stats,
                           [(k[0], k[1], day, k[2], k[3]) for k in keys], now)

    for day, keys in g2500_by_day.items():
        start, end = il_day_bounds(day)
        devs = set(db.execute(select(FieldMeasurement.device_pk).where(
            FieldMeasurement.ts >= start, FieldMeasurement.ts < end,
            FieldMeasurement.gx25.isnot(None)).distinct()).scalars()) - hidden
        stats = {}
        if devs:
            samples = fetch_samples(db, and_(FieldMeasurement.device_pk.in_(devs),
                                             FieldMeasurement.ts >= start, FieldMeasurement.ts < end + cap))
            assign_dt(samples, min(now, end + cap))
            for s in samples:
                if s["ts"] >= end or s["gx25"] is None:
                    continue
                k = (s["gx25"], s["gy25"], day, s["operator_key"] or UNKNOWN_OPERATOR, s["meas_tech"])
                if (k[0], k[1], k[3], k[4]) in keys:
                    add_sample(stats.setdefault(k, empty_stats()), s)
        coarse_n += _upsert(db, FieldAggDailyCoarse, ("gx25", "gy25", "day", "operator_key", "meas_tech"),
                            stats, [(k[0], k[1], day, k[2], k[3]) for k in keys], now)

    ids = [(d.level, d.gx, d.gy, d.hour, d.operator_key, d.tech) for d in dirty]
    for i in range(0, len(ids), 200):
        db.execute(delete(FieldAggDirty.__table__).where(
            tuple_(FieldAggDirty.level, FieldAggDirty.gx, FieldAggDirty.gy, FieldAggDirty.hour,
                   FieldAggDirty.operator_key, FieldAggDirty.tech).in_(ids[i:i + 200]),
            FieldAggDirty.marked_at <= now))
    db.commit()
    return {"buckets": len(dirty), "hourly": hourly_n, "daily": daily_n, "coarse": coarse_n}


# ── query side: heatmap & dead zones ──────────────────────────────────────────
def load_cells(db: Session, level: int, dfrom: datetime, dto: datetime, meas_tech: Optional[str],
               operators: Optional[list], bbox: Optional[tuple], day_resolution: bool) -> tuple:
    """Merge aggregate rows into output cells.
    Returns ({(op_or_None, tech, cx, cy): {"st": stats, "days": set, "first": t, "last": t}}, source)."""
    from app.services.field_quality import G100_DLAT, G100_DLON, G2500_DLAT, G2500_DLON
    if level == 2500:
        model, gxc, gyc, tcol = FieldAggDailyCoarse, FieldAggDailyCoarse.gx25, FieldAggDailyCoarse.gy25, "day"
        dlat, dlon, k = G2500_DLAT, G2500_DLON, 1
        source = "daily_coarse"
    else:
        k = 5 if level == 500 else 1
        dlat, dlon = G100_DLAT, G100_DLON
        if day_resolution or dto - dfrom > timedelta(days=7):
            model, gxc, gyc, tcol, source = FieldAggDaily, FieldAggDaily.gx, FieldAggDaily.gy, "day", "daily"
        else:
            model, gxc, gyc, tcol, source = FieldAggHourly, FieldAggHourly.gx, FieldAggHourly.gy, "hour", "hourly"
    conds = []
    if tcol == "day":
        conds += [model.day >= il_day(dfrom), model.day <= il_day(dto - timedelta(microseconds=1))]
    else:
        conds += [model.hour >= dfrom.replace(minute=0, second=0, microsecond=0), model.hour < dto]
    if meas_tech == "LTE":
        # no-service samples carry meas_tech NONE (the phone reports no RAT); they are
        # part of the operator's coverage picture, so the LTE layer includes them
        # (RSRP statistics are unaffected — NONE samples have no RSRP).
        conds.append(model.meas_tech.in_(("LTE", "NONE")))
    elif meas_tech:
        conds.append(model.meas_tech == meas_tech)
    if operators:
        conds.append(model.operator_key.in_(operators))
    if bbox:
        s, w, n, e = bbox
        import math
        conds += [gyc >= math.floor(s / dlat), gyc <= math.floor(n / dlat),
                  gxc >= math.floor(w / dlon), gxc <= math.floor(e / dlon)]
    out: dict = {}
    for r in db.execute(select(model).where(and_(*conds))).scalars():
        gx, gy = (r.gx25, r.gy25) if level == 2500 else (r.gx, r.gy)
        key = (r.operator_key, meas_tech or r.meas_tech, gx // k, gy // k)
        c = out.get(key)
        if c is None:
            c = out[key] = {"st": empty_stats(), "days": set(), "first": None, "last": None}
        merge_stats(c["st"], r)
        t = getattr(r, tcol)
        c["days"].add(t if tcol == "day" else il_day(t))
        tt = t if tcol == "hour" else datetime.combine(t, datetime.min.time())
        c["first"] = tt if c["first"] is None else min(c["first"], tt)
        c["last"] = tt if c["last"] is None else max(c["last"], tt)
    return out, source


def merge_operators(cells: dict) -> dict:
    """Collapse the operator dimension: {(tech, cx, cy): cell}."""
    out: dict = {}
    for (op, tech, cx, cy), c in cells.items():
        m = out.get((tech, cx, cy))
        if m is None:
            m = out[(tech, cx, cy)] = {"st": empty_stats(), "days": set(), "first": c["first"], "last": c["last"]}
        merge_stats(m["st"], finalize(c["st"]))
        m["days"] |= c["days"]
        m["first"] = min(m["first"], c["first"])
        m["last"] = max(m["last"], c["last"])
    return out


def cell_metrics(st: dict, tech: str) -> dict:
    hist = st["rsrp_hist"] if any(st["rsrp_hist"]) else None
    p50 = hist_percentile(hist, 50)
    p5 = hist_percentile(hist, 5)
    w_all = sum(st[f"w{i}"] for i in range(5)) + st["w_unk"]
    oos_pct = (100.0 * st["oos_s"] / w_all) if w_all > 0 else (100.0 * st["oos_n"] / st["n"] if st["n"] else None)
    w_cls = sum(st[f"w{i}"] for i in range(5))
    net = "LTE" if tech == "LTE" else "NR_SA"
    return {"p50": p50, "p5": p5, "n": st["n"], "n_rsrp": st["n_rsrp"],
            "tier": classify_tier(net, p50, "in_service") if p50 is not None else
            (0 if st["oos_n"] and st["oos_n"] == st["n"] else None),
            "oos_pct": round(oos_pct, 1) if oos_pct is not None else None,
            "w0_share": (st["w0"] / w_cls) if w_cls > 0 else None,
            "n_devices": len(st["device_set"])}


def dead_zones(cells: dict, level_deg: tuple, k_min: Optional[int]) -> list:
    """cells: output of load_cells at level 100 (per operator × tech).
    Spec 5.4: a cell is dead with n ≥ 10, (≥ 2 devices or ≥ 2 days) and
    (w0/Σw ≥ 0.6 or p50 < -115); clusters = 8-connected components."""
    dlat, dlon = level_deg
    dead: dict = defaultdict(dict)
    for (op, tech, cx, cy), c in cells.items():
        st = c["st"]
        if k_min is not None and len(st["device_set"]) < k_min:
            continue
        m = cell_metrics(st, tech)
        if st["n"] < 10 or (m["n_devices"] < 2 and len(c["days"]) < 2):
            continue
        if (m["w0_share"] is not None and m["w0_share"] >= 0.6) or (m["p50"] is not None and m["p50"] < -115):
            dead[(op, tech)][(cx, cy)] = c
    zones = []
    for (op, tech), cellmap in dead.items():
        seen = set()
        for start in sorted(cellmap):
            if start in seen:
                continue
            comp, stack = [], [start]
            seen.add(start)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nb = (cur[0] + dx, cur[1] + dy)
                        if nb in cellmap and nb not in seen:
                            seen.add(nb)
                            stack.append(nb)
            st = empty_stats()
            first = last = None
            for cxy in comp:
                c = cellmap[cxy]
                merge_stats(st, finalize(c["st"]))
                first = c["first"] if first is None else min(first, c["first"])
                last = c["last"] if last is None else max(last, c["last"])
            m = cell_metrics(st, tech)
            lat_c = sum((cy + 0.5) * dlat for _, cy in comp) / len(comp)
            lon_c = sum((cx + 0.5) * dlon for cx, _ in comp) / len(comp)
            import math
            area = len(comp) * (dlat * 111.32) * (dlon * 111.32 * math.cos(math.radians(lat_c)))
            severity = "critical" if (m["oos_pct"] or 0) >= 30 or len(comp) >= 5 else "warning"
            polygon = {"type": "MultiPolygon", "coordinates": [
                [[[cx * dlon, cy * dlat], [(cx + 1) * dlon, cy * dlat], [(cx + 1) * dlon, (cy + 1) * dlat],
                  [cx * dlon, (cy + 1) * dlat], [cx * dlon, cy * dlat]]] for cx, cy in sorted(comp)]}
            mn = min(comp)
            zones.append({"id": f"{op}:{tech}:{mn[0]}:{mn[1]}", "operator_key": op, "tech": tech,
                          "centroid_lat": round(lat_c, 6), "centroid_lon": round(lon_c, 6),
                          "cells": len(comp), "area_km2": round(area, 3), "p50": m["p50"],
                          "oos_pct": m["oos_pct"], "n": st["n"], "n_devices": m["n_devices"],
                          "severity": severity, "first_seen": first, "last_seen": last, "polygon": polygon})
    zones.sort(key=lambda z: (z["severity"] != "critical", -z["cells"], -z["n"]))
    return zones
