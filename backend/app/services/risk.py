"""
Operational Risk & Recommendations
──────────────────────────────────
Single source of truth for the communication-risk rating and the Hebrew
operational recommendations produced for both use-cases:

  1. Area coverage  – build_area_recommendations()
  2. Route / cross-section – build_route_recommendations()

Both the coverage engine and the API report layer MUST call assess_risk()
from here so that the same inputs always yield the same risk level.

Risk rule-set
─────────────
Union (= the stricter) of the two rule-sets that previously lived in
coverage_engine._assess_risk (route: covered %, longest dead zone) and in
api/coverage.get_report (area: covered %, minimum RSSI, weak/marginal %).
A level is raised as soon as ANY criterion of that level is met.

  Level     covered_pct   max_dead_km   weak_pct   rssi_min below threshold
  CRITICAL  < 50          > 3           > 30       > 15 dB
  HIGH      < 70          > 2           > 20       > 10 dB
  MEDIUM    < 85          > 1           > 10       >  5 dB
  LOW       otherwise

`covered_pct` is the operational coverage, i.e. the share of cells/points
whose RSSI is at or above the site's rx_threshold.

The RSSI criterion is RELATIVE to the receiver threshold (rx_threshold) and
applies to ROUTES only: one point deep below the threshold is a real dead spot
on the path. Area analyses do not pass it — the share of the area below the
threshold is already measured by covered_pct, and fixed dBm limits (the old
-115/-110/-105) rated well-covered areas CRITICAL whenever a few shadowed
cells existed, independent of the site's actual threshold.
"""
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ── Thresholds ─────────────────────────────────────────────────────────────────

# (level, score, covered_pct_below, max_dead_km_above, weak_pct_above,
#  rssi_deficit_db_above)  – deficit = rx_threshold − rssi_min
RISK_RULES: Tuple[Tuple[str, int, float, float, float, float], ...] = (
    ("CRITICAL", 4, 50.0, 3.0, 30.0, 15.0),
    ("HIGH",     3, 70.0, 2.0, 20.0, 10.0),
    ("MEDIUM",   2, 85.0, 1.0, 10.0,  5.0),
)
RISK_LOW: Tuple[str, int] = ("LOW", 1)

RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


# ── Risk rating ────────────────────────────────────────────────────────────────

def assess_risk(
    covered_pct:        float,
    max_dead_length_km: float           = 0.0,
    weak_pct:           float           = 0.0,
    rssi_min:           Optional[float] = None,
    threshold_dbm:      Optional[float] = None,
) -> Tuple[str, int]:
    """
    Four-level operational risk rating.

    Args:
        covered_pct:        % of cells/points with RSSI ≥ rx_threshold
        max_dead_length_km: longest continuous dead zone along a route (km);
                            0 for area analyses
        weak_pct:           % of cells/points in the "weak" + "marginal" levels
        rssi_min:           minimum RSSI (dBm) over ALL in-radius cells / route
                            points, including "none" (< -110 dBm) – i.e.
                            CoverageStats/CrossSectionStats.rssi_min_all, not the
                            display-only any-signal rssi_min.
                            None or NaN = criterion not applied
        threshold_dbm:      receiver threshold (rx_threshold) the deficit is
                            measured against; the RSSI criterion applies only
                            when BOTH rssi_min and threshold_dbm are given
                            (routes). Area analyses pass neither.

    Returns:
        (level, score) – level in LOW/MEDIUM/HIGH/CRITICAL, score 1..4
    """
    if rssi_min is not None and rssi_min != rssi_min:   # NaN → unknown
        rssi_min = None
    deficit = (threshold_dbm - rssi_min
               if rssi_min is not None and threshold_dbm is not None else None)
    for level, score, cov_lt, dead_gt, weak_gt, deficit_gt in RISK_RULES:
        if (covered_pct < cov_lt
                or max_dead_length_km > dead_gt
                or weak_pct > weak_gt
                or (deficit is not None and deficit > deficit_gt)):
            return level, score
    return RISK_LOW


def operational_summary(level: str) -> Dict[str, str]:
    """Hebrew operational status + conclusion for a risk level."""
    if level in ("CRITICAL", "HIGH"):
        return {
            "status":     "בעייתי",
            "conclusion": "כיסוי הרדיו באזור הניתוח בעייתי. נדרשת פעולה מיידית לשיפור הכיסוי.",
        }
    if level == "MEDIUM":
        return {
            "status":     "גבולי",
            "conclusion": "כיסוי הרדיו באזור הניתוח גבולי. מומלץ ביצוע פעולות שיפור בהקדם.",
        }
    return {
        "status":     "תקין",
        "conclusion": "כיסוי הרדיו באזור הניתוח תקין. אין צורך בפעולות מיידיות.",
    }


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _dem_warning(dem_source: Optional[str], dem_valid_pct: Optional[float]) -> Optional[str]:
    """Warning text when the result was not computed on (complete) real terrain."""
    if dem_source != "terrarium":
        return (
            "אזהרה: החישוב בוצע ללא נתוני טופוגרפיה אמיתיים (מודל סימולציה בלבד) – "
            "התוצאה אינה משקפת חסימות טרן בפועל ואין להסתמך עליה לתכנון מבצעי. "
            "יש להריץ מחדש כאשר נתוני הגבהים זמינים."
        )
    if dem_valid_pct is not None and dem_valid_pct < 95.0:
        return (
            f"אזהרה: נתוני הגבהים חלקיים ({dem_valid_pct:.1f}% מהשטח) – "
            "באזורים ללא נתונים הונח שטח מישורי, ולכן הכיסוי שם עשוי להיות אופטימי."
        )
    return None


# ── Route (cross-section) recommendations ──────────────────────────────────────

def build_route_recommendations(
    site:            Any,
    covered_pct:     float,
    dead_zones:      Sequence[Any],
    risk_level:      str,
    rssi_min:        float         = -999.0,
    rssi_avg:        float         = -999.0,
    total_length_km: float         = 0.0,
    mode:            str           = "DTM",
    dem_source:      Optional[str] = None,
    dem_valid_pct:   Optional[float] = None,
) -> List[str]:
    """
    Generate Hebrew operational recommendations from cross-section results.
    Rules are ordered: data-quality warning, critical blockers, improvements,
    then positives.

    `site` is any object with ant_height / frequency / rx_threshold attributes
    (coverage_engine.SiteParams); `dead_zones` are coverage_engine.DeadZone.
    When dem_source is None no data-quality warning is emitted (legacy callers).
    """
    recs: List[str] = []

    # ── 0. Data-quality warning ───────────────────────────────────────────────
    if dem_source is not None:
        warn = _dem_warning(dem_source, dem_valid_pct)
        if warn:
            recs.append(warn)

    # ── 1. Overall risk verdict ───────────────────────────────────────────────
    if risk_level == "CRITICAL":
        recs.append(
            f"הכיסוי קריטי ({covered_pct:.1f}% מהציר) – אסור להסתמך על קשר ישיר. "
            "יש לפרוס ממסר/ים לפני הפעלה מבצעית בציר זה."
        )
    elif risk_level == "HIGH":
        recs.append(
            f"הכיסוי גבולי-גבוה ({covered_pct:.1f}%) – קיים סיכון לניתוקי קשר. "
            "מומלץ להצטייד בממסר נייד ולתכנן נקודות תצפית חלופיות לאורך הציר."
        )
    elif risk_level == "MEDIUM":
        recs.append(
            f"הכיסוי גבולי ({covered_pct:.1f}%) – ייתכנו פערי קשר מקומיים. "
            "מומלץ לאמת בשטח לפני פעילות קריטית."
        )
    else:
        recs.append(
            f"הכיסוי תקין ({covered_pct:.1f}%) – ניתן להסתמך על קשר ישיר לאורך רוב הציר."
        )

    # ── 2. Dead-zone analysis ─────────────────────────────────────────────────
    if dead_zones:
        longest = max(dead_zones, key=lambda dz: dz.length_km)
        mid_lat  = (longest.start_lat + longest.end_lat) / 2
        mid_lon  = (longest.start_lon + longest.end_lon) / 2

        if len(dead_zones) == 1:
            recs.append(
                f"זוהה אזור מת אחד בין ק\"מ {longest.start_km:.1f} ל-{longest.end_km:.1f} "
                f"(אורך {longest.length_km:.2f} ק\"מ). "
                f"מיקום ממסר מומלץ: {mid_lat:.4f}°N, {mid_lon:.4f}°E."
            )
        else:
            total_dead = sum(dz.length_km for dz in dead_zones)
            dead_pct   = round(total_dead / total_length_km * 100, 1) if total_length_km else 0
            recs.append(
                f"זוהו {len(dead_zones)} אזורי מתים בסה\"כ {total_dead:.2f} ק\"מ "
                f"({dead_pct}% מהציר). "
                f"האזור הקריטי ביותר: ק\"מ {longest.start_km:.1f}–{longest.end_km:.1f} "
                f"({longest.length_km:.2f} ק\"מ) — ממסר מומלץ ב-{mid_lat:.4f}°N, {mid_lon:.4f}°E."
            )

        # Flag extremely long dead zones separately
        if longest.length_km > 3.0:
            recs.append(
                f"אזור מת קריטי של {longest.length_km:.1f} ק\"מ – "
                "ממסר קבוע נדרש, לא מספיק ממסר נייד."
            )
        elif longest.length_km > 1.5:
            recs.append(
                "אורך אזור המת הגדול ביותר חורג מ-1.5 ק\"מ – "
                "שקול ממסר קבוע לתמיכה מבצעית רציפה."
            )

    # ── 3. RSSI margin relative to rx_threshold ───────────────────────────────
    margin_min = rssi_min - site.rx_threshold
    margin_avg = rssi_avg - site.rx_threshold

    if rssi_min > -900:   # valid value
        if margin_min < 3:
            recs.append(
                f"עוצמת האות המינימלית ({rssi_min} dBm) קרובה מאוד לסף הקבלה "
                f"({site.rx_threshold} dBm, מרווח: {margin_min:.1f} dB) – "
                "שולי מבצעית; שינויי מזג אוויר או תנועת כלי רכב עלולים לגרום ניתוק."
            )
        elif margin_min < 8:
            recs.append(
                f"מרווח האות המינימלי ({margin_min:.1f} dB מהסף) נמוך – "
                "מומלץ להגביה את האנטנה או להוריד את סף הקבלה אם הרדיו מאפשר."
            )

    if rssi_avg > -900 and margin_avg < 10:
        recs.append(
            f"ממוצע עוצמת האות ({rssi_avg} dBm) קרוב לסף – "
            "בשטח הרים ומבנים הפועלים כמחסומים, הביצועים עשויים להיות גרועים יותר מהחישוב."
        )

    # ── 4. Antenna height ─────────────────────────────────────────────────────
    if site.ant_height < 8:
        recs.append(
            f"גובה האנטנה נמוך ({site.ant_height} מ'). "
            "הגבהה ל-15–20 מ' יכולה לשפר את הכיסוי משמעותית ולהפחית חסימת טרן."
        )
    elif site.ant_height < 15 and covered_pct < 80:
        recs.append(
            f"שקול הגבהת האנטנה מ-{site.ant_height} מ' ל-15 מ' ומעלה "
            "לשיפור קו הראייה בטרן מחוספס."
        )

    # ── 5. Frequency / propagation trade-off ─────────────────────────────────
    if site.frequency >= 1800 and covered_pct < 80:
        recs.append(
            f"תדר {site.frequency} MHz רגיש לחסימות טרן. "
            "שקול שימוש ב-700/900 MHz לכיסוי רחב יותר בשטח פתוח/הרים, "
            "או פריסת ממסרים בנקודות בינוניות לאורך הציר."
        )
    elif site.frequency <= 900 and covered_pct < 70:
        recs.append(
            f"גם בתדר {site.frequency} MHz הכיסוי נמוך – הגורם העיקרי הוא טרן חוסם "
            "ולא איבוד נתיב. יש לבחון מיקום אנטנה בנקודת תצפית גבוהה יותר."
        )

    # ── 6. DSM mode implications ──────────────────────────────────────────────
    if mode == "DSM" and covered_pct < 75:
        recs.append(
            "החישוב בוצע במצב DSM (כולל בניינים וצמחייה). "
            "הכיסוי הנמוך מצביע על חסימות עירוניות/יעריות משמעותיות – "
            "שקול ממסרים על גגות מבנים גבוהים לאורך הציר."
        )

    # ── 7. Positive conclusions ───────────────────────────────────────────────
    if covered_pct >= 95 and not dead_zones:
        recs.append(
            "כיסוי מצוין ללא אזורי מתים – הציר מתאים לתקשורת מבצעית רציפה."
        )
    elif covered_pct >= 80 and len(dead_zones) <= 1:
        recs.append(
            "כיסוי סביר לרוב שימושי שטח; יש להגדיר נקודות ממסר "
            "באזורי המתים המזוהים לפני תרגיל/פעילות קריטית."
        )

    return recs


# ── Area recommendations ───────────────────────────────────────────────────────

def build_area_recommendations(
    site:               Any,
    covered_pct:        float,
    weak_pct:           float,
    rssi_min:           Optional[float],
    dead_cluster_count: int,
    dem_source:         Optional[str],
    dem_valid_pct:      Optional[float] = None,
    mode:               str             = "DTM",
) -> List[str]:
    """
    Generate Hebrew operational recommendations for an area-coverage result.

    Args:
        site:               object with ant_height / frequency / rx_threshold
        covered_pct:        % of in-radius cells with RSSI ≥ rx_threshold
        weak_pct:           % of in-radius cells in "weak" + "marginal"
        rssi_min:           minimum RSSI (dBm) or None if unknown
        dead_cluster_count: number of dead-zone clusters found by the caller
        dem_source:         "terrarium" (real DEM) or "none" (pseudo-random)
        dem_valid_pct:      % of DEM cells with valid data (optional)
        mode:               "DTM" or "DSM"
    """
    recs: List[str] = []
    thr = site.rx_threshold

    # ── 0. Data-quality warning (always first) ────────────────────────────────
    warn = _dem_warning(dem_source, dem_valid_pct)
    if warn:
        recs.append(warn)

    # ── 1. Coverage level relative to rx threshold ────────────────────────────
    if covered_pct < 60:
        recs.append(
            f"רק {covered_pct:.1f}% מהשטח מעל סף הקבלה ({thr} dBm) – "
            "יש לשקול הוספת ממסר ביניים באזורי האפלה העיקריים."
        )
    elif covered_pct < 85:
        recs.append(
            f"{covered_pct:.1f}% מהשטח מעל סף הקבלה ({thr} dBm) – "
            "קיימים פערי כיסוי; מומלץ לתכנן ממסר נייד לאזורים הקריטיים."
        )

    # ── 2. Antenna height ─────────────────────────────────────────────────────
    if covered_pct < 85:
        if site.ant_height < 15:
            recs.append(
                f"גובה האנטנה ({site.ant_height} מ') נמוך – הגבהה ל-15–20 מ' "
                "צפויה לשפר את קו הראייה ואת הכיסוי השטחי."
            )
        else:
            recs.append(
                "מומלץ לבחון מיקום אנטנה בנקודה שולטת יותר בשטח "
                "(הגבהה נוספת במיקום הנוכחי תורמת מעט מעבר לגובה זה)."
            )

    # ── 3. Minimum signal ─────────────────────────────────────────────────────
    if rssi_min is not None:
        if rssi_min < thr:
            recs.append(
                f"עוצמת האות המינימלית ({rssi_min} dBm) נמוכה מסף הקבלה ({thr} dBm) – "
                "יש לבחון גיבוי בתקשורת חלופית (רדיו נייד / לוויני) באזורים אלה."
            )
        elif rssi_min - thr < 6:
            recs.append(
                f"מרווח האות המינימלי מעל הסף נמוך ({rssi_min - thr:.1f} dB) – "
                "דעיכות (fading) ותנועה עלולות לגרום לניתוקים בשולי האזור."
            )

    # ── 4. Weak / marginal share ──────────────────────────────────────────────
    if weak_pct > 15:
        recs.append(
            f"כ-{round(weak_pct)}% מהאזור בעוצמת אות חלשה/שולית – "
            "מומלץ לתעד נקודות עיוורות ולאמת במדידות שטח."
        )

    # ── 5. Dead-zone clusters ─────────────────────────────────────────────────
    if dead_cluster_count:
        recs.append(
            f"זוהו {dead_cluster_count} אשכולות אפלה – "
            "מומלץ ביצוע סיור שטח ובחינת נקודות ממסר."
        )

    # ── 6. Frequency / clutter ────────────────────────────────────────────────
    if site.frequency >= 1800 and covered_pct < 80:
        recs.append(
            f"תדר {site.frequency} MHz רגיש לחסימות טרן ומבנים – "
            "שקול שימוש ב-700/900 MHz לכיסוי רחב יותר."
        )
    if mode == "DSM" and covered_pct < 75:
        recs.append(
            "במצב DSM (כולל מבנים וצמחייה) הכיסוי נמוך – "
            "שקול ממסרים על גגות מבנים גבוהים."
        )

    # ── 7. Nothing to report ──────────────────────────────────────────────────
    if not recs:
        recs.append("הכיסוי תקין – אין המלצות דחופות")

    return recs
