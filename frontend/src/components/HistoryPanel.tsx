import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { coverageApi, type HistoryItem, type CoverageReport } from "../services/api";
import { useStore } from "../store/useStore";
import { notify } from "./Notification";
import styles from "./HistoryPanel.module.css";

const MODE_LABEL: Record<string, string> = { DTM: "טופוגרפיה", DSM: "תכסית" };
const LEVEL_LABELS: Record<string, string> = {
  excellent: "מצוין", good: "טוב", medium: "בינוני", weak: "חלש", marginal: "שולי",
};
const LEVEL_COLORS: Record<string, string> = {
  excellent: "#00ff88", good: "#7dff6b", medium: "#ffe600",
  weak: "#ff8c00", marginal: "#ff3b5c",
};

function formatDate(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString("he-IL", { dateStyle: "short", timeStyle: "short" });
}

const RISK_COLORS_HEX: Record<string, string> = {
  CRITICAL: "#cc0000", HIGH: "#ff4444", MEDIUM: "#ffa500", LOW: "#00aa44",
};
const RISK_HE_LABELS: Record<string, string> = {
  CRITICAL: "קריטי", HIGH: "גבוה", MEDIUM: "בינוני", LOW: "נמוך",
};

// ── HTML Report Generator ────────────────────────────────────────────────────
function buildReportHTML(r: CoverageReport): string {
  const { meta, macro, signal_distribution, propagation,
          operational_summary, risk, dead_zones_clusters, recommendations } = r;

  const distRows = Object.entries(signal_distribution)
    .map(([lvl, d]) => `
      <tr>
        <td><span class="dot" style="background:${LEVEL_COLORS[lvl] ?? "#aaa"}"></span>${LEVEL_LABELS[lvl] ?? lvl}</td>
        <td>${d.count.toLocaleString()}</td>
        <td>${d.pct}%</td>
        <td style="min-width:100px">
          <div class="bar" style="width:${d.pct}%;background:${LEVEL_COLORS[lvl] ?? "#aaa"}"></div>
        </td>
      </tr>`)
    .join("");

  const fsplRows = Object.entries(propagation.fspl_table_db)
    .map(([d, v]) => `<tr><td>${d}</td><td>${v} dB</td></tr>`).join("");

  const radiiRows = Object.entries(propagation.theoretical_radii_km)
    .map(([lvl, km]) => `
      <tr>
        <td><span class="dot" style="background:${LEVEL_COLORS[lvl] ?? "#aaa"}"></span>${LEVEL_LABELS[lvl] ?? lvl}</td>
        <td>${km} ק"מ</td>
      </tr>`).join("");

  // ── Executive Summary section ──────────────────────────────────────────────
  const riskColor   = risk ? (RISK_COLORS_HEX[risk.level] ?? "#555") : "#555";
  const riskLabel   = risk ? (RISK_HE_LABELS[risk.level] ?? risk.level) : "לא ידוע";
  const opStatus    = operational_summary?.status ?? "—";
  const opConclusion = operational_summary?.conclusion ?? "";
  const statusBg    = opStatus === "תקין" ? "#d4edda" : opStatus === "גבולי" ? "#fff3cd" : "#f8d7da";
  const statusColor = opStatus === "תקין" ? "#155724" : opStatus === "גבולי" ? "#856404" : "#721c24";

  const execSummaryHTML = operational_summary ? `
<div class="exec-box" style="border:2px solid ${riskColor};padding:16px;margin-bottom:20px;border-radius:4px;">
  <div style="display:flex;align-items:center;gap:16px;margin-bottom:12px;">
    <div style="background:${statusBg};color:${statusColor};font-size:20px;font-weight:700;padding:10px 20px;border-radius:4px;border:1px solid ${statusColor}40;">${opStatus}</div>
    <div>
      <div style="font-size:12px;color:#555;margin-bottom:2px;">רמת סיכון</div>
      <div style="background:${riskColor};color:#fff;font-size:14px;font-weight:700;padding:4px 14px;border-radius:3px;display:inline-block;">${riskLabel}</div>
    </div>
    <div style="margin-right:auto;font-size:13px;color:#333;max-width:55%;">${opConclusion}</div>
  </div>
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;">
    <div class="stat-box"><div class="stat-val" style="color:${riskColor}">${operational_summary.covered_pct}%</div><div class="stat-lbl">שטח מכוסה</div></div>
    <div class="stat-box"><div class="stat-val">${operational_summary.dead_zones_count}</div><div class="stat-lbl">אשכולות אפלה</div></div>
    <div class="stat-box"><div class="stat-val">${operational_summary.rssi_avg}</div><div class="stat-lbl">RSSI ממוצע</div></div>
    <div class="stat-box"><div class="stat-val">${operational_summary.rssi_min}</div><div class="stat-lbl">RSSI מינ'</div></div>
    <div class="stat-box"><div class="stat-val">${operational_summary.rssi_max}</div><div class="stat-lbl">RSSI מקס'</div></div>
  </div>
</div>` : "";

  // ── Dead zones clusters section ────────────────────────────────────────────
  const deadZoneRows = (dead_zones_clusters ?? []).map((dz, i) => `
    <tr>
      <td>${i + 1}</td>
      <td>${dz.centroid_lat.toFixed(4)}°N, ${dz.centroid_lon.toFixed(4)}°E</td>
      <td>${dz.cell_count}</td>
      <td><span style="color:${dz.severity === "none" ? "#cc0000" : "#ffa500"};font-weight:700;">${dz.severity === "none" ? "ללא קשר" : "שולי"}</span></td>
      <td>${dz.rssi_avg} dBm</td>
    </tr>`).join("");

  const deadZonesSectionHTML = (dead_zones_clusters && dead_zones_clusters.length > 0) ? `
<h2 style="background:#cc0000;">אזורי אפלה – Dead Zones (${dead_zones_clusters.length} אשכולות)</h2>
<table>
  <tr><th>#</th><th>מיקום מרכזי</th><th>תאי רשת</th><th>חומרה</th><th>RSSI ממוצע</th></tr>
  ${deadZoneRows}
</table>
<p class="note">* מיקומים חלופיים לממסר ביניים: סמוך לאחת הנקודות המרכזיות לעיל בגובה אנטנה של לפחות 6 מ'.</p>` : `
<h2 style="background:#00aa44;">אזורי אפלה – Dead Zones</h2>
<p style="color:#155724;background:#d4edda;padding:10px;border-radius:4px;">✅ לא זוהו אזורי אפלה משמעותיים בניתוח זה.</p>`;

  // ── Recommendations section ────────────────────────────────────────────────
  const recItems = (recommendations ?? []).map((rec, i) =>
    `<li style="margin-bottom:8px;"><b>${i + 1}.</b> ${rec}</li>`).join("");

  const recsSectionHTML = `
<h2>המלצות מבצעיות</h2>
<ul style="padding-right:20px;font-size:13px;line-height:1.7;">${recItems}</ul>`;

  return `<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
<meta charset="UTF-8">
<title>דוח כיסוי – ${meta.site_name}</title>
<style>
  body { font-family: Arial, sans-serif; margin: 0; padding: 24px 32px; color: #1a1a2e; background: #fff; direction: rtl; }
  h1 { font-size: 22px; border-bottom: 3px solid #3b82f6; padding-bottom: 8px; margin-bottom: 4px; }
  .subtitle { color: #555; font-size: 13px; margin-bottom: 24px; }
  h2 { font-size: 15px; background: #3b82f6; color: #fff; padding: 6px 12px; margin-top: 28px; }
  h3 { font-size: 13px; color: #3b82f6; border-bottom: 1px solid #dde; padding-bottom: 4px; margin-top: 18px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
  th { background: #f0f4ff; text-align: right; padding: 6px 10px; border: 1px solid #dde; }
  td { padding: 5px 10px; border: 1px solid #dde; }
  tr:nth-child(even) { background: #f9f9ff; }
  .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 12px; }
  .stat-box { background: #f0f4ff; border: 1px solid #c0ccee; padding: 12px; text-align: center; }
  .stat-val { font-size: 26px; font-weight: 700; color: #3b82f6; }
  .stat-lbl { font-size: 11px; color: #555; margin-top: 2px; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-left: 6px; }
  .bar { height: 10px; border-radius: 3px; display: inline-block; min-width: 2px; }
  .formula { background: #1a1a2e; color: #00ff88; font-family: monospace; padding: 10px 14px; font-size: 13px; border-radius: 4px; margin: 10px 0; }
  .note { font-size: 11px; color: #888; margin-top: 6px; }
  @media print { body { padding: 12px; } }
</style>
</head>
<body>
<h1>📡 דוח ניתוח כיסוי סלולרי</h1>
<div class="subtitle">הופק ב-${new Date().toLocaleString("he-IL")} | CoverageOps</div>

<h2 style="background:#1a1a2e;">0. סיכום מבצעי – Executive Summary</h2>
${execSummaryHTML}
${deadZonesSectionHTML}
${recsSectionHTML}

<h2>1. פרטי הניתוח</h2>
<table>
  <tr><th>שם אתר</th><td>${meta.site_name}</td><th>מזהה תוצאה</th><td>#${meta.result_id}</td></tr>
  <tr><th>מיקום אנטנה</th><td>${meta.site_lat.toFixed(5)}°N, ${meta.site_lon.toFixed(5)}°E</td><th>גובה אנטנה</th><td>${meta.ant_height} מ'</td></tr>
  <tr><th>תדר</th><td>${meta.frequency} MHz</td><th>הספק שידור</th><td>${meta.tx_power} dBm</td></tr>
  <tr><th>סף קבלה</th><td>${meta.rx_threshold} dBm</td><th>רדיוס מקסימלי</th><td>${meta.max_radius} ק"מ</td></tr>
  <tr><th>מודל חישוב</th><td>${MODE_LABEL[meta.mode] ?? meta.mode} (${meta.mode})</td><th>תאריך ניתוח</th><td>${new Date(meta.analysis_date).toLocaleString("he-IL")}</td></tr>
  <tr><th>זמן חישוב</th><td>${meta.duration_sec}s</td><th></th><td></td></tr>
</table>

<h2>2. סיכום מאקרו</h2>
<div class="stat-grid">
  <div class="stat-box"><div class="stat-val">${macro.covered_pct}%</div><div class="stat-lbl">שטח מכוסה</div></div>
  <div class="stat-box"><div class="stat-val">${macro.rssi_avg}</div><div class="stat-lbl">RSSI ממוצע (dBm)</div></div>
  <div class="stat-box"><div class="stat-val">${macro.rssi_max}</div><div class="stat-lbl">RSSI מקסימום</div></div>
  <div class="stat-box"><div class="stat-val">${macro.rssi_min}</div><div class="stat-lbl">RSSI מינימום</div></div>
</div>
<p class="note">תא שטח: ${macro.bbox.sw_lat.toFixed(4)}°N,${macro.bbox.sw_lon.toFixed(4)}°E → ${macro.bbox.ne_lat.toFixed(4)}°N,${macro.bbox.ne_lon.toFixed(4)}°E</p>

<h2>3. התפלגות עוצמת אות</h2>
<table>
  <tr><th>רמה</th><th>תאי רשת</th><th>אחוז מהכיסוי</th><th>גרף</th></tr>
  ${distRows}
</table>

<h2>4. ניתוח פרטני – התפשטות גלים</h2>
<p><b>מודל:</b> ${propagation.model}</p>
<div class="formula">${propagation.formula}</div>
<p class="note">רווח גובה אנטנה: ${propagation.height_gain_db} dB | מרווח טרן ממוצע הנחה: ${propagation.terrain_margin_assumed_db} dB</p>

<h3>הפסד שדה חופשי (FSPL) לפי מרחק מהאנטנה</h3>
<table>
  <tr><th>מרחק</th><th>FSPL</th></tr>
  ${fsplRows}
</table>

<h3>רדיוס כיסוי תיאורטי לפי רמת אות</h3>
<table>
  <tr><th>רמת אות</th><th>רדיוס מקסימלי</th></tr>
  ${radiiRows}
</table>
<p class="note">* הרדיוס התיאורטי מחושב על בסיס FSPL בלבד עם מרווח טרן ממוצע – הערכים בשטח תלויים בתנאי שטח בפועל.</p>

</body>
</html>`;
}

// ── Component ────────────────────────────────────────────────────────────────
export default function HistoryPanel() {
  const { setCoverageResults, setBbox, setActiveTab,
          crossSectionResult, setCrossSectionResult, setRouteWaypoints } = useStore();
  const qc = useQueryClient();
  const [exportingId, setExportingId] = useState<number | null>(null);
  const [loadingId, setLoadingId]     = useState<number | null>(null);

  const { data: history = [], isLoading } = useQuery<HistoryItem[]>({
    queryKey: ["coverage-history"],
    queryFn:  coverageApi.listAll,
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => coverageApi.deleteResult(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["coverage-history"] });
      notify("✅ תוצאה נמחקה");
    },
    onError: () => notify("⚠ שגיאה במחיקה"),
  });

  async function handleLoad(item: HistoryItem) {
    setLoadingId(item.id);
    try {
      const result = await coverageApi.getResult(item.id);
      setCoverageResults([result]);
      setBbox({
        sw_lat: item.poly_sw_lat, sw_lon: item.poly_sw_lon,
        ne_lat: item.poly_ne_lat, ne_lon: item.poly_ne_lon,
      });
      setActiveTab("analysis");
      notify(`✅ תוצאה #${item.id} נטענה למפה`);
    } catch {
      notify("⚠ שגיאה בטעינת התוצאה");
    } finally {
      setLoadingId(null);
    }
  }

  async function handleExport(item: HistoryItem) {
    setExportingId(item.id);
    try {
      const report = await coverageApi.getReport(item.id);
      const html   = buildReportHTML(report);
      const blob   = new Blob([html], { type: "text/html;charset=utf-8" });
      const url    = URL.createObjectURL(blob);
      const a      = document.createElement("a");
      a.href       = url;
      a.download   = `coverage_report_${item.site_name}_${item.id}.html`;
      a.click();
      URL.revokeObjectURL(url);
      notify("✅ הדוח הורד בהצלחה");
    } catch {
      notify("⚠ שגיאה בייצוא הדוח");
    } finally {
      setExportingId(null);
    }
  }

  if (isLoading) {
    return <div className={styles.empty}>טוען היסטוריה...</div>;
  }

  if (!history.length) {
    return (
      <div className={styles.empty}>
        <div className={styles.emptyIcon}>📋</div>
        אין ניתוחים שמורים במערכת
      </div>
    );
  }

  function handleClearCrossSection() {
    setCrossSectionResult(null);
    setRouteWaypoints([]);
    notify("✅ חתך הרדיו הוסר מהמפה");
  }

  return (
    <div>
      {crossSectionResult && (
        <button className={styles.clearCrossSection} onClick={handleClearCrossSection}>
          ✕ הסר חתך רדיו מהמפה
        </button>
      )}
      <div className={styles.sectionHeader}>היסטוריית ניתוחים ({history.length})</div>

      {history.map((item) => (
        <div key={item.id} className={styles.card}>
          <div className={styles.cardTop}>
            <span className={styles.siteName}>{item.site_name}</span>
            <span className={`${styles.modeBadge} ${item.mode === "DSM" ? styles.dsm : ""}`}>
              {item.mode}
            </span>
          </div>

          <div className={styles.cardMeta}>
            <span>📅 {formatDate(item.created_at)}</span>
            <span>📶 {item.covered_pct}% כיסוי</span>
            <span>⚡ {item.rssi_avg} dBm ממוצע</span>
          </div>

          <div className={styles.actions}>
            <button
              className={`${styles.btn} ${styles.load}`}
              onClick={() => handleLoad(item)}
              disabled={loadingId === item.id}
            >
              {loadingId === item.id ? "טוען..." : "⬆ טען למפה"}
            </button>
            <button
              className={`${styles.btn} ${styles.export}`}
              onClick={() => handleExport(item)}
              disabled={exportingId === item.id}
            >
              {exportingId === item.id ? "מייצא..." : "📄 ייצא דוח"}
            </button>
            <button
              className={`${styles.btn} ${styles.del}`}
              onClick={() => deleteMut.mutate(item.id)}
              disabled={deleteMut.isPending}
            >
              ✕
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
