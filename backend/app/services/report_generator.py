"""
Word Document Report Generator – Radio Cross-Section (חתך רדיו)
"""
import io
from datetime import datetime
from typing import List

from docx import Document
from docx.shared import Inches, Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from app.services.coverage_engine import CrossSectionStats, DeadZone


# ── Color palette ────────────────────────────────────────────────────────────
NAVY        = RGBColor(0x0F, 0x29, 0x4D)
ACCENT_BLUE = RGBColor(0x1A, 0x56, 0xDB)
WHITE       = RGBColor(0xFF, 0xFF, 0xFF)
BLACK       = RGBColor(0x00, 0x00, 0x00)
LIGHT_GRAY  = RGBColor(0xF3, 0xF4, 0xF6)

LEVEL_FILL: dict = {
    "excellent": "00B464",
    "good":      "7DFF6B",
    "medium":    "FFE600",
    "weak":      "FF8C00",
    "marginal":  "FF3B5C",
    "none":      "CCCCCC",
}
LEVEL_TEXT_DARK = {"good", "medium"}   # levels where black text is more readable

LEVEL_HE: dict = {
    "excellent": "מצוין",
    "good":      "טוב",
    "medium":    "בינוני",
    "weak":      "חלש",
    "marginal":  "שולי",
    "none":      "אין כיסוי",
}

RISK_FILL: dict = {
    "CRITICAL": "CC0000",
    "HIGH":     "FF4444",
    "MEDIUM":   "FFA500",
    "LOW":      "00AA44",
}
RISK_HE: dict = {
    "CRITICAL": "קריטי",
    "HIGH":     "גבוה",
    "MEDIUM":   "בינוני",
    "LOW":      "נמוך",
}
MODE_HE: dict = {"DTM": "טופוגרפיה (DTM)", "DSM": "תכסית + מבנים (DSM)"}


# ── XML helpers ───────────────────────────────────────────────────────────────
def _shade_cell(cell, fill_hex: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd   = OxmlElement("w:shd")
    shd.set(qn("w:val"),   "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"),  fill_hex.upper().replace("#", ""))
    tc_pr.append(shd)


def _rtl_para(para) -> None:
    p_pr = para._p.get_or_add_pPr()
    bidi = OxmlElement("w:bidi")
    bidi.set(qn("w:val"), "1")
    p_pr.insert(0, bidi)


def _rtl_table(table) -> None:
    tbl_pr = table._tbl.tblPr   # CT_Tbl always has exactly one tblPr
    bidi   = OxmlElement("w:bidiVisual")
    bidi.set(qn("w:val"), "1")
    tbl_pr.insert(0, bidi)


def _set_col_widths(table, widths_cm: List[float]) -> None:
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            if i < len(widths_cm):
                cell.width = Cm(widths_cm[i])


def _cell_text(cell, text: str, bold=False, size_pt=10,
               align=WD_ALIGN_PARAGRAPH.RIGHT,
               color: RGBColor = BLACK) -> None:
    para = cell.paragraphs[0]
    para.alignment = align
    _rtl_para(para)
    run  = para.add_run(text)
    run.bold            = bold
    run.font.size       = Pt(size_pt)
    run.font.color.rgb  = color


def _add_heading(doc: Document, text: str, level: int = 1) -> None:
    para = doc.add_paragraph()
    _rtl_para(para)
    para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = para.add_run(text)
    run.bold           = True
    run.font.color.rgb = NAVY if level == 1 else ACCENT_BLUE
    run.font.size      = Pt(14 if level == 1 else 12)
    # bottom border
    p_pr  = para._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"),   "single")
    bottom.set(qn("w:sz"),    "6")
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), "1A56DB")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


def _add_kv_table(doc: Document, rows: List[tuple]) -> None:
    """Two-column key/value table (RTL)."""
    table = doc.add_table(rows=len(rows), cols=2)
    table.style = "Table Grid"
    _rtl_table(table)
    for i, (key, val) in enumerate(rows):
        row = table.rows[i]
        _shade_cell(row.cells[1], "EEF2FF")          # key col (right in RTL)
        _cell_text(row.cells[1], key, bold=True, size_pt=10)
        _cell_text(row.cells[0], str(val), size_pt=10)
    _set_col_widths(table, [11.0, 5.0])
    doc.add_paragraph()


def _add_stat_box(doc: Document, label: str, value: str,
                  fill_hex: str, text_white: bool = True) -> None:
    table = doc.add_table(rows=2, cols=1)
    _rtl_table(table)
    _shade_cell(table.rows[0].cells[0], fill_hex)
    _shade_cell(table.rows[1].cells[0], fill_hex)
    color = WHITE if text_white else BLACK
    _cell_text(table.rows[0].cells[0], value, bold=True, size_pt=20,
               align=WD_ALIGN_PARAGRAPH.CENTER, color=color)
    _cell_text(table.rows[1].cells[0], label, bold=False, size_pt=10,
               align=WD_ALIGN_PARAGRAPH.CENTER, color=color)
    doc.add_paragraph()


def _dem_quality_text(stats: CrossSectionStats, site_params: dict) -> str:
    """Human-readable DEM provenance / completeness line."""
    src = getattr(stats, "dem_source", None)
    if src != "terrarium":
        return "ללא נתוני טופוגרפיה – מודל סימולציה בלבד (אין להסתמך לתכנון מבצעי)"
    txt = f"Terrarium – {getattr(stats, 'dem_valid_pct', 0.0):.1f}% תקין"
    m_px = site_params.get("dem_m_per_px")
    if m_px:
        txt += f", רזולוציה ~{m_px:g} מ'/פיקסל"
    return txt


def _threshold_dbm(stats: CrossSectionStats, site_params: dict) -> float:
    """rx_threshold used for covered_pct / dead zones (site_params first, then stats)."""
    thr = site_params.get("rx_threshold")
    if thr is None:
        thr = getattr(stats, "threshold_dbm", None)
    return float(thr) if thr is not None else -110.0


# ── Main generator ────────────────────────────────────────────────────────────
def generate_cross_section_docx(
    stats: CrossSectionStats,
    site_params: dict,
) -> bytes:
    doc = Document()

    # ── Page setup: A4, narrow margins ──────────────────────────────────────
    section = doc.sections[0]
    section.page_width   = Cm(21)
    section.page_height  = Cm(29.7)
    section.left_margin  = Cm(2)
    section.right_margin = Cm(2)
    section.top_margin   = Cm(2)
    section.bottom_margin = Cm(2)

    # ── Header ───────────────────────────────────────────────────────────────
    hdr_table = doc.add_table(rows=1, cols=1)
    _shade_cell(hdr_table.rows[0].cells[0], "0F294D")
    title_para = hdr_table.rows[0].cells[0].paragraphs[0]
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _rtl_para(title_para)
    r1 = title_para.add_run("📡  דוח חתך רדיו\n")
    r1.bold           = True
    r1.font.size      = Pt(22)
    r1.font.color.rgb = WHITE
    r2 = title_para.add_run("ניתוח כיסוי ציר תנועה  |  CoverageOps")
    r2.font.size      = Pt(11)
    r2.font.color.rgb = RGBColor(0xA0, 0xC4, 0xFF)
    doc.add_paragraph()

    # Date + classification
    meta_para = doc.add_paragraph()
    _rtl_para(meta_para)
    meta_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    mr = meta_para.add_run(
        f"תאריך הפקה: {datetime.now().strftime('%d/%m/%Y  %H:%M')}     "
        f"|     אתר: {stats.site_name}     |     מסמך פנימי"
    )
    mr.font.size      = Pt(9)
    mr.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)
    doc.add_paragraph()

    threshold = _threshold_dbm(stats, site_params)

    # ── Section 1: Analysis details ──────────────────────────────────────────
    _add_heading(doc, "1. פרטי הניתוח")
    _add_kv_table(doc, [
        ("שם אתר",              stats.site_name),
        ("מיקום אנטנה",        f"{site_params['lat']:.5f}°N,  {site_params['lon']:.5f}°E"),
        ("גובה אנטנה",         f"{site_params['ant_height']} מ' מעל קרקע"
                                + (" (ערך ברירת מחדל)" if site_params.get("ant_height_is_default") else "")),
        ("תדר",                 f"{site_params['frequency']} MHz"),
        ("הספק שידור",         f"{site_params['tx_power']} dBm"),
        ("סף קבלה",            f"{site_params['rx_threshold']} dBm"),
        ("רדיוס מקסימלי",      f"{site_params['max_radius']} ק\"מ"),
        ("מודל חישוב",          MODE_HE.get(stats.mode, stats.mode)),
        ("אורך ציר",            f"{stats.total_length_km:.2f} ק\"מ"),
        ("נקודות ניתוח",        str(len(stats.points))),
        ("נקודות ציר (waypoints)", str(len(stats.waypoints))),
        ("זמן חישוב",           f"{stats.duration_sec}s"),
        ("נתוני גבהים (DEM)",   _dem_quality_text(stats, site_params)),
        ("ציון סיכון",          f"{RISK_HE.get(stats.risk_level, stats.risk_level)} "
                                f"({getattr(stats, 'risk_score', '-')}/4)"),
        ("כיסוי מעל סף הקבלה",  f"{stats.covered_pct}% (סף {threshold} dBm)"),
        ("כיסוי – אות כלשהו",   f"{getattr(stats, 'covered_pct_any_signal', '-')}% (מעל ‎-110 dBm)"),
    ])

    # ── Section 2: Executive summary ─────────────────────────────────────────
    _add_heading(doc, "2. סיכום מנהלים")

    risk_fill = RISK_FILL.get(stats.risk_level, "888888")
    _add_stat_box(doc, "כיסוי כולל",          f"{stats.covered_pct}%",       "1A56DB")
    _add_stat_box(doc, "אזורי מתים",          str(len(stats.dead_zones)),    "6B46C1")
    _add_stat_box(doc, "RSSI מינימלי",        f"{stats.rssi_min} dBm",       "0E7490")
    _add_stat_box(doc, "הערכת סיכון תקשורת",
                  RISK_HE.get(stats.risk_level, stats.risk_level),
                  risk_fill, text_white=stats.risk_level != "MEDIUM")

    # Key findings
    _add_heading(doc, "ממצאים עיקריים", level=2)
    findings = [
        f"אחוז כיסוי (RSSI ≥ {threshold} dBm): {stats.covered_pct}% מהציר",
        f"RSSI ממוצע: {stats.rssi_avg} dBm  |  מינ': {stats.rssi_min} dBm  |  מקס': {stats.rssi_max} dBm",
        f"זוהו {len(stats.dead_zones)} אזורי מתים",
    ]
    if stats.dead_zones:
        max_dz = max(stats.dead_zones, key=lambda d: d.length_km)
        findings.append(
            f"אזור המת הארוך ביותר: {max_dz.length_km:.2f} ק\"מ "
            f"(בין {max_dz.start_km:.2f} ל-{max_dz.end_km:.2f} ק\"מ)"
        )
    for f in findings:
        p = doc.add_paragraph(style="List Bullet")
        _rtl_para(p)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r = p.runs[0] if p.runs else p.add_run(f)
        if not p.runs:
            pass
        else:
            p.runs[0].text = f
            p.runs[0].font.size = Pt(10)
    doc.add_paragraph()

    # ── Section 3: Coverage profile table ────────────────────────────────────
    _add_heading(doc, "3. פרופיל כיסוי לאורך הציר")

    # Subsample: max 150 rows in table
    pts = stats.points
    step = max(1, len(pts) // 150)
    display_pts = pts[::step]

    headers = ["סטטוס", "רמה", "RSSI (dBm)", "קו אורך", "קו רוחב", "מרחק מאנטנה (ק\"מ)", "מרחק בציר (ק\"מ)"]
    tbl = doc.add_table(rows=1 + len(display_pts), cols=7)
    tbl.style = "Table Grid"
    _rtl_table(tbl)

    # Header row
    hdr_row = tbl.rows[0]
    for i, h in enumerate(headers):
        _shade_cell(hdr_row.cells[i], "0F294D")
        _cell_text(hdr_row.cells[i], h, bold=True, size_pt=9, color=WHITE,
                   align=WD_ALIGN_PARAGRAPH.CENTER)

    # Data rows — status (✓/✗) uses the engine's per-point flag (unrounded
    # rssi >= threshold), i.e. exactly the dead-zone rule
    for ri, pt in enumerate(display_pts, start=1):
        row     = tbl.rows[ri]
        fill    = LEVEL_FILL.get(pt.level, "CCCCCC")
        txt_col = BLACK if pt.level in LEVEL_TEXT_DARK else (
                  BLACK if pt.level == "none" else WHITE)
        covered = getattr(pt, "covered", pt.rssi >= threshold)
        _shade_cell(row.cells[0], "00B464" if covered else "CC0000")
        _shade_cell(row.cells[1], fill)
        status_icon = "✓" if covered else "✗"
        data = [
            status_icon,
            LEVEL_HE.get(pt.level, pt.level),
            str(pt.rssi),
            f"{pt.lon:.5f}",
            f"{pt.lat:.5f}",
            f"{pt.dist_from_site:.2f}",
            f"{pt.dist_along:.2f}",
        ]
        for ci, val in enumerate(data):
            _cell_text(row.cells[ci], val, size_pt=8,
                       align=WD_ALIGN_PARAGRAPH.CENTER,
                       color=(WHITE if ci == 0 else txt_col) if ci < 2 else BLACK)

    _set_col_widths(tbl, [1.5, 2.2, 2.5, 2.8, 2.8, 3.0, 3.0])
    doc.add_paragraph()

    # ── Section 4: Dead zones ─────────────────────────────────────────────────
    _add_heading(doc, "4. ניתוח אזורי מתים")
    if not stats.dead_zones:
        p = doc.add_paragraph("לא זוהו אזורי מתים לאורך הציר.")
        _rtl_para(p)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.runs[0].font.color.rgb = RGBColor(0x05, 0x96, 0x4F)
        p.runs[0].bold = True
    else:
        dz_headers = ["חומרה", "אורך (ק\"מ)", "קו אורך יציאה", "קו רוחב יציאה",
                      "קו אורך כניסה", "קו רוחב כניסה", "עד ק\"מ", "מ-ק\"מ", "#"]
        dz_tbl = doc.add_table(rows=1 + len(stats.dead_zones), cols=9)
        dz_tbl.style = "Table Grid"
        _rtl_table(dz_tbl)
        hdr_row = dz_tbl.rows[0]
        for i, h in enumerate(dz_headers):
            _shade_cell(hdr_row.cells[i], "5B0000")
            _cell_text(hdr_row.cells[i], h, bold=True, size_pt=9,
                       color=WHITE, align=WD_ALIGN_PARAGRAPH.CENTER)
        for ri, dz in enumerate(stats.dead_zones, start=1):
            row = dz_tbl.rows[ri]
            severity = ("קריטי" if dz.length_km > 2
                        else "גבוה" if dz.length_km > 1
                        else "בינוני" if dz.length_km > 0.5 else "נמוך")
            sev_fill = ("CC0000" if dz.length_km > 2
                        else "FF4444" if dz.length_km > 1
                        else "FFA500" if dz.length_km > 0.5 else "FFD700")
            _shade_cell(row.cells[0], sev_fill)
            data = [
                severity,
                f"{dz.length_km:.3f}",
                f"{dz.end_lon:.5f}",
                f"{dz.end_lat:.5f}",
                f"{dz.start_lon:.5f}",
                f"{dz.start_lat:.5f}",
                f"{dz.end_km:.2f}",
                f"{dz.start_km:.2f}",
                str(ri),
            ]
            for ci, val in enumerate(data):
                _cell_text(row.cells[ci], val, size_pt=9,
                           align=WD_ALIGN_PARAGRAPH.CENTER,
                           color=WHITE if ci == 0 else BLACK)
        _set_col_widths(dz_tbl, [2.0, 2.0, 2.5, 2.5, 2.5, 2.5, 1.8, 1.8, 1.0])
    doc.add_paragraph()

    # ── Section 5: Signal distribution ───────────────────────────────────────
    _add_heading(doc, "5. התפלגות עוצמת האות")
    dist_tbl = doc.add_table(rows=1 + len(stats.signal_distribution), cols=3)
    dist_tbl.style = "Table Grid"
    _rtl_table(dist_tbl)
    for i, h in enumerate(["אחוז", "נקודות", "רמה"]):
        _shade_cell(dist_tbl.rows[0].cells[i], "0F294D")
        _cell_text(dist_tbl.rows[0].cells[i], h, bold=True, size_pt=10,
                   color=WHITE, align=WD_ALIGN_PARAGRAPH.CENTER)
    ordered_levels = ["excellent", "good", "medium", "weak", "marginal", "none"]
    for ri, lvl in enumerate(ordered_levels, start=1):
        d    = stats.signal_distribution.get(lvl, {"count": 0, "pct": 0})
        row  = dist_tbl.rows[ri]
        fill = LEVEL_FILL.get(lvl, "CCCCCC")
        _shade_cell(row.cells[2], fill)
        txt  = BLACK if lvl in LEVEL_TEXT_DARK or lvl == "none" else WHITE
        _cell_text(row.cells[2], LEVEL_HE.get(lvl, lvl), bold=True, size_pt=10,
                   align=WD_ALIGN_PARAGRAPH.CENTER, color=txt)
        _cell_text(row.cells[1], str(d["count"]), size_pt=10, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_text(row.cells[0], f"{d['pct']}%", size_pt=10, align=WD_ALIGN_PARAGRAPH.CENTER)
    _set_col_widths(dist_tbl, [3.0, 3.0, 4.0])
    doc.add_paragraph()

    # ── Section 6: Operational recommendations ───────────────────────────────
    _add_heading(doc, "6. המלצות מבצעיות")
    for i, rec in enumerate(stats.recommendations, 1):
        p = doc.add_paragraph()
        _rtl_para(p)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.space_after = Pt(6)
        r_num = p.add_run(f"{i}. ")
        r_num.bold           = True
        r_num.font.color.rgb = ACCENT_BLUE
        r_num.font.size      = Pt(11)
        r_txt = p.add_run(rec)
        r_txt.font.size = Pt(10)
    doc.add_paragraph()

    # ── Footer line ───────────────────────────────────────────────────────────
    footer_para = doc.add_paragraph()
    _rtl_para(footer_para)
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_tbl = doc.add_table(rows=1, cols=1)
    _shade_cell(footer_tbl.rows[0].cells[0], "0F294D")
    _cell_text(footer_tbl.rows[0].cells[0],
               f"הופק על ידי CoverageOps  |  {datetime.now().strftime('%d/%m/%Y %H:%M')}  |  מסמך פנימי",
               size_pt=9, align=WD_ALIGN_PARAGRAPH.CENTER, color=RGBColor(0xA0, 0xC4, 0xFF))

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ══════════════════════════════════════════════════════════════════════════════
# Point-to-point link report (קישור נקודתי)
# ══════════════════════════════════════════════════════════════════════════════

EST_MARK = " (משוער)"


def _fmt(v, spec: str = ".1f", unit: str = "", none: str = "—") -> str:
    if v is None:
        return none
    try:
        return f"{format(float(v), spec)}{unit}"
    except (TypeError, ValueError):
        return str(v)


def render_link_profile_png(data: dict, width: int = 1400, height: int = 560) -> bytes:
    """
    Simple terrain profile chart (Pillow): terrain fill, LOS line and the first
    Fresnel-zone envelope (los_m ± fresnel_m), both endpoints marked.
    Labels are ASCII only (no font shaping for Hebrew in Pillow's default font).
    """
    from PIL import Image, ImageDraw, ImageFont

    prof = data.get("profile") or []
    img  = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:                                  # Pillow < 10.1
        font = ImageFont.load_default()
    if len(prof) < 2:
        draw.text((20, 20), "no profile data", fill=(0, 0, 0), font=font)
        buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()

    L, R, T, B = 90, 30, 30, 70
    pw, ph = width - L - R, height - T - B
    xs  = [float(p["dist_km"]) for p in prof]
    los = [float(p["los_m"]) for p in prof]
    fr  = [float(p.get("fresnel_m") or 0.0) for p in prof]
    # unknown terrain → linear between known neighbours (endpoints always known)
    ter_raw = [p.get("terrain_m") for p in prof]
    known = [(i, float(v)) for i, v in enumerate(ter_raw) if v is not None]
    ter: list = []
    for i in range(len(prof)):
        if ter_raw[i] is not None:
            ter.append(float(ter_raw[i])); continue
        lo = max((k for k in known if k[0] < i), default=None, key=lambda k: k[0])
        hi = min((k for k in known if k[0] > i), default=None, key=lambda k: k[0])
        if lo and hi:
            t = (i - lo[0]) / (hi[0] - lo[0]); ter.append(lo[1] + t * (hi[1] - lo[1]))
        else:
            ter.append((lo or hi or (0, 0.0))[1])

    x0, x1 = xs[0], xs[-1] if xs[-1] > xs[0] else xs[0] + 1e-3
    lows  = ter + [l - f for l, f in zip(los, fr)]
    highs = ter + [l + f for l, f in zip(los, fr)]
    y_lo, y_hi = min(lows), max(highs)
    pad = max((y_hi - y_lo) * 0.08, 5.0)
    y_lo, y_hi = y_lo - pad, y_hi + pad

    def X(d): return L + (d - x0) / (x1 - x0) * pw
    def Y(h): return T + (y_hi - h) / (y_hi - y_lo) * ph

    # grid + axes
    for k in range(6):
        h = y_lo + k * (y_hi - y_lo) / 5
        y = Y(h)
        draw.line([(L, y), (L + pw, y)], fill=(230, 230, 230))
        draw.text((8, y - 10), f"{h:.0f} m", fill=(90, 90, 90), font=font)
    for k in range(6):
        d = x0 + k * (x1 - x0) / 5
        x = X(d)
        draw.line([(x, T), (x, T + ph)], fill=(235, 235, 235))
        draw.text((min(x - 25, width - 85), T + ph + 8), f"{d:.2f} km", fill=(90, 90, 90), font=font)
    draw.rectangle([L, T, L + pw, T + ph], outline=(120, 120, 120))

    # terrain fill
    poly = [(X(d), Y(h)) for d, h in zip(xs, ter)] + [(X(xs[-1]), T + ph), (X(xs[0]), T + ph)]
    draw.polygon(poly, fill=(196, 170, 120), outline=(140, 110, 70))
    # Fresnel envelope + LOS
    upper = [(X(d), Y(l + f)) for d, l, f in zip(xs, los, fr)]
    lower = [(X(d), Y(l - f)) for d, l, f in zip(xs, los, fr)]
    draw.line(upper, fill=(26, 86, 219), width=2)
    draw.line(lower, fill=(26, 86, 219), width=2)
    los_col = (0, 160, 80) if data.get("is_los") else (220, 40, 60)
    draw.line([(X(d), Y(l)) for d, l in zip(xs, los)], fill=los_col, width=3)
    # endpoints (antennas)
    for d, g, l in ((xs[0], ter[0], los[0]), (xs[-1], ter[-1], los[-1])):
        draw.line([(X(d), Y(g)), (X(d), Y(l))], fill=(15, 41, 77), width=4)
        draw.ellipse([X(d) - 6, Y(l) - 6, X(d) + 6, Y(l) + 6], fill=(15, 41, 77))
    ob = data.get("worst_obstacle")
    if ob:
        ox, oy = X(float(ob["dist_km"])), Y(float(ob["terrain_m"]))
        draw.ellipse([ox - 7, oy - 7, ox + 7, oy + 7], outline=(220, 40, 60), width=3)
    draw.text((L + 10, T + 6), "TX", fill=(15, 41, 77), font=font)
    draw.text((L + pw - 40, T + 6), "RX", fill=(15, 41, 77), font=font)
    draw.text((L + pw / 2 - 160, height - 28),
              "terrain | LOS (green=clear, red=blocked) | 1st Fresnel zone (blue)",
              fill=(60, 60, 60), font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def generate_link_docx(data: dict) -> bytes:
    """Word report for a stored point-to-point link response (POST /api/link/)."""
    tx   = data.get("transmitter") or {}
    rx   = data.get("receiver") or {}
    est  = set(tx.get("estimated_fields") or [])
    mode = data.get("mode", "DTM")

    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2)
    section.top_margin = section.bottom_margin = Cm(2)

    # ── Header ───────────────────────────────────────────────────────────────
    hdr = doc.add_table(rows=1, cols=1)
    _shade_cell(hdr.rows[0].cells[0], "0F294D")
    tp = hdr.rows[0].cells[0].paragraphs[0]
    tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _rtl_para(tp)
    r1 = tp.add_run("דוח קישור נקודתי\n")
    r1.bold, r1.font.size, r1.font.color.rgb = True, Pt(22), WHITE
    r2 = tp.add_run("תקציב קישור משדר ← נקודת קליטה  |  CoverageOps")
    r2.font.size, r2.font.color.rgb = Pt(11), RGBColor(0xA0, 0xC4, 0xFF)
    doc.add_paragraph()

    meta = doc.add_paragraph()
    _rtl_para(meta)
    meta.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    mr = meta.add_run(
        f"תאריך הפקה: {datetime.now().strftime('%d/%m/%Y  %H:%M')}     |     "
        f"תאריך ניתוח: {str(data.get('created_at') or '')[:16].replace('T', ' ')}     |     "
        f"מזהה: {data.get('id', '—')}     |     מסמך פנימי")
    mr.font.size, mr.font.color.rgb = Pt(9), RGBColor(0x6B, 0x72, 0x80)
    doc.add_paragraph()

    def _e(field: str, text: str) -> str:
        return text + (EST_MARK if field in est else "")

    # ── 1. Transmitter ───────────────────────────────────────────────────────
    _add_heading(doc, "1. משדר")
    kind_he = "אתר שלי" if tx.get("kind") == "site" else "אתר סלולר אזרחי"
    tx_rows = [
        ("שם",              tx.get("name") or "—"),
        ("סוג",             kind_he),
        ("מיקום",           f"{_fmt(tx.get('lat'), '.5f')}°N,  {_fmt(tx.get('lon'), '.5f')}°E"),
        ("גובה קרקע",       f"{_fmt(data.get('tx_ground_asl'), '.0f')} מ' מעל פני הים"),
        ("גובה אנטנה",      _e("ant_height_m", f"{_fmt(tx.get('ant_height_m'), 'g')} מ' מעל הקרקע")),
        ("תדר",             _e("frequency_mhz", f"{tx.get('frequency_mhz', '—')} MHz")),
        ("הספק שידור (EIRP)", _e("tx_power_dbm", f"{_fmt(tx.get('tx_power_dbm'), 'g')} dBm")),
        ("מקור פרמטרים",    "משוער" if tx.get("params_source") == "estimated" else "ידני"),
    ]
    if tx.get("operator_he"):
        tx_rows.insert(2, ("מפעיל", tx["operator_he"]))
    _add_kv_table(doc, tx_rows)

    # ── 2. Receiver ──────────────────────────────────────────────────────────
    _add_heading(doc, "2. מקלט (נקודת קליטה)")
    _add_kv_table(doc, [
        ("שם",               rx.get("name") or "—"),
        ("פרופיל מכשיר",     rx.get("profile_name") or "ברירת מחדל מובנית"),
        ("מיקום",            f"{_fmt(rx.get('lat'), '.5f')}°N,  {_fmt(rx.get('lon'), '.5f')}°E"),
        ("גובה קרקע",        f"{_fmt(data.get('rx_ground_asl'), '.0f')} מ' מעל פני הים"),
        ("גובה מכשיר",       f"{_fmt(rx.get('height_agl'), 'g')} מ' מעל הקרקע"),
        ("רגישות",           f"{_fmt(rx.get('sensitivity_dbm'), 'g')} dBm"),
        ("הגבר אנטנה",       f"{_fmt(rx.get('antenna_gain_dbi'), 'g')} dBi"),
    ])

    # ── 3. Results ───────────────────────────────────────────────────────────
    _add_heading(doc, "3. תוצאות")
    level = data.get("level", "none")
    margin = data.get("margin_db")
    verdict_fill = ("00B464" if margin is not None and margin >= 10
                    else "FFA500" if margin is not None and margin >= 0 else "CC0000")
    verdict = ("קשר תקין" if verdict_fill == "00B464"
               else "קשר גבולי" if verdict_fill == "FFA500" else "אין קשר")
    _add_stat_box(doc, f"RSSI {_fmt(data.get('rssi_dbm'), '.1f')} dBm  |  מרווח {_fmt(margin, '+.1f')} dB",
                  verdict, verdict_fill, text_white=verdict_fill != "FFA500")

    headers = ["מרחק (ק\"מ)", "אזימוט (°)", "FSPL (dB)", "עקיפה (dB)",
               "RSSI (dBm)", "מרווח (dB)", "קו ראייה", "פרנל (%)"]
    values  = [_fmt(data.get("distance_km"), ".2f"), _fmt(data.get("bearing_deg"), ".1f"),
               _fmt(data.get("fspl_db"), ".1f"), _fmt(data.get("diffraction_db"), ".1f"),
               _fmt(data.get("rssi_dbm"), ".1f"), _fmt(margin, "+.1f"),
               "כן" if data.get("is_los") else "לא",
               _fmt(data.get("fresnel_clearance_pct"), ".0f")]
    tbl = doc.add_table(rows=2, cols=len(headers))
    tbl.style = "Table Grid"
    _rtl_table(tbl)
    for i, (h, v) in enumerate(zip(headers, values)):
        _shade_cell(tbl.rows[0].cells[i], "0F294D")
        _cell_text(tbl.rows[0].cells[i], h, bold=True, size_pt=9, color=WHITE,
                   align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_text(tbl.rows[1].cells[i], v, size_pt=10, align=WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_paragraph()

    extra = [
        ("רמת אות",           LEVEL_HE.get(level, level)),
        ("מצב חישוב",          MODE_HE.get(mode, mode)),
        ("הפסד תכסית (DSM)",   f"{_fmt(data.get('clutter_db'), '.1f')} dB"),
        ("תוספת גובה אנטנה",   f"{_fmt(data.get('height_gain_db'), '.1f')} dB"),
        ("הגבר מקלט",          f"{_fmt(data.get('rx_gain_db'), '.1f')} dB"),
        ("גובה מכשיר נדרש",    ("כבר מספיק" if data.get("required_rx_height_m") == 0
                               else f"{_fmt(data.get('required_rx_height_m'), 'g')} מ'"
                               if data.get("required_rx_height_m") is not None else "לא ניתן להשגה")),
    ]
    _add_kv_table(doc, extra)

    # ── 4. Worst obstacle ────────────────────────────────────────────────────
    _add_heading(doc, "4. מכשול עיקרי")
    ob = data.get("worst_obstacle")
    if ob:
        _add_kv_table(doc, [
            ("מרחק מהמשדר",   f"{_fmt(ob.get('dist_km'), '.2f')} ק\"מ"),
            ("מיקום",          f"{_fmt(ob.get('lat'), '.5f')}°N,  {_fmt(ob.get('lon'), '.5f')}°E"),
            ("גובה קרקע",      f"{_fmt(ob.get('terrain_m'), '.0f')} מ'"),
            ("גובה קו הראייה", f"{_fmt(ob.get('los_m'), '.0f')} מ'"),
            ("פרמטר עקיפה ν",  _fmt(ob.get("nu"), ".2f")),
        ])
    else:
        p = doc.add_paragraph("לא זוהה מכשול טופוגרפי התורם להפסד עקיפה.")
        _rtl_para(p)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    # ── 5. Profile chart ─────────────────────────────────────────────────────
    _add_heading(doc, "5. חתך טופוגרפי")
    try:
        png = render_link_profile_png(data)
        pic_para = doc.add_paragraph()
        pic_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic_para.add_run().add_picture(io.BytesIO(png), width=Cm(17))
    except Exception:                                   # pragma: no cover
        p = doc.add_paragraph("לא ניתן היה לשרטט את החתך.")
        _rtl_para(p)
    doc.add_paragraph()

    # ── 6. Recommendations ───────────────────────────────────────────────────
    _add_heading(doc, "6. המלצות")
    for i, rec in enumerate(data.get("recommendations") or [], 1):
        p = doc.add_paragraph()
        _rtl_para(p)
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        rn = p.add_run(f"{i}. ")
        rn.bold, rn.font.color.rgb, rn.font.size = True, ACCENT_BLUE, Pt(11)
        p.add_run(rec).font.size = Pt(10)
    warnings = data.get("warnings") or []
    if warnings:
        _add_heading(doc, "אזהרות", level=2)
        for w in warnings:
            p = doc.add_paragraph()
            _rtl_para(p)
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            rw = p.add_run("⚠ " + w)
            rw.font.size, rw.font.color.rgb = Pt(10), RGBColor(0xB4, 0x53, 0x09)
    doc.add_paragraph()

    # ── Data quality ─────────────────────────────────────────────────────────
    src = data.get("dem_source")
    if src == "terrarium" or (src and src.startswith("terrarium")):
        dq = (f"נתוני גבהים: Terrarium – {_fmt(data.get('dem_valid_pct'), '.1f')}% תקין באזור, "
              f"{_fmt(data.get('profile_valid_pct'), '.0f')}% לאורך התוואי")
        if data.get("dem_m_per_px"):
            dq += f", רזולוציה ~{_fmt(data.get('dem_m_per_px'), '.0f')} מ'/פיקסל"
    else:
        dq = "נתוני גבהים: ללא נתוני טופוגרפיה – לא חושבו חסימות (תוצאה אופטימית)"
    dq += ".  מודל: FSPL + ITU-R P.526 Deygout (k=4/3)"
    p = doc.add_paragraph()
    _rtl_para(p)
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    rq = p.add_run(dq)
    rq.font.size, rq.font.color.rgb = Pt(9), RGBColor(0x6B, 0x72, 0x80)

    footer = doc.add_table(rows=1, cols=1)
    _shade_cell(footer.rows[0].cells[0], "0F294D")
    _cell_text(footer.rows[0].cells[0],
               f"הופק על ידי CoverageOps  |  {datetime.now().strftime('%d/%m/%Y %H:%M')}  |  מסמך פנימי",
               size_pt=9, align=WD_ALIGN_PARAGRAPH.CENTER, color=RGBColor(0xA0, 0xC4, 0xFF))

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
