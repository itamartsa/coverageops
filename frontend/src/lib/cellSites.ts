/**
 * Civil cellular sites layer — shared constants & helpers.
 * Data: data.gov.il, Ministry of Environmental Protection (antennaactive + antenna_hakama).
 */
import type { CellSiteDetail } from "../services/api";

export const CELL_OPERATORS: Record<string, { label: string; color: string; note?: string }> = {
  PHI:       { label: "PHI",    color: "#f97316", note: "תשתית משותפת של הוט מובייל ופרטנר" },
  PELEPHONE: { label: "פלאפון", color: "#3b82f6" },
  CELLCOM:   { label: "סלקום",  color: "#a855f7" },
  OTHER:     { label: "אחר",    color: "#9ca3af" },
};

export const ALL_OPERATORS = ["PHI", "PELEPHONE", "CELLCOM", "OTHER"];

const esc = (v: unknown) =>
  String(v ?? "—").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

const fmtDate = (d?: string | null) =>
  d ? d.split("-").reverse().join("/") : "—";

const row = (k: string, v: string) =>
  `<tr><td style="color:#777;padding:1px 0 1px 8px;white-space:nowrap">${k}</td><td style="padding:1px 0">${v}</td></tr>`;

const safeUrl = (u?: string | null) =>
  u && /^https?:\/\//.test(u) ? u.replace(/^http:/, "https:") : null;

export function cellSitePopupHtml(s: CellSiteDetail): string {
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
    <div style="font-family:sans-serif;font-size:12px;direction:rtl;min-width:220px">
      <b>📡 אתר סלולר ${isActive ? "פעיל" : `<span style="color:#d97706">בהקמה</span>`}</b>
      <table style="margin-top:4px;border-collapse:collapse">${rows.join("")}</table>
      ${links ? `<div style="margin-top:5px">${links}</div>` : ""}
      <div style="color:#aaa;font-size:10px;margin-top:4px">
        ${s.lat.toFixed(5)}°N, ${s.lon.toFixed(5)}°E · מקור: המשרד להגנת הסביבה
      </div>
    </div>`;
}
