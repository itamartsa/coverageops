/**
 * Civil cellular sites layer — shared constants & helpers.
 * Data: data.gov.il, Ministry of Environmental Protection (antennaactive + antenna_hakama).
 */
import type { CellSiteDetail, CellSiteHeight, CellSiteRf } from "../services/api";

export const CELL_OPERATORS: Record<string, { label: string; color: string; note?: string }> = {
  PHI:       { label: "PHI",    color: "#f97316", note: "תשתית משותפת של הוט מובייל ופרטנר" },
  PELEPHONE: { label: "פלאפון", color: "#3b82f6" },
  CELLCOM:   { label: "סלקום",  color: "#a855f7" },
  OTHER:     { label: "אחר",    color: "#9ca3af" },
};

export const ALL_OPERATORS = ["PHI", "PELEPHONE", "CELLCOM", "OTHER"];

export const esc = (v: unknown) =>
  String(v ?? "—").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

const fmtDate = (d?: string | null) =>
  d ? d.split("-").reverse().join("/") : "—";

const row = (k: string, v: string) =>
  `<tr><td style="color:#777;padding:1px 0 1px 8px;white-space:nowrap">${k}</td><td style="padding:1px 0">${v}</td></tr>`;

const safeUrl = (u?: string | null) =>
  u && /^https?:\/\//.test(u) ? u.replace(/^http:/, "https:") : null;

/** Roles allowed to edit a site's antenna height (mirrors the backend) */
export const canEditCellSiteHeight = (role?: string | null) => role === "ADMIN" || role === "OPERATOR";

/** Marker style tweak: sites whose height was entered by a user get a dark, thicker ring. */
export const isManualHeight = (h: Pick<CellSiteHeight, "height_source">) => h.height_source === "manual";

/** RF (EIRP / frequency) entered by a user rather than estimated (legacy, whole-record flag). */
export const isManualRf = (r: Pick<CellSiteRf, "rf_source">) => r.rf_source === "manual";

/** Per-field RF provenance; falls back to the legacy `rf_source` when the backend doesn't send it. */
export const isManualTxPower = (r: Pick<CellSiteRf, "rf_source" | "tx_power_source">) =>
  (r.tx_power_source ?? r.rf_source) === "manual";
export const isManualFrequency = (r: Pick<CellSiteRf, "rf_source" | "frequency_source">) =>
  (r.frequency_source ?? r.rf_source) === "manual";

/** Any RF value entered by a user (shows the note / "reset" option). */
export const hasManualRf = (r: Pick<CellSiteRf, "rf_source" | "tx_power_source" | "frequency_source">) =>
  isManualRf(r) || isManualTxPower(r) || isManualFrequency(r);

/** Roles allowed to create an analysis Site from a cell site (mirrors the backend). */
export const canPromoteCellSite = canEditCellSiteHeight;

/** Site bands ascending (deduped); falls back to the current frequency. */
export function cellSiteBands(s: CellSiteRf): number[] {
  const b = [...new Set((s.bands ?? []).filter((x) => Number.isFinite(x)))].sort((a, c) => a - c);
  if (!b.length && s.frequency_mhz != null) b.push(s.frequency_mhz);
  return b;
}

const fmtDateTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString("he-IL", { day: "2-digit", month: "2-digit", year: "numeric" }) : "—";

const fmtH = (h: number | null) => (h == null ? "—" : `${Math.round(h * 10) / 10} מ'`);

const BTN = "font:inherit;font-size:11px;padding:1px 7px;cursor:pointer;border:1px solid #999;background:#f5f5f5;border-radius:3px";

/** The antenna-height block of the popup (view mode). */
function heightBlockHtml(s: CellSiteDetail, canEdit: boolean): string {
  const manual = isManualHeight(s);
  const badge = manual
    ? `<span style="color:#15803d">✓ הוזן ידנית</span>`
    : `<span style="color:#d97706" title="גובה האנטנה אינו חלק מהמאגר הממשלתי">⚠ משוער לפי סוג האתר</span>`;
  const meta = manual
    ? `<div style="color:#777;font-size:10px">${s.height_note ? `${esc(s.height_note)} · ` : ""}${esc(fmtDateTime(s.height_updated_at))}${s.height_updated_by ? ` · ${esc(s.height_updated_by)}` : ""}</div>`
    : "";
  const btn = canEdit
    ? ` <button type="button" data-cs-action="edit" style="${BTN}">ערוך גובה</button>`
    : "";
  return `<div style="margin-top:4px">
      <span style="color:#777">גובה אנטנה:</span>
      <b style="${manual ? "" : "color:#92400e;font-weight:600"}">${esc(fmtH(s.ant_height_m))}</b> ${badge}${btn}
      ${meta}
    </div>`;
}

const fmtNumU = (v: number | null | undefined, unit: string) =>
  v == null || !Number.isFinite(v) ? "—" : `${Math.round(v * 10) / 10} ${unit}`;

const MAN_MARK = `<span style="color:#15803d" title="הוזן ידנית">✓</span>`;
const EST_MARK = (what: string) =>
  `<span style="color:#d97706" title="${what} אינו חלק מהמאגר הממשלתי — ערך משוער">⚠</span>`;

/** The RF block of the popup (view mode): "תדר: 1800 MHz ⚠ · הספק: 43 dBm ✓". */
function rfBlockHtml(s: CellSiteDetail, canEdit: boolean): string {
  const txMan = isManualTxPower(s), fMan = isManualFrequency(s);
  const anyMan = hasManualRf(s);
  const meta = anyMan && (s.rf_note || s.rf_updated_at)
    ? `<div style="color:#777;font-size:10px">${s.rf_note ? `${esc(s.rf_note)} · ` : ""}${esc(fmtDateTime(s.rf_updated_at))}${s.rf_updated_by ? ` · ${esc(s.rf_updated_by)}` : ""}</div>`
    : "";
  const bands = cellSiteBands(s);
  const bandsTxt = bands.length > 1
    ? `<div style="color:#777;font-size:10px">תדרים באתר: ${bands.map((b) => esc(b)).join(" · ")} MHz</div>` : "";
  const btn = canEdit
    ? ` <button type="button" data-cs-action="edit-rf" style="${BTN}">ערוך RF</button>`
    : "";
  const est = "color:#92400e;font-weight:600";
  return `<div style="margin-top:3px">
      <span style="color:#777">תדר:</span> <b style="${fMan ? "" : est}">${esc(fmtNumU(s.frequency_mhz, "MHz"))}</b>
      ${s.frequency_mhz != null ? (fMan ? MAN_MARK : EST_MARK("התדר")) : ""}
      · <span style="color:#777">הספק:</span> <b style="${txMan ? "" : est}" title="EIRP">${esc(fmtNumU(s.tx_power_dbm, "dBm"))}</b>
      ${s.tx_power_dbm != null ? (txMan ? MAN_MARK : EST_MARK("הספק השידור")) : ""}
      ${btn}
      ${bandsTxt}${meta}
    </div>`;
}

/** Inline RF edit form (EIRP + frequency select from the site's bands + free input). */
export function cellSiteRfFormHtml(s: CellSiteDetail): string {
  const anyMan = hasManualRf(s);
  const mark = (manual: boolean, what: string) => (manual ? MAN_MARK : EST_MARK(what));
  const bands = cellSiteBands(s);
  const cur = s.frequency_mhz ?? bands[0] ?? null;
  const opts = [...new Set([...bands, ...(cur != null ? [cur] : [])])].sort((a, b) => a - b)
    .map((b) => `<option value="${esc(b)}"${b === cur ? " selected" : ""}>${esc(b)} MHz</option>`).join("");
  const inp = "font:inherit;font-size:12px;padding:1px 3px";
  return `<div style="margin-top:4px;padding:5px;border:1px solid #ddd;background:#fafafa">
      <div style="margin-bottom:3px">פרמטרי RF אמיתיים:</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        <label>${mark(isManualTxPower(s), "הספק השידור")} הספק (EIRP, dBm)
          <input data-cs-field="tx" type="number" min="0" max="70" step="0.5" value="${esc(s.tx_power_dbm ?? "")}"
                 style="width:58px;${inp}" /></label>
        <label>${mark(isManualFrequency(s), "התדר")} תדר
          <select data-cs-field="freq" style="${inp}">${opts || `<option value="">—</option>`}</select></label>
        <input data-cs-field="freq-custom" type="number" min="100" max="6000" step="1" placeholder="אחר (MHz)"
               style="width:78px;${inp}" />
      </div>
      <input data-cs-field="rfnote" type="text" maxlength="256" placeholder="מקור / הערה (רשות)"
             value="${anyMan && s.rf_note ? esc(s.rf_note) : ""}"
             style="width:100%;margin-top:4px;font:inherit;font-size:11px;padding:1px 3px" />
      <div style="margin-top:5px;display:flex;gap:4px;flex-wrap:wrap">
        <button type="button" data-cs-action="save-rf" style="${BTN};border-color:#15803d;color:#15803d">שמור</button>
        <button type="button" data-cs-action="cancel" style="${BTN}">ביטול</button>
        ${anyMan ? `<button type="button" data-cs-action="reset-rf" style="${BTN};color:#d97706">חזור להערכה</button>` : ""}
      </div>
      <div data-cs-field="err" style="color:#dc2626;font-size:10px;margin-top:3px"></div>
    </div>`;
}

const ACT_BTN = "font:inherit;font-size:11px;padding:3px 6px;cursor:pointer;border-radius:3px;flex:1;white-space:nowrap";

/** The two action buttons at the bottom of the popup (hidden for VIEWER — same role check as the edit forms). */
function actionsHtml(canPromote: boolean, canLink: boolean): string {
  if (!canPromote && !canLink) return "";
  return `<div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap">
      ${canPromote ? `<button type="button" data-cs-action="promote" style="${ACT_BTN};border:1px solid #0369a1;background:#e0f2fe;color:#0c4a6e">📡 נתח כיסוי מאתר זה</button>` : ""}
      ${canLink ? `<button type="button" data-cs-action="link" style="${ACT_BTN};border:1px solid #15803d;background:#dcfce7;color:#14532d">🎯 קישור למכשיר מאתר זה</button>` : ""}
    </div>`;
}

/** Band picker shown before promoting a multi-band site (defaults to the lowest band). */
export function cellSitePromoteFormHtml(s: CellSiteDetail): string {
  const bands = cellSiteBands(s);
  const opts = bands.map((b, i) => `<option value="${esc(b)}"${i === 0 ? " selected" : ""}>${esc(b)} MHz${i === 0 ? " (הנמוך)" : ""}</option>`).join("");
  return `<div style="margin-top:6px;padding:5px;border:1px solid #bae6fd;background:#f0f9ff">
      <div style="margin-bottom:3px">באיזה תדר לנתח? (לאתר ${bands.length} תדרים)</div>
      <select data-cs-field="pfreq" style="font:inherit;font-size:12px;padding:1px 3px">${opts}</select>
      <div style="margin-top:5px;display:flex;gap:4px">
        <button type="button" data-cs-action="promote-go" style="${BTN};border-color:#0369a1;color:#0369a1">צור אתר לניתוח</button>
        <button type="button" data-cs-action="cancel" style="${BTN}">ביטול</button>
      </div>
      <div data-cs-field="err" style="color:#dc2626;font-size:10px;margin-top:3px"></div>
    </div>`;
}

/** Inline edit form for the antenna height (replaces the height block). */
export function cellSiteHeightFormHtml(s: CellSiteDetail): string {
  const manual = isManualHeight(s);
  return `<div style="margin-top:4px;padding:5px;border:1px solid #ddd;background:#fafafa">
      <div style="margin-bottom:3px">גובה אנטנה אמיתי (מטר מעל הקרקע):</div>
      <input data-cs-field="h" type="number" min="0.5" max="300" step="0.5" value="${esc(s.ant_height_m ?? "")}"
             style="width:70px;font:inherit;font-size:12px;padding:1px 3px" />
      <input data-cs-field="note" type="text" maxlength="256" placeholder="מקור / הערה (רשות)"
             value="${manual && s.height_note ? esc(s.height_note) : ""}"
             style="width:130px;font:inherit;font-size:11px;padding:1px 3px;margin-inline-start:4px" />
      <div style="margin-top:5px;display:flex;gap:4px;flex-wrap:wrap">
        <button type="button" data-cs-action="save" style="${BTN};border-color:#15803d;color:#15803d">שמור</button>
        <button type="button" data-cs-action="cancel" style="${BTN}">ביטול</button>
        ${manual ? `<button type="button" data-cs-action="reset" style="${BTN};color:#d97706">חזור להערכה</button>` : ""}
      </div>
      <div data-cs-field="err" style="color:#dc2626;font-size:10px;margin-top:3px"></div>
    </div>`;
}

export function cellSitePopupHtml(s: CellSiteDetail, canEdit = false, canPromote = canEdit): string {
  const op = CELL_OPERATORS[s.operator] ?? CELL_OPERATORS.OTHER;
  const isActive = s.status === "active";
  const rows: string[] = [
    row("מפעיל", `<b style="color:${op.color}">${esc(s.operator_he)}</b>`),
    row("מס' אתר", esc(s.site_code)),
    row("כתובת", `${esc(s.address)}, ${esc(s.city)}`),
    row("סוג אתר", esc(s.site_type_he)),
  ];
  if (isActive) {
    rows.push(
      row("טכנולוגיות", esc((s.technologies || "").split(",").join(" · ") || "—")),
      row("היתר הפעלה", fmtDate(s.operation_permit_date)),
      row("בדיקה תקופתית", fmtDate(s.last_periodic_check)),
      row("קרינה מרבית", s.max_pct_of_threshold != null ? `${esc(s.max_pct_of_threshold)}% מסף הבריאות` : "—"),
    );
  } else {
    rows.push(
      row("סוג היתר", esc(s.permit_type)),
      row("הגשת בקשה", fmtDate(s.request_date)),
    );
  }
  const links = [
    safeUrl(s.construction_permit_url) && `<a href="${safeUrl(s.construction_permit_url)}" target="_blank" rel="noopener">היתר הקמה (PDF)</a>`,
    safeUrl(s.operation_permit_url) && `<a href="${safeUrl(s.operation_permit_url)}" target="_blank" rel="noopener">היתר הפעלה (PDF)</a>`,
  ].filter(Boolean).join(" · ");

  return `
    <div data-cellsite-id="${Number(s.id)}" style="font-family:sans-serif;font-size:12px;direction:rtl;min-width:220px">
      <b>📡 אתר סלולר ${isActive ? "פעיל" : `<span style="color:#d97706">בהקמה</span>`}</b>
      <table style="margin-top:4px;border-collapse:collapse">${rows.join("")}</table>
      <div data-cs-height>${heightBlockHtml(s, canEdit)}</div>
      <div data-cs-rf>${rfBlockHtml(s, canEdit)}</div>
      <div data-cs-actions>${actionsHtml(canPromote, canEdit)}</div>
      ${links ? `<div style="margin-top:5px">${links}</div>` : ""}
      <div style="color:#aaa;font-size:10px;margin-top:4px">
        ${Number(s.lat).toFixed(5)}°N, ${Number(s.lon).toFixed(5)}°E · מקור: המשרד להגנת הסביבה
      </div>
    </div>`;
}
