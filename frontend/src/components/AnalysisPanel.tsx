import { useState } from "react";
import { useStore } from "../store/useStore";
import { coverageApi } from "../services/api";
import type { CoverageResult } from "../services/api";
import { notify } from "./Notification";
import styles from "./AnalysisPanel.module.css";
import type { MapLayer } from "../store/useStore";

const LEGEND = [
  {
    label: "מצוין",  value: "> −70 dBm",   color: "var(--cov-excellent)",
    desc: "קליטה מלאה — שיחה/נתונים ברמה הגבוהה ביותר",
  },
  {
    label: "טוב",    value: "−70 עד −80",  color: "var(--cov-good)",
    desc: "קליטה טובה מאוד — ביצועי קצה שמורים לרוב",
  },
  {
    label: "בינוני", value: "−80 עד −90",  color: "var(--cov-medium)",
    desc: "קליטה סבירה — שיחה אמינה, נתונים עשויים להאט",
  },
  {
    label: "חלש",    value: "−90 עד −100", color: "var(--cov-weak)",
    desc: "קליטה ירודה — ירידה בקצב נתונים, שיחה אפשרית",
  },
  {
    label: "שולי",   value: "−100 עד −110",color: "var(--cov-marginal)",
    desc: "על סף הסף — קישור לא יציב, פינוי עשוי לבצע ניתוק",
  },
  {
    label: "אין",    value: "< −110 dBm",  color: "#ffffff",
    desc: "אפלה — אין קישוריות, נדרש ממסר / כיסוי חלופי",
  },
];

const FREQ_COLORS: Record<number, string> = {
  700: "#ff6b35", 850: "#ffb347", 900:  "#00ff88",
  1800: "#00d4ff", 2100: "#a78bfa", 2600: "#f472b6",
  3500: "#f59e0b",
};

export default function AnalysisPanel() {
  const [infoOpen, setInfoOpen] = useState(false);

  const {
    sites,
    analysisMode, setAnalysisMode,
    selectedSiteIds, setSelectedSiteIds,
    setActiveTab, setDrawMode,
    bbox, setBbox,
    coverageResults, setCoverageResults, setIsCalculating,
    isCalculating,
    addMapLayer,
  } = useStore();

  function toggleSite(id: number) {
    if (selectedSiteIds.includes(id)) {
      setSelectedSiteIds(selectedSiteIds.filter((x) => x !== id));
    } else {
      setSelectedSiteIds([...selectedSiteIds, id]);
    }
  }

  async function runAnalysis() {
    if (!selectedSiteIds.length) { notify("⚠ בחר לפחות אתר אחד"); return; }
    if (!bbox)                    { notify("⚠ הגדר תא שטח"); return; }

    setIsCalculating(true);
    setCoverageResults([]);

    const results: CoverageResult[] = [];
    for (const siteId of selectedSiteIds) {
      try {
        const result = await coverageApi.analyze({
          site_id: siteId,
          mode: analysisMode,
          sw_lat: bbox.sw_lat, sw_lon: bbox.sw_lon,
          ne_lat: bbox.ne_lat, ne_lon: bbox.ne_lon,
          resolution: 300,
        });
        results.push(result);
      } catch (err: any) {
        setIsCalculating(false);
        notify(`⚠ ${err.response?.data?.detail ?? "שגיאה בחישוב"}`);
        return;
      }
    }

    setCoverageResults(results);
    setIsCalculating(false);

    // Create a layer entry in the layer manager for each result
    results.forEach((result) => {
      const siteName = sites.find((s) => s.id === result.site_id)?.name ?? `אתר ${result.site_id}`;
      const layer: MapLayer = {
        id:         `coverage-${result.id}`,
        name:       `${siteName} — ${result.mode}`,
        type:       "coverage",
        visible:    true,
        resultId:   result.id,
        bbox:       result.sw_lat
          ? { sw_lat: result.sw_lat!, sw_lon: result.sw_lon!, ne_lat: result.ne_lat!, ne_lon: result.ne_lon! }
          : undefined,
        mode:       result.mode,
        coveredPct: result.covered_pct,
        siteName,
        createdAt:  result.created_at,
      };
      addMapLayer(layer);
    });

    const avgPct = (results.reduce((s, r) => s + r.covered_pct, 0) / results.length).toFixed(1);
    notify(`✅ ניתוח הושלם – ${avgPct}% כיסוי ממוצע | שכבה נוצרה בטאב שכבות`);
  }

  function clearResults() {
    setCoverageResults([]);
    notify("תוצאות נוקו");
  }

  const step1Done = selectedSiteIds.length > 0;
  const step2Done = !!bbox;
  const readyToRun = step1Done && step2Done;

  return (
    <div>
      <div className={styles.sectionHeader}>
        אשף ניתוח כיסוי
        <button
          className={styles.infoBtn}
          onClick={() => setInfoOpen((v) => !v)}
          title="מידע על שיטת החישוב"
        >
          ⓘ
        </button>
      </div>

      {/* Info panel – methodology */}
      {infoOpen && (
        <div className={styles.infoPanel}>
          <div className={styles.infoPanelClose}>
            <span className={styles.infoPanelTitle}>כיצד מחושב ניתוח הכיסוי?</span>
            <button className={styles.infoPanelCloseBtn} onClick={() => setInfoOpen(false)}>✕</button>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>🛰 מודל הפצה</div>
            <div className={styles.infoText}>
              החישוב מבוסס על שילוב של שני מרכיבים:<br />
              <strong>FSPL</strong> (Free Space Path Loss) — נוסחת פריס לחישוב
              אובדן נתיב במרחב חופשי בפונקציה של מרחק ותדר:<br />
              <span className={styles.formula}>RSSI = Pₜ − FSPL(d,f) − Ldiff + Ghₐ</span><br />
              <strong>ITU-R P.526</strong> — אפקט עקיפת סכין (Knife-Edge Diffraction)
              על בסיס פרופיל גובה אמיתי מ-DEM.
            </div>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>🗺 נתוני גובה (DEM)</div>
            <div className={styles.infoText}>
              המערכת מורידה אוטומטית tiles של גובה שטח מ-
              <strong> Terrarium RGB</strong> (AWS S3), ברזולוציה של
              עד ~10 מ׳/פיקסל. כל tile מקוד RGB:
              <span className={styles.formula}>h(מ׳) = R×256 + G + B÷256 − 32768</span>
              רשת הניתוח: <strong>300×300 = 90,000 תאים</strong>, כל תא בוחן
              24 נקודות על פרופיל הנתיב מהמשדר.
            </div>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>⚡ שלבי החישוב לכל תא</div>
            <div className={styles.infoSteps}>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>1</span>חישוב FSPL לפי מרחק Haversine ותדר</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>2</span>דגימת 24 נקודות על פרופיל הגובה לאורך הנתיב</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>3</span>לכל נקודה: חישוב פרמטר ν (Fresnel-Kirchhoff)</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>4</span>הפסד עקיפה מקסימלי → מחסום הכי קריטי בנתיב</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>5</span>רווח גובה אנטנה: 20·log₁₀(h / 1.5 מ׳)</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>6</span>DSM: הוספת 9 dB הפסד תכסית (מבנים/צמחייה)</div>
            </div>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>🎨 סולם עוצמות האות — פירוט</div>
            {LEGEND.map((row) => (
              <div key={row.label} className={styles.infoLegendRow}>
                <div className={styles.infoLegendColor} style={{
                  background: row.color,
                  border: row.color === "#ffffff" ? "1px solid #555" : undefined,
                }} />
                <div className={styles.infoLegendBody}>
                  <div className={styles.infoLegendLabel}>
                    <strong>{row.label}</strong>
                    <span className={styles.infoLegendValue}>{row.value} dBm</span>
                  </div>
                  <div className={styles.infoLegendDesc}>{row.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Step 1 – multi-site selection */}
      <div className={`${styles.stepCard} ${step1Done ? styles.completed : ""}`}>
        <div className={styles.stepHeader}>
          <span>1. בחירת מוקדי קשר</span>
          {step1Done
            ? <span className={`${styles.stepStatus} ${styles.done}`}>✔ {selectedSiteIds.length} נבחרו</span>
            : <span className={`${styles.stepStatus} ${styles.pending}`}>ממתין</span>}
        </div>

        {sites.length === 0 ? (
          <button className={styles.actionBtn} onClick={() => setActiveTab("new")}>
            + אין אתרים – הקם אתר חדש מכאן
          </button>
        ) : (
          <>
            <div className={styles.siteCheckList}>
              {sites.map((s) => {
                const checked = selectedSiteIds.includes(s.id);
                const color   = FREQ_COLORS[s.frequency] ?? "var(--accent)";
                return (
                  <label
                    key={s.id}
                    className={`${styles.siteCheckItem} ${checked ? styles.checked : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleSite(s.id)}
                    />
                    <span
                      className={styles.freqDot}
                      style={{ background: color }}
                    />
                    <span style={{ flex: 1 }}>{s.name}</span>
                    <span style={{ color: "var(--text-dim)", fontSize: 10 }}>{s.frequency} MHz</span>
                  </label>
                );
              })}
            </div>
            <button
              className={styles.actionBtn}
              style={{ marginTop: 8 }}
              onClick={() => setActiveTab("new")}
            >
              + הקם אתר חדש
            </button>
          </>
        )}
      </div>

      {/* Step 2 */}
      <div className={`${styles.stepCard} ${step2Done ? styles.completed : ""}`}>
        <div className={styles.stepHeader}>
          <span>2. הגדרת תא שטח במפה</span>
          {step2Done ? <span className={`${styles.stepStatus} ${styles.done}`}>✔ הוגדר</span> : <span className={`${styles.stepStatus} ${styles.pending}`}>ממתין</span>}
        </div>
        {!step2Done ? (
          <>
            <div style={{fontSize: 11, color: "var(--text-dim)", marginBottom: 10, lineHeight: 1.4}}>
              לחץ כאן ולאחר מכן גש למפה לשרטוט השטח אותו תרצה לסרוק.
            </div>
            <button className={styles.actionBtn} onClick={() => setDrawMode("rect")}>🔲 שרטט מלבן כיסוי חופשי</button>
            <button className={styles.actionBtn} onClick={() => setDrawMode("square")}>⬛ שרטט ריבוע כיסוי שווה צלעות</button>
          </>
        ) : (
          <div style={{fontSize: 12, color: "var(--text-dim)"}}>
            <span style={{color: "var(--accent2)"}}>תא שטח הוגדר.</span> ניתן לגרור אותו במפה.
            <button className={`${styles.actionBtn} ${styles.danger}`} style={{marginTop: 8, padding: "5px"}} onClick={() => { setBbox(null); setCoverageResults([]); }}>✕ אפס ובחר חדש</button>
          </div>
        )}
      </div>

      {/* Step 3 */}
      <div className={`${styles.stepCard} ${readyToRun ? styles.completed : ""}`}>
        <div className={styles.stepHeader}>
          <span>3. חישוב</span>
        </div>
        <div className={styles.modeToggle}>
          <button
            className={`${styles.modeBtn} ${analysisMode === "DTM" ? styles.active : ""}`}
            onClick={() => setAnalysisMode("DTM")}
          >
            📏 טופוגרפיה<br /><small>DTM בלבד</small>
          </button>
          <button
            className={`${styles.modeBtn} ${analysisMode === "DSM" ? styles.active : ""}`}
            onClick={() => setAnalysisMode("DSM")}
          >
            🏢 תכסית<br /><small>DSM + מבנים</small>
          </button>
        </div>
        <button className={styles.runBtn} onClick={runAnalysis} disabled={!readyToRun || isCalculating}>
          {isCalculating ? "⏳ מחשב כיסוי..." : `▶ הרץ חישוב${selectedSiteIds.length > 1 ? ` (${selectedSiteIds.length} אתרים)` : ""}`}
        </button>
        {coverageResults.length > 0 && (
          <button className={`${styles.actionBtn} ${styles.danger}`} style={{marginTop: 6}} onClick={clearResults}>
            ✕ נקה תוצאות מהמפה
          </button>
        )}
      </div>

      {/* Legend */}
      <div className={styles.sectionHeader} style={{ marginTop: 16 }}>סולם צבעים</div>
      <div className={styles.legend}>
        {LEGEND.map((row) => (
          <div key={row.label} className={styles.legendRow}>
            <div
              className={styles.legendColor}
              style={{
                background: row.color,
                border: row.color === "#ffffff" ? "1px solid #555" : undefined,
              }}
            />
            <div className={styles.legendLabel}>{row.label}</div>
            <div className={styles.legendValue}>{row.value}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: -8, marginBottom: 8, textAlign: "center" }}>
        לחץ ⓘ למעלה לפירוט מלא של כל רמה וכיצד מחושב הכיסוי
      </div>

      {/* Stats – per site */}
      {coverageResults.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className={styles.sectionHeader}>תוצאות ניתוח</div>
          {coverageResults.map((r) => {
            const siteName = sites.find((s) => s.id === r.site_id)?.name ?? `אתר ${r.site_id}`;
            return (
              <div key={r.id} className={styles.siteResultBlock}>
                <div className={styles.siteResultTitle}>{siteName}</div>
                <div className={styles.statsGrid}>
                  <div className={styles.statBox}>
                    <div className={styles.statValue}>{r.covered_pct}%</div>
                    <div className={styles.statLabel}>שטח מכוסה</div>
                  </div>
                  <div className={styles.statBox}>
                    <div className={styles.statValue}>{r.rssi_avg}</div>
                    <div className={styles.statLabel}>RSSI ממוצע</div>
                  </div>
                  <div className={styles.statBox}>
                    <div className={styles.statValue}>{r.rssi_max}</div>
                    <div className={styles.statLabel}>מקסימום</div>
                  </div>
                  <div className={styles.statBox}>
                    <div className={styles.statValue}>{r.rssi_min}</div>
                    <div className={styles.statLabel}>מינימום</div>
                  </div>
                </div>
                <div className={styles.duration}>
                  ⏱ חושב ב-{r.duration_sec}s | מודל {r.mode}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
