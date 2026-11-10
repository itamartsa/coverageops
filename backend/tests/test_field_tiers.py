"""Shared tier vectors (tests/fixtures/field_tier_vectors.json) — single source of
truth for the dashboard and the Android app (contract v2 / v2.1)."""
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from app.services.field_quality import (
    RANGES, REJECT_REASON_RE, classify_tier, meas_tech_for, process_sample,
)

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "field_tier_vectors.json").read_text("utf-8"))


def _sample(**kw):
    s = {"sample_id": str(uuid.uuid4()), "ts": datetime.utcnow().isoformat() + "Z", "network_type": "LTE",
         "service_state": "in_service", "lat": 32.0123, "lon": 34.8456, "loc_accuracy_m": 5.0}
    s.update(kw)
    return s


def test_vector_file_schema():
    assert VECTORS["schema"] == "coverageops.field_tier_vectors"
    assert isinstance(VECTORS["version"], int)
    ids = [v["id"] for v in VECTORS["vectors"]]
    assert len(ids) == len(set(ids)) and len(ids) >= 28
    for v in VECTORS["vectors"]:
        assert v["expected_tier"] in (0, 1, 2, 3, 4, None)
        assert v["rsrp_dbm"] is None or isinstance(v["rsrp_dbm"], (int, float))


@pytest.mark.parametrize("v", VECTORS["vectors"], ids=lambda v: v["id"])
def test_tier_vector(v):
    assert classify_tier(v["network_type"], v["rsrp_dbm"], v["service_state"]) == v["expected_tier"]


@pytest.mark.parametrize("v", VECTORS["vectors"], ids=lambda v: v["id"])
def test_tier_vector_through_ingest_normalisation(v):
    """The same vectors through the server's sample processing (nr_leg/rssi/cell age included)."""
    kw = {k: v[k] for k in ("network_type", "service_state", "rsrp_dbm", "nr_leg", "rssi_dbm", "cell_info_age_ms")
          if k in v}
    out = process_sample(_sample(**kw), datetime.utcnow(), None)
    assert out["reason"] is None
    assert out["row"]["tier"] == v["expected_tier"]


@pytest.mark.parametrize("v", VECTORS["normalization"]["vectors"], ids=lambda v: v["id"])
def test_normalization_vectors(v):
    kw = {"network_type": v["network_type"], "service_state": v["service_state"]}
    if v["field"].startswith("nr_leg."):
        kw["nr_leg"] = {v["field"].split(".")[1]: v["input"]}
        kw["rsrp_dbm"] = v.get("rsrp_dbm")
    else:
        kw[v["field"]] = v["input"]
    out = process_sample(_sample(**kw), datetime.utcnow(), None)
    assert out["reason"] is None and out["normalized"] == 0          # a sentinel is not "out of range"
    row = out["row"]
    col = "nr_ss_rsrp_dbm" if v["field"] == "nr_leg.ss_rsrp_dbm" else v["field"]
    assert row[col] is v["expected"]
    assert row["flags"] & 128
    assert row["tier"] == v["expected_tier"]


def test_range_table_matches_server():
    js = VECTORS["ranges"]["fields"]
    assert js == {k: {"min": r["min"], "max": r["max"], "source": r["source"]} for k, r in RANGES.items()}


@pytest.mark.parametrize("v", VECTORS["ranges"]["range_vectors"], ids=lambda v: v["id"])
def test_range_vectors(v):
    out = process_sample(_sample(**{v["field"]: v["input"]}), datetime.utcnow(), None)
    if "reject" in v:
        assert out["reason"] == v["reject"]
        return
    assert out["reason"] is None
    assert out["row"][v["field"]] == v["expected"]
    assert bool(out["normalized"]) is v["normalized"]


def test_meas_tech_keys():
    assert [meas_tech_for(x) for x in ("LTE", "NR_NSA", "NR_SA", "UMTS", "GSM", "NONE", None)] == \
        ["LTE", "LTE", "NR", "LEGACY", "LEGACY", "NONE", "NONE"]


def test_reject_reason_regex_is_the_closed_list():
    ok = ["invalid_sample_id", "invalid_ts", "ts_in_future", "ts_too_old", "invalid_enum:network_type",
          "invalid_enum:neighbors.rat", "out_of_range:lat", "out_of_range:lon"]
    bad = ["out_of_range:rsrp_dbm", "stale", "duplicate"]
    assert all(re.match(REJECT_REASON_RE, r) for r in ok)
    assert not any(re.match(REJECT_REASON_RE, r) for r in bad)
