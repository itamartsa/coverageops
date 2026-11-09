/**
 * Civil cellular sites layer — shared constants & helpers.
 * Data: data.gov.il, Ministry of Environmental Protection (antennaactive + antenna_hakama).
 */
import type { CellSiteDetail, CellSiteHeight } from "../services/api";

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

export function cellSitePopupHtml(s: CellSiteDetail, canEdit = false): string {
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
      ${links ? `<div style="margin-top:5px">${links}</div>` : ""}
      <div style="color:#aaa;font-size:10px;margin-top:4px">
        ${s.lat.toFixed(5)}°N, ${s.lon.toFixed(5)}°E · מקור: המשרד להגנת הסביבה
      </div>
    </div>`;
}
