"""
Field reception — KPIs from raw samples (spec 5.2/5.3, v2.1 rule 3).

Time weighting: each sample carries dt = min(next sample of the same device+SIM
− ts, cap=600 s); the last sample of a sequence gets min(cap, window_end − ts);
time beyond the cap is "unknown". Stale-cell samples (cell_info_age_ms > 10 s)
keep only their service-state contribution: OOS time is always counted, their
signal values and in-service tier never are (their time is "unknown").
RSRP is never mixed across technologies: lte (LTE + NR_NSA anchor), nr_sa
(NR_SA SS-RSRP) and nsa_nr_leg (SS-RSRP of the NSA NR leg) are separate.
"""
from collections import defaultdict
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.field import FLAG_AGE_UNKNOWN, FLAG_STALE_CELL, FieldMeasurement
from app.services.field_agg import DT_CAP_S, assign_dt, hidden_device_pks
from app.services.field_quality import OOS_STATES, effective_tier, percentile

KPI_COLS = (FieldMeasurement.device_pk, FieldMeasurement.sim_slot, FieldMeasurement.ts,
            FieldMeasurement.flags, FieldMeasurement.tier, FieldMeasurement.service_state,
            FieldMeasurement.data_state, FieldMeasurement.network_type, FieldMeasurement.meas_tech,
            FieldMeasurement.rsrp_dbm, FieldMeasurement.nr_ss_rsrp_dbm, FieldMeasurement.operator_key,
            FieldMeasurement.operator_name, FieldMeasurement.cell_id, FieldMeasurement.pci,
            FieldMeasurement.earfcn_arfcn, FieldMeasurement.band, FieldMeasurement.gx, FieldMeasurement.gy,
            FieldMeasurement.lat, FieldMeasurement.lon, FieldMeasurement.is_roaming)


def load_window(db: Session, dfrom: datetime, dto: datetime, device_pks: Optional[set] = None,
                operators: Optional[list] = None, include_roaming: bool = False,
                bbox: Optional[tuple] = None) -> list:
    """Raw samples of the window with dt assigned (computed on the full per-device
    sequence, before any operator/bbox filter)."""
    conds = [FieldMeasurement.ts >= dfrom, FieldMeasurement.ts < dto]
    if device_pks is not None:
        if not device_pks:
            return []
        conds.append(FieldMeasurement.device_pk.in_(device_pks))
    rows = db.execute(select(*KPI_COLS).where(and_(*conds)).order_by(
        FieldMeasurement.device_pk, FieldMeasurement.sim_slot, FieldMeasurement.ts)).mappings().all()
    hidden = hidden_device_pks(db)
    samples = [dict(r) for r in rows if r["device_pk"] not in hidden]
    assign_dt(samples, min(dto, datetime.utcnow()), DT_CAP_S)
    out = []
    for s in samples:
        if not include_roaming and s["is_roaming"]:
            continue
        if operators and s["operator_key"] not in operators:
            continue
        if bbox is not None:
            # spatial KPIs: gridded samples only (not coarse, accuracy ≤ 100 m, fresh fix)
            if s["gx"] is None:
                continue
            south, west, north, east = bbox
            if not (south <= s["lat"] <= north and west <= s["lon"] <= east):
                continue
        out.append(s)
    return out


def _r(x: Optional[float], nd: int = 1) -> Optional[float]:
    return None if x is None else round(x, nd)


def compute_kpis(samples: list) -> dict:
    """KPIs of a list of samples that already carry dt/gap (see load_window).
    pct_time_by_tier[i] is tier i (0 = marginal … 4 = excellent), denominator =
    classified time only (unclassified excluded, v2)."""
    w = [0.0] * 5
    w_unk = w_stale = gap = oos_s = total = nsa_s = lte_s = data_conn_s = fallback_s = in_service_s = 0.0
    n = n_stale = n_age_unknown = 0
    lte, nr_sa, nsa_leg = [], [], []
    changes = 0
    change_time = 0.0
    prev_by_seq: dict = {}
    for s in samples:
        dt = s.get("dt", 0.0)
        n += 1
        total += dt
        gap += s.get("gap", 0.0)
        stale = bool(s["flags"] & FLAG_STALE_CELL)
        if stale:
            n_stale += 1
        if s["flags"] & FLAG_AGE_UNKNOWN:
            n_age_unknown += 1
        eff = effective_tier(s["tier"], s["flags"], s["service_state"])
        if eff == -1:
            w_stale += dt
        elif eff is None:
            w_unk += dt
        else:
            w[eff] += dt
        if s["service_state"] in OOS_STATES:
            oos_s += dt
        elif s["service_state"] == "in_service":
            in_service_s += dt
            if s["network_type"] in ("UMTS", "GSM", "NONE"):
                fallback_s += dt
        if s.get("data_state") == "connected":
            data_conn_s += dt
        if s["meas_tech"] == "LTE":
            lte_s += dt
            if s["network_type"] == "NR_NSA":
                nsa_s += dt
        if not stale:
            if s["rsrp_dbm"] is not None:
                if s["network_type"] in ("LTE", "NR_NSA"):
                    lte.append(s["rsrp_dbm"])
                elif s["network_type"] == "NR_SA":
                    nr_sa.append(s["rsrp_dbm"])
            if s["network_type"] == "NR_NSA" and s["nr_ss_rsrp_dbm"] is not None:
                nsa_leg.append(s["nr_ss_rsrp_dbm"])
        # observed cell changes: consecutive non-stale samples of the same device+SIM, gap ≤ cap
        seq = (s["device_pk"], s["sim_slot"])
        prev = prev_by_seq.get(seq)
        if not stale and s["service_state"] == "in_service":
            cell = ((s["operator_key"], s["cell_id"]) if s["cell_id"] is not None
                    else (s["operator_key"], s["earfcn_arfcn"], s["pci"]))
            if prev is not None and (s["ts"] - prev[0]).total_seconds() <= DT_CAP_S:
                change_time += (s["ts"] - prev[0]).total_seconds()
                if prev[1] != cell:
                    changes += 1
            prev_by_seq[seq] = (s["ts"], cell)
        else:
            prev_by_seq.pop(seq, None)
    cls = sum(w)
    tot_unknown = w_stale + gap
    denom = total + gap
    return {
        "samples": n,
        "time_s": int(round(total)),
        "pct_time_by_tier": [_r(100 * x / cls) if cls > 0 else None for x in w],
        "pct_time_unclassified": _r(100 * w_unk / denom) if denom > 0 else None,
        "pct_time_unknown": _r(100 * tot_unknown / denom) if denom > 0 else None,
        "rsrp": {
            "lte": {"p5": percentile(lte, 5), "p50": percentile(lte, 50), "n": len(lte)},
            "nr_sa": {"p5": percentile(nr_sa, 5), "p50": percentile(nr_sa, 50), "n": len(nr_sa)},
            "nsa_nr_leg": {"p5": percentile(nsa_leg, 5), "p50": percentile(nsa_leg, 50), "n": len(nsa_leg)},
        },
        "oos_minutes": _r(oos_s / 60.0, 2),
        "cell_change_per_hour": _r(changes / (change_time / 3600.0), 2) if change_time > 0 else None,
        "nsa_share_pct": _r(100 * nsa_s / lte_s) if lte_s > 0 else None,
        "data_connected_pct": _r(100 * data_conn_s / total) if total > 0 else None,
        "fallback_pct": _r(100 * fallback_s / in_service_s) if in_service_s > 0 else None,
        "stale_excluded_pct": _r(100 * n_stale / n) if n else None,
        "age_unknown_pct": _r(100 * n_age_unknown / n) if n else None,
    }


def group_by(samples: list, keyfn: Callable) -> dict:
    out: dict = defaultdict(list)
    for s in samples:
        k = keyfn(s)
        if k is not None:
            out[k].append(s)
    return out


def fair_filter(samples: list) -> list:
    """Keep samples in G100 cells measured (non-stale, gridded) by every operator
    present in the set — "fair comparison" (spec 5.3)."""
    ops = {s["operator_key"] for s in samples if s["operator_key"]}
    if len(ops) < 2:
        return samples
    cells = defaultdict(set)
    for s in samples:
        if s["gx"] is not None and not s["flags"] & FLAG_STALE_CELL and s["operator_key"]:
            cells[(s["gx"], s["gy"])].add(s["operator_key"])
    shared = {c for c, o in cells.items() if o >= ops}
    return [s for s in samples if s["gx"] is not None and (s["gx"], s["gy"]) in shared]
