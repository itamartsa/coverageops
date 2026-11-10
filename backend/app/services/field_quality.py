"""
Field reception — sample quality: tier classification (single source of truth),
meas_tech, sentinel normalisation, range table, per-sample validation and the
derived columns (flags, grid cells).

Contract: docs/field/API_CONTRACT.md v2 + v2.1 (v2.1 overrides):
  * tiers are half-open on RSRP (LTE / NR_NSA anchor) or SS-RSRP (NR_SA);
    out_of_service / emergency_only → 0 in every technology; power_off and
    in_service without RSRP → None ("unclassified");
  * a radio value out of range is nulled and the sample is accepted (counted in
    `normalized`); whole-sample rejection only for the closed reason list
    (REJECT_REASONS) incl. out_of_range:lat|lon;
  * staleness (cell_info_age_ms > 10 s) never rejects: it is a flag used at
    query time and applies to signal/cell fields only.

The range table here is mirrored in tests/fixtures/field_tier_vectors.json
(the file shared with the Android team); a test asserts they are identical.
"""
import math
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from app.core.config import settings
from app.models.field import (
    FLAG_AGE_UNKNOWN, FLAG_COARSE, FLAG_OUT_OF_RANGE, FLAG_OUTSIDE_IL, FLAG_SENTINEL,
    FLAG_STALE_CELL, FLAG_STALE_LOC, FLAG_TS_FUTURE,
)
from app.services.field_time import parse_iso

# ── tiers ─────────────────────────────────────────────────────────────────────
TIERS = {
    4: {"key": "excellent", "label_he": "מצוין", "min_dbm": -85.0, "token": "--cov-excellent"},
    3: {"key": "good", "label_he": "טוב", "min_dbm": -95.0, "token": "--cov-good"},
    2: {"key": "medium", "label_he": "בינוני", "min_dbm": -105.0, "token": "--cov-medium"},
    1: {"key": "weak", "label_he": "חלש", "min_dbm": -115.0, "token": "--cov-weak"},
    0: {"key": "marginal", "label_he": "שולי/אין", "min_dbm": None, "token": "--cov-marginal"},
}
UNCLASSIFIED = {"key": "unclassified", "label_he": "לא מסווג", "token": "--cov-unk"}

OOS_STATES = ("out_of_service", "emergency_only")
RSRP_TECHS = ("LTE", "NR_NSA", "NR_SA")


def classify_tier(network_type: Optional[str], rsrp_dbm: Optional[float],
                  service_state: Optional[str]) -> Optional[int]:
    """0..4 or None (unclassified). Pure function of its three inputs; staleness
    is applied where the tier is used, not here."""
    if service_state in OOS_STATES:
        return 0
    if service_state == "power_off":
        return None
    if network_type not in RSRP_TECHS or rsrp_dbm is None:
        return None
    x = float(rsrp_dbm)
    if x >= -85:
        return 4
    if x >= -95:
        return 3
    if x >= -105:
        return 2
    if x >= -115:
        return 1
    return 0


def meas_tech_for(network_type: Optional[str]) -> str:
    return {"LTE": "LTE", "NR_NSA": "LTE", "NR_SA": "NR", "UMTS": "LEGACY",
            "GSM": "LEGACY"}.get(network_type or "", "NONE")


# ── enums & ranges ────────────────────────────────────────────────────────────
ENUMS = {
    "network_type": ("NR_SA", "NR_NSA", "LTE", "UMTS", "GSM", "NONE"),
    "service_state": ("in_service", "emergency_only", "out_of_service", "power_off"),
    "data_state": ("connected", "disconnected", "suspended"),
    "loc_provider": ("gps", "fused", "network", "none"),
    "trigger": ("periodic", "cell_change", "service_change", "movement", "manual", "active_test"),
    "neighbors.rat": ("LTE", "NR", "UMTS", "GSM"),
}
REQUIRED_ENUMS = ("network_type", "service_state")

# [min, max] inclusive. "spec" = FIELD_DASHBOARD_SPEC 3.3 table; "type" = server
# type-safety bounds added by the backend (values that would overflow the DB
# column); both null the value (v2.1 rule 4). lat/lon reject the sample.
RANGES: dict[str, dict] = {
    "lat":               {"min": -90, "max": 90, "source": "spec"},
    "lon":               {"min": -180, "max": 180, "source": "spec"},
    "loc_accuracy_m":    {"min": 0, "max": 50000, "source": "spec"},
    "rsrp_dbm":          {"min": -156, "max": -31, "source": "spec"},
    "rsrq_db":           {"min": -43, "max": 20, "source": "spec"},
    "sinr_db":           {"min": -23, "max": 40, "source": "spec"},
    "rssi_dbm":          {"min": -130, "max": -10, "source": "spec"},
    "battery_pct":       {"min": 0, "max": 100, "source": "spec"},
    "signal_level":      {"min": 0, "max": 4, "source": "spec"},
    "timing_advance":    {"min": 0, "max": 1282, "source": "spec"},
    "timing_advance_us": {"min": 0, "max": 2000, "source": "spec"},
    "cell_info_age_ms":  {"min": 0, "max": 86400000, "source": "spec"},
    "loc_age_ms":        {"min": 0, "max": 86400000, "source": "spec"},
    "sim_slot":          {"min": 0, "max": 3, "source": "spec"},
    "nr_leg.ss_rsrp_dbm": {"min": -156, "max": -31, "source": "spec"},
    "nr_leg.ss_rsrq_db":  {"min": -43, "max": 20, "source": "spec"},
    "nr_leg.ss_sinr_db":  {"min": -23, "max": 40, "source": "spec"},
    "neighbors.rsrp_dbm": {"min": -156, "max": -31, "source": "spec"},
    "neighbors.rsrq_db":  {"min": -43, "max": 20, "source": "spec"},
    "cell_id":           {"min": 0, "max": 68719476735, "source": "type"},   # NCI 36 bit
    "pci":               {"min": 0, "max": 1007, "source": "type"},
    "tac":               {"min": 0, "max": 16777215, "source": "type"},      # 24 bit
    "earfcn_arfcn":      {"min": 0, "max": 3279165, "source": "type"},
    "band":              {"min": 1, "max": 1024, "source": "type"},
    "cqi":               {"min": 0, "max": 15, "source": "type"},
    "altitude_m":        {"min": -1000, "max": 10000, "source": "type"},
    "altitude_msl_m":    {"min": -1000, "max": 10000, "source": "type"},
    "speed_mps":         {"min": 0, "max": 350, "source": "type"},
    "bearing_deg":       {"min": 0, "max": 360, "source": "type"},
    "nr_leg.pci":        {"min": 0, "max": 1007, "source": "type"},
    "nr_leg.arfcn":      {"min": 0, "max": 3279165, "source": "type"},
    "nr_leg.band":       {"min": 1, "max": 1024, "source": "type"},
    "neighbors.pci":     {"min": 0, "max": 1007, "source": "type"},
    "neighbors.earfcn_arfcn": {"min": 0, "max": 3279165, "source": "type"},
    "active_test.latency_ms": {"min": 0, "max": 600000, "source": "type"},
    "active_test.jitter_ms":  {"min": 0, "max": 600000, "source": "type"},
    "active_test.loss_pct":   {"min": 0, "max": 100, "source": "type"},
    "active_test.dl_kbps":    {"min": 0, "max": 100000000, "source": "type"},
    "active_test.ul_kbps":    {"min": 0, "max": 100000000, "source": "type"},
}

INT_FIELDS = {"sim_slot", "cell_id", "pci", "tac", "earfcn_arfcn", "band", "cqi", "timing_advance",
              "timing_advance_us", "signal_level", "cell_info_age_ms", "loc_age_ms", "battery_pct",
              "nr_leg.pci", "nr_leg.arfcn", "nr_leg.band", "neighbors.pci", "neighbors.earfcn_arfcn"}
FLOAT_FIELDS = {"loc_accuracy_m", "altitude_m", "altitude_msl_m", "speed_mps", "bearing_deg",
                "rsrp_dbm", "rsrq_db", "sinr_db", "rssi_dbm", "nr_leg.ss_rsrp_dbm", "nr_leg.ss_rsrq_db",
                "nr_leg.ss_sinr_db", "neighbors.rsrp_dbm", "neighbors.rsrq_db",
                "active_test.latency_ms", "active_test.jitter_ms", "active_test.loss_pct",
                "active_test.dl_kbps", "active_test.ul_kbps"}
BOOL_FIELDS = {"is_roaming", "is_charging", "screen_on"}

SAMPLE_KEYS = frozenset({
    "sample_id", "ts", "lat", "lon", "loc_accuracy_m", "altitude_m", "altitude_msl_m", "speed_mps",
    "bearing_deg", "loc_provider", "loc_age_ms", "network_type", "mcc", "mnc", "operator_name",
    "sim_slot", "is_roaming", "cell_id", "pci", "tac", "earfcn_arfcn", "band", "rsrp_dbm", "rsrq_db",
    "sinr_db", "rssi_dbm", "cqi", "timing_advance", "timing_advance_us", "signal_level",
    "cell_info_age_ms", "data_state", "service_state", "neighbors", "nr_leg", "battery_pct",
    "is_charging", "screen_on", "trigger", "active_test",
})
NEIGHBOR_KEYS = frozenset({"pci", "earfcn_arfcn", "rsrp_dbm", "rsrq_db", "rat"})
NR_LEG_KEYS = frozenset({"pci", "arfcn", "band", "ss_rsrp_dbm", "ss_rsrq_db", "ss_sinr_db"})
ACTIVE_TEST_KEYS = frozenset({"latency_ms", "jitter_ms", "loss_pct", "dl_kbps", "ul_kbps"})

# Android "unavailable" values: Integer.MAX_VALUE, Long.MAX_VALUE, Integer.MIN_VALUE (NR)
SENTINELS = (2147483647, 9223372036854775807, -2147483648)

REJECT_REASONS = ("invalid_sample_id", "invalid_ts", "ts_in_future", "ts_too_old",
                  "invalid_enum:<field>", "out_of_range:lat", "out_of_range:lon")
REJECT_REASON_RE = r"^(invalid_sample_id|invalid_ts|ts_in_future|ts_too_old|invalid_enum:[a-z_.]+|out_of_range:(lat|lon))$"

ISRAEL_BBOX = (29.3, 33.5, 34.0, 36.0)       # lat_min, lat_max, lon_min, lon_max
G100_DLAT, G100_DLON = 0.0009, 0.00106        # ≈ 100 × 100 m at 31.5°N
G2500_DLAT, G2500_DLON = 0.0225, 0.0265       # ≈ 2.5 km
FUTURE_TOLERANCE = timedelta(minutes=5)

HIST_MIN, HIST_STEP, HIST_BINS = -160.0, 2.0, 60


class EnvelopeError(ValueError):
    """Malformed request (→ 422), as opposed to a per-sample rejection."""


# ── grid ──────────────────────────────────────────────────────────────────────
def g100(lat: float, lon: float) -> tuple:
    return math.floor(lon / G100_DLON), math.floor(lat / G100_DLAT)


def g2500(lat: float, lon: float) -> tuple:
    return math.floor(lon / G2500_DLON), math.floor(lat / G2500_DLAT)


def cell_bounds(level: int, gx: int, gy: int) -> tuple:
    """(south, west, north, east) of a grid cell. level 100 / 500 / 2500."""
    if level == 2500:
        return gy * G2500_DLAT, gx * G2500_DLON, (gy + 1) * G2500_DLAT, (gx + 1) * G2500_DLON
    k = 5 if level == 500 else 1
    return (gy * k * G100_DLAT, gx * k * G100_DLON, (gy + 1) * k * G100_DLAT, (gx + 1) * k * G100_DLON)


def in_israel(lat: float, lon: float) -> bool:
    a, b, c, d = ISRAEL_BBOX
    return a <= lat <= b and c <= lon <= d


# ── histogram helpers (aggregates) ────────────────────────────────────────────
def hist_index(x: float) -> int:
    return max(0, min(HIST_BINS - 1, int(math.floor((x - HIST_MIN) / HIST_STEP))))


def hist_percentile(hist: Optional[list], q: float) -> Optional[float]:
    """Percentile (0..100) from a 2-dB histogram, linear inside the bin."""
    if not hist:
        return None
    total = sum(hist)
    if total <= 0:
        return None
    target = q / 100.0 * total
    acc = 0.0
    for i, c in enumerate(hist):
        if c <= 0:
            continue
        if acc + c >= target:
            frac = (target - acc) / c
            return round(HIST_MIN + (i + frac) * HIST_STEP, 2)
        acc += c
    return round(HIST_MIN + HIST_BINS * HIST_STEP, 2)


def percentile(values: list, q: float) -> Optional[float]:
    """Linear-interpolated percentile (numpy 'linear') of raw values."""
    if not values:
        return None
    v = sorted(values)
    pos = (len(v) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 2)


# ── per-sample processing ─────────────────────────────────────────────────────
class _Norm:
    def __init__(self):
        self.flags = 0
        self.normalized = 0

    def number(self, name: str, value: Any, is_int: bool) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.flags |= FLAG_OUT_OF_RANGE
            self.normalized += 1
            return None
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            self.flags |= FLAG_OUT_OF_RANGE
            self.normalized += 1
            return None
        if value in SENTINELS:
            self.flags |= FLAG_SENTINEL
            return None
        r = RANGES.get(name)
        if r is not None and not (r["min"] <= value <= r["max"]):
            self.flags |= FLAG_OUT_OF_RANGE
            self.normalized += 1
            return None
        if is_int:
            if isinstance(value, float):
                if value != int(value):
                    self.flags |= FLAG_OUT_OF_RANGE
                    self.normalized += 1
                    return None
            return int(value)
        return float(value)

    def boolean(self, value: Any) -> Optional[bool]:
        if value is None or isinstance(value, bool):
            return value
        self.flags |= FLAG_OUT_OF_RANGE
        self.normalized += 1
        return None


def _check_keys(obj: Any, allowed: frozenset, where: str) -> None:
    if not isinstance(obj, dict):
        raise EnvelopeError(f"{where}: object expected")
    extra = set(obj) - allowed
    if extra:
        raise EnvelopeError(f"{where}: unknown field(s) {sorted(extra)}")


def _num_field(norm: _Norm, name: str, value: Any) -> Any:
    return norm.number(name, value, name in INT_FIELDS)


def _code_str(value: Any, lengths: tuple) -> Optional[str]:
    if isinstance(value, str) and value.isdigit() and len(value) in lengths:
        return value
    return None


def process_sample(raw: Any, now: datetime, sent_at: Optional[datetime]) -> dict:
    """Validate + normalise one contract Sample.

    Returns {"row": dict|None, "reason": str|None, "sample_id": str|None,
             "normalized": int}. Raises EnvelopeError for structural problems
    (unknown field — privacy guard, spec 9.1)."""
    _check_keys(raw, SAMPLE_KEYS, "sample")
    sid_raw = raw.get("sample_id")
    out = {"row": None, "reason": None, "sample_id": sid_raw if isinstance(sid_raw, str) else None,
           "normalized": 0}

    # 1. sample_id
    try:
        sid = uuid.UUID(sid_raw) if isinstance(sid_raw, str) else None
    except ValueError:
        sid = None
    if sid is None:
        out["reason"] = "invalid_sample_id"
        return out
    # 2. ts
    ts = parse_iso(raw.get("ts"))
    if ts is None:
        out["reason"] = "invalid_ts"
        return out
    if ts > now + FUTURE_TOLERANCE or (sent_at is not None and ts > sent_at + FUTURE_TOLERANCE):
        out["reason"] = "ts_in_future"
        return out
    if ts < now - timedelta(days=settings.FIELD_RAW_RETENTION_DAYS):
        out["reason"] = "ts_too_old"
        return out
    # 3. enums (incl. neighbors[].rat)
    enums = {}
    for f in ("network_type", "service_state", "data_state", "loc_provider", "trigger"):
        v = raw.get(f)
        if v is None:
            if f in REQUIRED_ENUMS:
                out["reason"] = f"invalid_enum:{f}"
                return out
            enums[f] = None
            continue
        if v not in ENUMS[f]:
            out["reason"] = f"invalid_enum:{f}"
            return out
        enums[f] = v
    neighbors_raw = raw.get("neighbors")
    if neighbors_raw is not None and not isinstance(neighbors_raw, list):
        raise EnvelopeError("sample.neighbors: list expected")
    for nb in neighbors_raw or []:
        _check_keys(nb, NEIGHBOR_KEYS, "sample.neighbors[]")
        if nb.get("rat") not in ENUMS["neighbors.rat"]:
            out["reason"] = "invalid_enum:neighbors.rat"
            return out
    nr_leg_raw = raw.get("nr_leg")
    if nr_leg_raw is not None:
        _check_keys(nr_leg_raw, NR_LEG_KEYS, "sample.nr_leg")
    at_raw = raw.get("active_test")
    if at_raw is not None:
        _check_keys(at_raw, ACTIVE_TEST_KEYS, "sample.active_test")

    # 4. lat/lon (the only out_of_range that rejects — v2.1 rule 4)
    lat, lon = raw.get("lat"), raw.get("lon")
    for name, v in (("lat", lat), ("lon", lon)):
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))
                              or not (RANGES[name]["min"] <= v <= RANGES[name]["max"])):
            out["reason"] = f"out_of_range:{name}"
            return out
    if (lat is None) != (lon is None):
        out["reason"] = "out_of_range:lat"
        return out

    norm = _Norm()
    row: dict = {"sample_id": sid, "ts": ts, "received_at": now, **enums}
    if ts > now:
        norm.flags |= FLAG_TS_FUTURE
    row["lat"] = float(lat) if lat is not None else None
    row["lon"] = float(lon) if lon is not None else None
    for f in ("loc_accuracy_m", "altitude_m", "altitude_msl_m", "speed_mps", "bearing_deg", "loc_age_ms",
              "sim_slot", "cell_id", "pci", "tac", "earfcn_arfcn", "band", "rsrp_dbm", "rsrq_db", "sinr_db",
              "rssi_dbm", "cqi", "timing_advance", "timing_advance_us", "signal_level", "cell_info_age_ms",
              "battery_pct"):
        row[f] = _num_field(norm, f, raw.get(f))
    for f in BOOL_FIELDS:
        row[f] = norm.boolean(raw.get(f))
    row["mcc"] = _code_str(raw.get("mcc"), (3,))
    row["mnc"] = _code_str(raw.get("mnc"), (2, 3))
    on = raw.get("operator_name")
    row["operator_name"] = on[:32] if isinstance(on, str) else None
    row["operator_key"] = f"{row['mcc']}-{row['mnc']}" if row["mcc"] and row["mnc"] else None

    # silent fix-ups (spec 3.3): accuracy without a position; >8 neighbours; active_test off-trigger
    if row["lat"] is None:
        row["loc_accuracy_m"] = None
    nbs = []
    for nb in (neighbors_raw or [])[:8]:
        nbs.append({"rat": nb["rat"],
                    "pci": _num_field(norm, "neighbors.pci", nb.get("pci")),
                    "earfcn_arfcn": _num_field(norm, "neighbors.earfcn_arfcn", nb.get("earfcn_arfcn")),
                    "rsrp_dbm": _num_field(norm, "neighbors.rsrp_dbm", nb.get("rsrp_dbm")),
                    "rsrq_db": _num_field(norm, "neighbors.rsrq_db", nb.get("rsrq_db"))})
    row["neighbors"] = nbs if neighbors_raw is not None else None
    nr = {}
    if nr_leg_raw is not None:
        for k in ("pci", "arfcn", "band", "ss_rsrp_dbm", "ss_rsrq_db", "ss_sinr_db"):
            nr[k] = _num_field(norm, f"nr_leg.{k}", nr_leg_raw.get(k))
    row["nr_pci"], row["nr_arfcn"], row["nr_band"] = nr.get("pci"), nr.get("arfcn"), nr.get("band")
    row["nr_ss_rsrp_dbm"], row["nr_ss_rsrq_db"], row["nr_ss_sinr_db"] = (
        nr.get("ss_rsrp_dbm"), nr.get("ss_rsrq_db"), nr.get("ss_sinr_db"))
    if at_raw is not None and enums.get("trigger") == "active_test":
        row["active_test"] = {k: _num_field(norm, f"active_test.{k}", at_raw.get(k))
                              for k in ACTIVE_TEST_KEYS if k in at_raw}
    else:
        row["active_test"] = None

    # derived
    row["tier"] = classify_tier(row["network_type"], row["rsrp_dbm"], row["service_state"])
    row["meas_tech"] = meas_tech_for(row["network_type"])
    flags = norm.flags
    age = row["cell_info_age_ms"]
    if age is None:
        flags |= FLAG_AGE_UNKNOWN
    elif age > settings.FIELD_STALE_CELL_MS:
        flags |= FLAG_STALE_CELL
    if row["loc_age_ms"] is not None and row["loc_age_ms"] > settings.FIELD_MAX_LOC_AGE_MS:
        flags |= FLAG_STALE_LOC
    row["gx"] = row["gy"] = row["gx25"] = row["gy25"] = None
    if row["lat"] is not None:
        acc = row["loc_accuracy_m"]
        on_lattice = (abs(row["lat"] * 100 - round(row["lat"] * 100)) <= 1e-6
                      and abs(row["lon"] * 100 - round(row["lon"] * 100)) <= 1e-6)
        if (acc is not None and acc >= 1000) or on_lattice:
            flags |= FLAG_COARSE
            # coarse definition (v2): no altitude/speed/bearing/TA
            for f in ("altitude_m", "altitude_msl_m", "speed_mps", "bearing_deg", "timing_advance",
                      "timing_advance_us"):
                row[f] = None
        if not in_israel(row["lat"], row["lon"]):
            flags |= FLAG_OUTSIDE_IL
        elif not flags & FLAG_STALE_LOC:
            if not flags & FLAG_COARSE and acc is not None and acc <= 100:
                row["gx"], row["gy"] = g100(row["lat"], row["lon"])
            if acc is None or acc <= 2000:
                if acc is not None:
                    row["gx25"], row["gy25"] = g2500(row["lat"], row["lon"])
    row["flags"] = flags
    out["row"] = row
    out["normalized"] = norm.normalized
    out["sample_id"] = str(sid)
    return out


def is_stale(flags: int) -> bool:
    return bool(flags & FLAG_STALE_CELL)


def effective_tier(tier: Optional[int], flags: int, service_state: Optional[str]) -> Optional[int]:
    """Tier as used by KPIs: a stale sample keeps only its service-state tier
    (v2.1 rule 3 — service_state is always counted). Returns -1 for "unknown"
    (stale signal), None for unclassified."""
    if flags & FLAG_STALE_CELL:
        return 0 if service_state in OOS_STATES else -1
    return tier
