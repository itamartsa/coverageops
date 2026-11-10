/**
 * Info + options of the "מדידות שטח" layer on the main map (spec 8.1, in the CellSitesInfo
 * pattern): measure, value, operator, time window, the MEASURED tier legend with its RSRP
 * thresholds (a different metric from the predicted-coverage RSSI legend — contract v2 D-CR11)
 * and a link to the field dashboard.
 */
import { useNavigate } from "react-router-dom";
import { useStore } from "../store/useStore";
import { useOperators } from "../lib/fieldQueries";
import { TIER_ORDER, tierDef, tierRangeText, barsSvg } from "../lib/fieldTiers";
import styles from "./CellSitesInfo.module.css";

export default function FieldHeatInfo() {
  const opts = useStore((s) => s.fieldHeatOpts);
  const setOpts = useStore((s) => s.setFieldHeatOpts);
  const ops = useOperators();
  const navigate = useNavigate();
  const sel: React.CSSProperties = {
    background: "var(--bg3)", border: "1px solid var(--border)", color: "var(--text-bright)",
    fontFamily: "inherit", fontSize: 11, padding: "3px 6px", width: "100%",
  };
  return (
    <div className={styles.root} data-testid="field-heat-info">
      <div className={styles.p}>
        מפת חום של מדידות אמת ממכשירי השטח (אפליקציית Field Probe) — <b>נמדד</b>, לא חזוי.
        המדד הוא <b>RSRP</b> (LTE / עוגן NSA) או <b>SS-RSRP</b> (5G SA), ולכן הספים שונים ממקרא ניתוחי הכיסוי (RSSI).
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 8px", alignItems: "center", fontSize: 11, marginTop: 8 }}>
        <label htmlFor="fh-measure">מדד</label>
        <select id="fh-measure" style={sel} value={opts.measure} onChange={(e) => setOpts({ measure: e.target.value as typeof opts.measure })}>
          <option value="lte_rsrp">RSRP LTE (כולל עוגן NSA)</option>
          <option value="nr_ss_rsrp">SS-RSRP NR (5G SA)</option>
        </select>
        <label htmlFor="fh-metric">ערך</label>
        <select id="fh-metric" style={sel} value={opts.metric} onChange={(e) => setOpts({ metric: e.target.value as typeof opts.metric })}>
          <option value="p50">חציון (P50)</option><option value="p5">אחוזון 5 (P5)</option><option value="oos_pct">% זמן ללא שירות</option>
        </select>
        <label htmlFor="fh-op">מפעיל</label>
        <select id="fh-op" style={sel} value={opts.operator ?? ""} onChange={(e) => setOpts({ operator: e.target.value || null })}>
          <option value="">כל המפעילים</option>
          {(ops.data ?? []).map((o) => <option key={o.operator_key} value={o.operator_key}>{o.name_he}</option>)}
        </select>
        <label htmlFor="fh-win">חלון</label>
        <select id="fh-win" style={sel} value={opts.window} onChange={(e) => setOpts({ window: e.target.value as typeof opts.window })}>
          <option value="24h">24 שעות</option><option value="7d">7 ימים</option><option value="30d">30 ימים</option>
        </select>
      </div>
      <div style={{ marginTop: 10, fontSize: 11 }}>
        <div style={{ color: "var(--muted-ink)", marginBottom: 4 }}>מקרא מדידות · RSRP dBm (נמדד)</div>
        {TIER_ORDER.map((t) => {
          const d = tierDef(t);
          return (
            <div key={t} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
              <span style={{ width: 16, height: 12, background: d.color, opacity: 0.85, display: "inline-flex", alignItems: "center", justifyContent: "center" }}
                aria-hidden="true" dangerouslySetInnerHTML={{ __html: barsSvg(d.bars, { size: 10, fill: "#0a0e14", dim: "rgba(10,14,20,.6)" }) }} />
              <span>{d.label_he}</span>
              <bdi dir="ltr" style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 600 }}>{tierRangeText(t)}</bdi>
            </div>
          );
        })}
      </div>
      <button className={styles.syncBtn} style={{ marginTop: 10 }} onClick={() => navigate("/field?tab=analysis")}>
        📶 פתח דאשבורד שטח
      </button>
    </div>
  );
}
