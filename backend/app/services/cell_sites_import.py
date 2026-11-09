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

from app.models.cell_site import (
    CellSite, CellSiteBandDefault, CellSiteSync, CellSiteTypeDefault, sync_rf_source,
)
from app.models.user import Site
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

# Estimated EIRP (dBm) per site type — the dataset has no RF data either.
# Seeded into `cell_site_type_defaults.default_tx_power_dbm` (admin-editable).
TX_POWER_DEFAULTS: dict[str, float] = {
    "ground_mast": 43.0, "rooftop_mast": 43.0, "sting": 40.0, "tilted": 40.0,
    "wireless_access": 30.0, "small_outdoor": 20.0, "small_indoor": 20.0, "other": 40.0,
}

# Representative band (MHz) per technology generation — seeded into
# `cell_site_band_defaults` (admin-editable); fallback when the table is empty.
# generation: (frequency_mhz, label_he)
BAND_DEFAULTS: dict[str, tuple[int, str]] = {
    "2G": (900,  "2G – GSM 900"),
    "3G": (2100, "3G – UMTS 2100"),
    "4G": (1800, "4G – LTE 1800"),
    "5G": (3500, "5G – NR 3500"),
}
FALLBACK_BAND_MHZ = 900


def band_label(generation: str, frequency_mhz: int) -> str:
    """Label for an admin-changed band, e.g. "4G – 2300 MHz"."""
    return f"{generation} – {int(frequency_mhz)} MHz"     # site with no known technology (e.g. construction requests)

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
            db.add(CellSiteTypeDefault(site_type=t, default_height_m=h, label_he=label, note_he=note,
                                       default_tx_power_dbm=TX_POWER_DEFAULTS.get(t)))
            added = True
    # tx power column added later: fill it only where still NULL (keeps admin edits)
    for row in db.query(CellSiteTypeDefault).filter(CellSiteTypeDefault.default_tx_power_dbm.is_(None)).all():
        row.default_tx_power_dbm = TX_POWER_DEFAULTS.get(row.site_type, TX_POWER_DEFAULTS["other"])
        added = True
    if added:
        db.commit()
    seed_band_defaults(db)


def seed_band_defaults(db: Session) -> None:
    """Insert missing rows of `cell_site_band_defaults` (never overwrites admin edits)."""
    have = {g for (g,) in db.query(CellSiteBandDefault.generation).all()}
    added = False
    for g, (mhz, label) in BAND_DEFAULTS.items():
        if g not in have:
            db.add(CellSiteBandDefault(generation=g, frequency_mhz=mhz, label_he=label))
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


# ── Estimated RF (EIRP + frequency) ──────────────────────────────────────────
def tx_power_defaults_map(db: Session) -> dict[str, float]:
    """site_type -> estimated EIRP dBm (DB values over TX_POWER_DEFAULTS)."""
    out = dict(TX_POWER_DEFAULTS)
    try:
        for t, p in db.query(CellSiteTypeDefault.site_type, CellSiteTypeDefault.default_tx_power_dbm).all():
            if p is not None:
                out[t] = p
    except Exception as e:
        log.warning("cell_site_type_defaults unavailable (%s) — using built-in tx power", e)
        db.rollback()
    return out


def estimated_tx_power_for(site_type: str | None, db: Session | None = None,
                           _cache: dict[str, float] | None = None) -> float:
    """Estimated EIRP (dBm) for a site type (DB table, falling back to TX_POWER_DEFAULTS)."""
    m = _cache if _cache is not None else (tx_power_defaults_map(db) if db is not None
                                           else dict(TX_POWER_DEFAULTS))
    return m.get(site_type or "other", m.get("other", TX_POWER_DEFAULTS["other"]))


def band_defaults_map(db: Session | None = None) -> dict[str, int]:
    """generation ("4G") -> MHz (DB values over BAND_DEFAULTS)."""
    out = {g: v[0] for g, v in BAND_DEFAULTS.items()}
    if db is None:
        return out
    try:
        for g, f in db.query(CellSiteBandDefault.generation, CellSiteBandDefault.frequency_mhz).all():
            if f:
                out[g] = int(f)
    except Exception as e:
        log.warning("cell_site_band_defaults unavailable (%s) — using built-in bands", e)
        db.rollback()
    return out


def bands_for(technologies: str | None, db: Session | None = None,
              _cache: dict[str, int] | None = None) -> list[int]:
    """Technologies "2G,4G,5G" -> sorted unique MHz list, e.g. [900, 1800, 3500]. Unknown generations ignored."""
    m = _cache if _cache is not None else band_defaults_map(db)
    gens = {g.strip().upper() for g in (technologies or "").split(",") if g.strip()}
    return sorted({m[g] for g in gens if g in m})


def default_band(technologies: str | None, db: Session | None = None,
                 _cache: dict[str, int] | None = None) -> int:
    """Lowest supported band (best propagation); FALLBACK_BAND_MHZ if none is known."""
    b = bands_for(technologies, db, _cache)
    return b[0] if b else FALLBACK_BAND_MHZ


def backfill_estimated_rf(db: Session) -> int:
    """Fill NULL tx_power_dbm / frequency_mhz with estimates (e.g. right after the columns were added)."""
    tx = tx_power_defaults_map(db)
    n = 0
    for t, p in tx.items():
        n += (db.query(CellSite)
                .filter(CellSite.tx_power_dbm.is_(None), CellSite.site_type == t)
                .update({CellSite.tx_power_dbm: p}, synchronize_session=False))
    n += (db.query(CellSite)
            .filter(CellSite.tx_power_dbm.is_(None))
            .update({CellSite.tx_power_dbm: tx.get("other", TX_POWER_DEFAULTS["other"])},
                    synchronize_session=False))
    bands = band_defaults_map(db)
    techs = [t for (t,) in db.query(CellSite.technologies)
                             .filter(CellSite.frequency_mhz.is_(None)).distinct().all()]
    for t in techs:
        col = CellSite.technologies.is_(None) if t is None else CellSite.technologies == t
        n += (db.query(CellSite)
                .filter(CellSite.frequency_mhz.is_(None), col)
                .update({CellSite.frequency_mhz: default_band(t, _cache=bands)},
                        synchronize_session=False))
    db.commit()
    if n:
        log.info("cell_sites: backfilled estimated RF on %d rows/fields", n)
    return n


def _primary_key(operator, site_code, source_id):
    """Identity of a physical site across re-imports: operator + site code
    (status is left out so a site moving construction -> active keeps its data)."""
    return (operator, site_code if site_code else f"#src{source_id}")


def _height_key(operator, site_code, status, source_id):
    """Status-qualified identity — used only when the primary key collides."""
    return (*_primary_key(operator, site_code, source_id), status)


class _IdentityIndex:
    """Maps rows of the *old* table to rows of the new import.

    Rows are matched on (operator, site_code). Where that key is not unique —
    in the old table or in the new data — the status-qualified key is used
    instead, so two rows sharing a site code never swap their values.
    """

    def __init__(self, old_rows):
        """old_rows: iterable of (old_id, operator, site_code, status, source_id)."""
        from collections import Counter
        old_rows = list(old_rows)
        cnt = Counter(_primary_key(op, code, src) for _i, op, code, _st, src in old_rows)
        self.unique = {}
        self.qualified = {}
        for oid, op, code, st, src in old_rows:
            pk = _primary_key(op, code, src)
            if cnt[pk] == 1:
                self.unique[pk] = oid
            self.qualified[_height_key(op, code, st, src)] = oid

    @staticmethod
    def new_counts(new_rows):
        """new_rows: iterable of (operator, site_code, source_id)."""
        from collections import Counter
        return Counter(_primary_key(op, code, src) for op, code, src in new_rows)

    def lookup(self, operator, site_code, status, source_id, new_counts) -> int | None:
        pk = _primary_key(operator, site_code, source_id)
        if new_counts.get(pk, 0) == 1 and pk in self.unique:
            return self.unique[pk]
        return self.qualified.get(_height_key(operator, site_code, status, source_id))


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

    Antenna heights and RF (EIRP / frequency, each with its own source):
    every new row gets the estimate for its site type / technologies; values
    entered manually are carried over per field (see _IdentityIndex for the
    matching). Sites promoted from a cell site (`sites.cell_site_id`) are
    re-linked to the new row.
    """
    defaults = height_defaults_map(db)
    tx_defaults = tx_power_defaults_map(db)
    bands = band_defaults_map(db)

    index = _IdentityIndex(db.query(CellSite.id, CellSite.operator, CellSite.site_code,
                                    CellSite.status, CellSite.source_id).all())
    manual_h = {r.id: (r.ant_height_m, r.height_note, r.height_updated_at, r.height_updated_by)
                for r in db.query(CellSite.id, CellSite.ant_height_m, CellSite.height_note,
                                  CellSite.height_updated_at, CellSite.height_updated_by)
                           .filter(CellSite.height_source == "manual").all()}
    manual_rf = {r.id: r for r in
                 db.query(CellSite.id, CellSite.tx_power_dbm, CellSite.tx_power_source,
                          CellSite.frequency_mhz, CellSite.frequency_source, CellSite.rf_note,
                          CellSite.rf_updated_at, CellSite.rf_updated_by)
                   .filter((CellSite.tx_power_source == "manual")
                           | (CellSite.frequency_source == "manual")).all()}
    links = db.query(Site.id, Site.cell_site_id).filter(Site.cell_site_id.isnot(None)).all()

    sites, skipped = [], 0
    counts = {"active": 0, "construction": 0}
    for status in ("active", "construction"):
        for rec in data.get(status, []):
            s = _to_site(rec, status)
            if s is None:
                skipped += 1
                continue
            sites.append(s)
            counts[status] += 1

    if counts["active"] < 1000:
        raise ValueError(f"Suspiciously few active sites ({counts['active']}) — aborting import")

    new_counts = _IdentityIndex.new_counts((s.operator, s.site_code, s.source_id) for s in sites)
    for s in sites:
        old_id = index.lookup(s.operator, s.site_code, s.status, s.source_id, new_counts)
        h = manual_h.get(old_id)
        if h:
            s.ant_height_m, s.height_note, s.height_updated_at, s.height_updated_by = h
            s.height_source = "manual"
        else:
            s.ant_height_m = estimated_height_for(s.site_type, _cache=defaults)
            s.height_source = "estimated"
        rf = manual_rf.get(old_id)
        if rf is not None and rf.tx_power_source == "manual" and rf.tx_power_dbm is not None:
            s.tx_power_dbm, s.tx_power_source = rf.tx_power_dbm, "manual"
        else:
            s.tx_power_dbm = estimated_tx_power_for(s.site_type, _cache=tx_defaults)
            s.tx_power_source = "estimated"
        if rf is not None and rf.frequency_source == "manual" and rf.frequency_mhz is not None:
            s.frequency_mhz, s.frequency_source = rf.frequency_mhz, "manual"
        else:
            s.frequency_mhz = default_band(s.technologies, _cache=bands)
            s.frequency_source = "estimated"
        if rf is not None:
            s.rf_note, s.rf_updated_at, s.rf_updated_by = rf.rf_note, rf.rf_updated_at, rf.rf_updated_by
        sync_rf_source(s)

    db.query(CellSite).delete()
    db.bulk_save_objects(sites)
    if links:
        _relink_sites(db, index, new_counts, links)
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


def _relink_sites(db: Session, index: "_IdentityIndex", new_counts, links: list) -> None:
    """Point promoted Sites at the re-imported cell rows (ids change on every import)."""
    db.flush()
    old_to_new = {}
    for cid, op, code, st, src in db.query(CellSite.id, CellSite.operator, CellSite.site_code,
                                           CellSite.status, CellSite.source_id).all():
        oid = index.lookup(op, code, st, src, new_counts)
        if oid is not None:
            old_to_new[oid] = cid
    for site_id, old_cell_id in links:
        db.query(Site).filter(Site.id == site_id).update(
            {Site.cell_site_id: old_to_new.get(old_cell_id)}, synchronize_session=False)


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
    """Called at startup: seed height/EIRP/band defaults; if the table is empty, load
    the bundled snapshot; otherwise backfill estimated heights/RF on rows that lack them."""
    seed_type_defaults(db)
    if db.query(CellSite.id).first() is None and SNAPSHOT_PATH.exists():
        import_data(db, load_snapshot())
    else:
        backfill_estimated_heights(db)
        backfill_estimated_rf(db)
