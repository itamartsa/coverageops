"""
Import Israel's civil cellular sites into the `cell_sites` table.

Source: data.gov.il — Ministry of Environmental Protection (המשרד להגנת הסביבה)
  * antennaactive  — active transmission sites (~8,500)
  * antenna_hakama — sites with a construction-permit request (~300)

Two ways in:
  * live      — CKAN datastore API at data.gov.il (refreshed by the ministry ~daily)
  * snapshot  — bundled file app/seed_data/cell_sites_snapshot.json.gz
                (used on first start, and as a fallback if data.gov.il blocks the server)
"""
import gzip
import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.cell_site import CellSite, CellSiteSync, CellSiteTypeDefault
from app.services.itm import itm_to_wgs84

log = logging.getLogger(__name__)

API_URL = "https://data.gov.il/api/3/action/datastore_search"
RESOURCES = {
    "active":       "8935c8e5-ec77-421f-af86-d970583195f8",   # antennaactive
    "construction": "ff398c7e-c522-4ee8-a53a-312b188a573d",   # antenna_hakama
}
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "seed_data" / "cell_sites_snapshot.json.gz"
USER_AGENT = "Mozilla/5.0 (CoverageOps cell-sites importer)"

SITE_TYPES = {
    "תורן קרקעי": "ground_mast", "קרקעי": "ground_mast",
    "תורן על הגג": "rooftop_mast", "תורן על מבנה": "rooftop_mast", "גג": "rooftop_mast",
    "אתר זעיר פנימי": "small_indoor", "אתר זעיר חיצוני": "small_outdoor",
    "אנטנת עוקץ": "sting", "מתקן גישה אלחוטי": "wireless_access", "אנטנה משתפלת": "tilted",
}

# Estimated antenna height (m AGL) per site type — the government dataset has
# no heights. Seeded into `cell_site_type_defaults` (admin-editable); this dict
# is also the fallback when the table is empty/unavailable.
# site_type: (default_height_m, label_he, note_he)
HEIGHT_DEFAULTS: dict[str, tuple[float, str, str]] = {
    "ground_mast":     (25.0, "תורן קרקעי",       "תרנים קרקעיים בישראל נעים לרוב בין 18 ל-36 מ'"),
    "rooftop_mast":    (18.0, "תורן על הגג",      "גובה הבניין + תורן של 3–6 מ'; תלוי מאוד בבניין"),
    "sting":           (15.0, "אנטנת עוקץ",       "על קצה גג/חזית — בקירוב גובה הבניין"),
    "tilted":          (15.0, "אנטנה משתפלת",     "על גג/חזית — בקירוב גובה הבניין"),
    "wireless_access": (6.0,  "מתקן גישה אלחוטי", "small cell על עמוד תאורה/חזית, 4–10 מ'"),
    "small_outdoor":   (6.0,  "אתר זעיר חיצוני",  ""),
    "small_indoor":    (3.0,  "אתר זעיר פנימי",   "בתוך מבנה — לא רלוונטי לכיסוי חוץ"),
    "other":           (15.0, "אחר",              ""),
}

# Rough envelope of Israel + Judea/Samaria + Golan, to drop broken coordinates
_LAT_RANGE = (29.3, 33.5)
_LON_RANGE = (34.2, 35.95)


# ── Fetch ─────────────────────────────────────────────────────────────────────
def _fetch_resource(resource_id: str, page: int = 5000) -> list[dict]:
    rows, offset = [], 0
    while True:
        qs = urllib.parse.urlencode({"resource_id": resource_id, "limit": page, "offset": offset})
        req = urllib.request.Request(f"{API_URL}?{qs}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.load(r)["result"]
        rows += res["records"]
        offset += page
        if offset >= res["total"]:
            return rows


def fetch_live() -> dict:
    return {
        "source": "live",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "active": _fetch_resource(RESOURCES["active"]),
        "construction": _fetch_resource(RESOURCES["construction"]),
    }


def load_snapshot() -> dict:
    with gzip.open(SNAPSHOT_PATH, "rt", encoding="utf-8") as f:
        data = json.load(f)
    data["source"] = "snapshot"
    return data


# ── Normalize ─────────────────────────────────────────────────────────────────
def _operator(he: str) -> str:
    he = he or ""
    if "PHI" in he.upper() or "פרטנר" in he or "הוט" in he:
        return "PHI"
    if "פלאפון" in he:
        return "PELEPHONE"
    if "סלקום" in he:
        return "CELLCOM"
    return "OTHER"


def _techs(v) -> str:
    return ",".join(f"{d}G" for d in sorted(set(re.findall(r"\d", str(v or "")))))


def _date(v):
    if not v:
        return None
    v = str(v)
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if m:
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    return v[:10]


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _str(v, n):
    return None if v in (None, "") else str(v).strip()[:n]


def _to_site(rec: dict, status: str):
    x, y = _num(rec.get("X_ITM")), _num(rec.get("Y_ITM"))
    if not x or not y:
        return None
    lat, lon = itm_to_wgs84(x, y)
    if not (_LAT_RANGE[0] < lat < _LAT_RANGE[1] and _LON_RANGE[0] < lon < _LON_RANGE[1]):
        return None
    active = status == "active"
    type_he = rec.get("סוג אתר") if active else rec.get("סוג המוקד")
    s = CellSite(
        status=status,
        operator=_operator(rec.get("חברה")),
        operator_he=_str(rec.get("חברה"), 64),
        site_code=_str(rec.get("מס' אתר") if active else rec.get("מספר האתר"), 32),
        city=_str(rec.get("עיר") if active else rec.get("ישוב"), 128),
        address=_str(rec.get("כתובת האתר") if active else rec.get("כתובת + תאור"), 256),
        authority=_str(rec.get("רשות מקומית"), 128),
        jurisdiction=_str(rec.get("תחום שיפוט"), 128),
        site_type=SITE_TYPES.get((type_he or "").strip(), "other"),
        site_type_he=_str(type_he, 64),
        lat=round(lat, 6), lon=round(lon, 6), x_itm=x, y_itm=y,
        source_id=int(_num(rec.get("ID")) or 0) or None,
    )
    if active:
        s.technologies = _techs(rec.get("טכנולוגיית שידור"))
        s.construction_permit_date = _date(rec.get("תאריך היתר הקמה"))
        s.operation_permit_date = _date(rec.get("תאריך היתר הפעלה"))
        s.last_periodic_check = _date(rec.get("בדיקה תקופתית אחרונה"))
        s.radiation_permit = _str(rec.get("היתר קרינה"), 32)
        s.max_power_uw_cm2 = _num(rec.get("עוצמה מרבית תיאורטית בµW לסמר"))
        s.max_pct_of_threshold = _num(rec.get("תוצאה מירבית ב% ביחס לסף הבריאות"))
        s.max_point_desc = _str(rec.get("תאור נקודה בה התקבלה תוצאה מירבית"), 1000)
        s.construction_permit_url = _str(rec.get("קובץ הקמה"), 256)
        s.operation_permit_url = _str(rec.get("קובץ הפעלה"), 256)
    else:
        s.request_date = _date(rec.get("תאריך הגשת הבקשה"))
        s.permit_type = _str(rec.get("סוג  היתר") or rec.get("סוג היתר"), 64)
        s.reference_no = _str(rec.get("מס' סימוכין"), 32)
        s.technologies = ""
    return s


# ── Estimated heights ─────────────────────────────────────────────────────────
def seed_type_defaults(db: Session) -> None:
    """Insert missing rows of `cell_site_type_defaults` (never overwrites admin edits)."""
    have = {t for (t,) in db.query(CellSiteTypeDefault.site_type).all()}
    added = False
    for t, (h, label, note) in HEIGHT_DEFAULTS.items():
        if t not in have:
            db.add(CellSiteTypeDefault(site_type=t, default_height_m=h, label_he=label, note_he=note))
            added = True
    if added:
        db.commit()


def height_defaults_map(db: Session) -> dict[str, float]:
    """site_type -> estimated height (DB values over the built-in dict)."""
    out = {t: v[0] for t, v in HEIGHT_DEFAULTS.items()}
    try:
        for t, h in db.query(CellSiteTypeDefault.site_type, CellSiteTypeDefault.default_height_m).all():
            out[t] = h
    except Exception as e:  # table missing (very old DB) — use the dict
        log.warning("cell_site_type_defaults unavailable (%s) — using built-in defaults", e)
        db.rollback()
    return out


def estimated_height_for(site_type: str | None, db: Session | None = None,
                         _cache: dict[str, float] | None = None) -> float:
    """Estimated antenna height for a site type (DB table, falling back to HEIGHT_DEFAULTS)."""
    m = _cache if _cache is not None else (height_defaults_map(db) if db is not None
                                           else {t: v[0] for t, v in HEIGHT_DEFAULTS.items()})
    return m.get(site_type or "other", m.get("other", HEIGHT_DEFAULTS["other"][0]))


def _height_key(operator, site_code, status, source_id):
    """Identity used to carry manual heights across a full re-import."""
    return (operator, site_code if site_code else f"#src{source_id}", status)


def backfill_estimated_heights(db: Session) -> int:
    """Rows with no height yet (e.g. right after the columns were added) get the estimate."""
    defaults = height_defaults_map(db)
    n = 0
    for t, h in defaults.items():
        n += (db.query(CellSite)
                .filter(CellSite.ant_height_m.is_(None), CellSite.site_type == t)
                .update({CellSite.ant_height_m: h, CellSite.height_source: "estimated"},
                        synchronize_session=False))
    # unknown/NULL types
    n += (db.query(CellSite)
            .filter(CellSite.ant_height_m.is_(None))
            .update({CellSite.ant_height_m: defaults.get("other", 15.0),
                     CellSite.height_source: "estimated"}, synchronize_session=False))
    db.commit()
    if n:
        log.info("cell_sites: backfilled estimated height on %d rows", n)
    return n


# ── Load into DB ──────────────────────────────────────────────────────────────
def import_data(db: Session, data: dict) -> CellSiteSync:
    """Replace the whole table with `data` (atomic).

    Antenna heights: every new row gets the estimate for its site type;
    heights entered manually by users are carried over (matched by
    operator + site_code + status).
    """
    defaults = height_defaults_map(db)
    manual = {
        _height_key(r.operator, r.site_code, r.status, r.source_id):
            (r.ant_height_m, r.height_note, r.height_updated_at, r.height_updated_by)
        for r in db.query(CellSite.operator, CellSite.site_code, CellSite.status, CellSite.source_id,
                          CellSite.ant_height_m, CellSite.height_note,
                          CellSite.height_updated_at, CellSite.height_updated_by)
                   .filter(CellSite.height_source == "manual").all()
    }
    sites, skipped = [], 0
    counts = {"active": 0, "construction": 0}
    for status in ("active", "construction"):
        for rec in data.get(status, []):
            s = _to_site(rec, status)
            if s is None:
                skipped += 1
                continue
            m = manual.get(_height_key(s.operator, s.site_code, s.status, s.source_id))
            if m:
                s.ant_height_m, s.height_note, s.height_updated_at, s.height_updated_by = m
                s.height_source = "manual"
            else:
                s.ant_height_m = estimated_height_for(s.site_type, _cache=defaults)
                s.height_source = "estimated"
            sites.append(s)
            counts[status] += 1

    if counts["active"] < 1000:
        raise ValueError(f"Suspiciously few active sites ({counts['active']}) — aborting import")

    db.query(CellSite).delete()
    db.bulk_save_objects(sites)
    sync = CellSiteSync(
        source=data.get("source"),
        data_fetched_at=data.get("fetched_at"),
        active_count=counts["active"],
        construction_count=counts["construction"],
        skipped_count=skipped,
    )
    db.add(sync)
    db.commit()
    log.info("cell_sites imported: %s (skipped %d, source=%s)", counts, skipped, sync.source)
    return sync


def sync_live_or_snapshot(db: Session) -> CellSiteSync:
    """Try data.gov.il; fall back to the bundled snapshot."""
    try:
        return import_data(db, fetch_live())
    except Exception as e:  # network blocked, API change, ...
        log.warning("live cell-sites fetch failed (%s) — using snapshot", e)
        sync = import_data(db, load_snapshot())
        sync.note = f"live fetch failed: {e}"[:500]
        db.commit()
        return sync


def ensure_loaded(db: Session) -> None:
    """Called at startup: seed height defaults; if the table is empty, load the
    bundled snapshot; otherwise backfill estimated heights on rows that lack one."""
    seed_type_defaults(db)
    if db.query(CellSite.id).first() is None and SNAPSHOT_PATH.exists():
        import_data(db, load_snapshot())
    else:
        backfill_estimated_heights(db)
