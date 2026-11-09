import { useState } from "react";
import { useStore } from "../store/useStore";
import { apiErrorText, coverageApi } from "../services/api";
import type { CoverageResult } from "../services/api";
import {
  collectWarnings, demLabel, demStatus, fmtCoveredVsThreshold, fmtDbm, fmtNum, fmtPct, DASH,
  isLegacyThreshold, layerFromResult, runWithConcurrency,
} from "../services/coverageFormat";
import { notify } from "./Notification";
import styles from "./AnalysisPanel.module.css";

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

/** Max /analyze requests in flight at once. */
const ANALYZE_CONCURRENCY = 3;

type RunStatus = "queued" | "pending" | "done" | "failed";
interface SiteRun { status: RunStatus; error?: string; coveredPct?: number }

const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  queued:  "… בתור",
  pending: "⏳ מחשב...",
  done:    "✔ הושלם",
  failed:  "✕ נכשל",
};

const errorText = (err: any) => apiErrorText(err, "שגיאה בחישוב");

export default function AnalysisPanel() {
  const [infoOpen, setInfoOpen] = useState(false);
  const [runs, setRuns] = useState<Record<number, SiteRun>>({});

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

    // Snapshot inputs — the user may change selection/bbox while we compute
    const siteIds = [...selectedSiteIds];
    const area    = { ...bbox };
    const mode    = analysisMode;
    const nameOf  = (id: number) => sites.find((s) => s.id === id)?.name ?? `אתר ${id}`;

    setIsCalculating(true);
    setCoverageResults([]);
    setRuns(Object.fromEntries(siteIds.map((id) => [id, { status: "queued" as RunStatus }])));

    // At most ANALYZE_CONCURRENCY sites in flight; each one reports independently as it settles.
    // `resolution` is intentionally omitted → server picks an adaptive grid.
    const settled = await runWithConcurrency(siteIds, ANALYZE_CONCURRENCY,
      async (siteId): Promise<CoverageResult> => {
        setRuns((prev) => ({ ...prev, [siteId]: { status: "pending" } }));
        try {
          const result = await coverageApi.analyze({
            site_id: siteId,
            mode,
            sw_lat: area.sw_lat, sw_lon: area.sw_lon,
            ne_lat: area.ne_lat, ne_lon: area.ne_lon,
          });
          setRuns((prev) => ({ ...prev, [siteId]: { status: "done", coveredPct: result.covered_pct } }));
          // Show each result + its map layer as soon as it arrives
          const current = useStore.getState().coverageResults;
          setCoverageResults([...current.filter((r) => r.id !== result.id), result]);
          addMapLayer(layerFromResult(result, nameOf(result.site_id)));
          return result;
        } catch (err: any) {
          const msg = errorText(err);
          setRuns((prev) => ({ ...prev, [siteId]: { status: "failed", error: msg } }));
          throw new Error(msg);
        }
      },
    );

    setIsCalculating(false);

    const ok   = settled.flatMap((s) => (s.status === "fulfilled" ? [s.value] : []));
    const fail = settled.length - ok.length;

    if (!ok.length) {
      notify(`⚠ כל ${settled.length} החישובים נכשלו`);
      return;
    }
    const avgPct = (ok.reduce((s, r) => s + (r.covered_pct ?? 0), 0) / ok.length).toFixed(1);
    const simulated = ok.filter((r) => demStatus(r.dem_source) === "simulated").length;
    notify(
      `${fail ? "⚠" : "✅"} ניתוח הושלם – ${ok.length}/${settled.length} אתרים` +
      ` | ${avgPct}% כיסוי ממוצע מעל סף` +
      (fail ? ` | ${fail} נכשלו` : "") +
      (simulated ? ` | ${simulated} על טרן מדומה` : "") +
      " | שכבות נוצרו בטאב שכבות",
    );
  }

  function clearResults() {
    setCoverageResults([]);
    setRuns({});
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
              החישוב מבוסס על שילוב של שלושה מרכיבים:<br />
              <strong>FSPL</strong> (Free Space Path Loss) — נוסחת פריס לחישוב
              אובדן נתיב במרחב חופשי בפונקציה של מרחק ותדר:<br />
              <span className={styles.formula}>RSSI = Pₜ − FSPL(d,f) − Ldiff + Ghₐ</span><br />
              <strong>עקיפה רב-מכשולית — שיטת Deygout</strong> (ITU-R P.526 §4.5):
              מאתרים את המכשול הראשי בנתיב, ולאחר מכן את המכשולים המשניים
              משני צדיו — ולא רק את המחסום הבודד הגבוה ביותר.<br />
              <strong>עקמומיות כדור הארץ</strong> — פרופיל הגובה מתוקן לפי רדיוס
              אפקטיבי <strong>k = 4/3</strong> (שבירה אטמוספרית סטנדרטית).
            </div>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>🗺 נתוני גובה (DEM)</div>
            <div className={styles.infoText}>
              המערכת מורידה אוטומטית tiles של גובה שטח מ-
              <strong> Terrarium RGB</strong> (AWS S3). כל tile מקוד RGB:
              <span className={styles.formula}>h(מ׳) = R×256 + G + B÷256 − 32768</span>
              <strong>tiles חסרים</strong> מסומנים כ-NaN ואינם מוחלפים בגובה 0 —
              כך שים / חור בנתונים לא ייצור &quot;מישור&quot; מדומה. אחוז הפיקסלים התקינים
              מוצג בתוצאה.<br />
              כאשר לא נמצאו נתוני DEM אמיתיים התוצאה מסומנת כ-
              <strong> סימולציה</strong> ומוצגת אזהרה.
            </div>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>📐 רשת אדפטיבית</div>
            <div className={styles.infoText}>
              הרזולוציה נקבעת אוטומטית לפי גודל תא השטח: יעד של
              <strong> ~40 מ׳ לתא</strong>, בתחום של <strong>60–500 תאים לציר</strong>.
              שטח קטן מקבל רשת צפופה ומדויקת; שטח גדול נשאר מהיר לחישוב.
            </div>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>⚡ שלבי החישוב לכל תא</div>
            <div className={styles.infoSteps}>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>1</span>חישוב FSPL לפי מרחק Haversine ותדר</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>2</span>דגימת פרופיל הגובה לאורך הנתיב + תיקון עקמומיות (k=4/3)</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>3</span>לכל נקודה: חישוב פרמטר ν (Fresnel-Kirchhoff)</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>4</span>Deygout: מכשול ראשי + מכשולים משניים → הפסד עקיפה כולל</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>5</span>רווח גובה אנטנה: 20·log₁₀(h / 1.5 מ׳) — מוגבל (capped) כשקיים DEM, כי הטרן כבר מגולם בפרופיל</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>6</span>DSM: הוספת 9 dB הפסד תכסית (מבנים/צמחייה)</div>
              <div className={styles.infoStep}><span className={styles.infoStepNum}>7</span>תאים ללא נתוני גובה (NaN) אינם נספרים כ&quot;מכוסים&quot;</div>
            </div>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>📶 מהו &quot;שטח מכוסה&quot;?</div>
            <div className={styles.infoText}>
              <strong>אחוז הכיסוי מחושב ביחס לסף הקליטה (rx_threshold) של האתר</strong> —
              אחוז התאים שבהם RSSI ≥ הסף, למשל <span className={styles.inlineCode}>72% ≥ −90 dBm</span>.<br />
              כמספר משני מוצג גם אחוז השטח עם <strong>אות כלשהו</strong> (&gt; −110 dBm),
              שהיה ההגדרה הקודמת — ולכן גבוה יותר.
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
            <button className={`${styles.actionBtn} ${styles.danger}`} style={{marginTop: 8, padding: "5px"}} onClick={() => { setBbox(null); setCoverageResults([]); setRuns({}); }}>✕ אפס ובחר חדש</button>
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
        {Object.keys(runs).length > 0 && (
          <div className={styles.runList}>
            {Object.entries(runs).map(([id, run]) => {
              const siteName = sites.find((s) => s.id === +id)?.name ?? `אתר ${id}`;
              return (
                <div key={id} className={`${styles.runRow} ${styles[`run_${run.status}`] ?? ""}`}>
                  <span className={styles.runName}>{siteName}</span>
                  <span className={styles.runStatus} title={run.error}>
                    {RUN_STATUS_LABEL[run.status]}
                    {run.status === "done" && run.coveredPct != null && ` · ${fmtPct(run.coveredPct)}`}
                  </span>
                  {run.status === "failed" && run.error && (
                    <div className={styles.runError}>{run.error}</div>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {(coverageResults.length > 0 || Object.keys(runs).length > 0) && !isCalculating && (
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
            // Only the threshold returned WITH this result — never the site's current rx_threshold.
            const threshold = r.threshold_dbm;
            const legacy    = isLegacyThreshold(threshold);
            const warnings  = collectWarnings(r.warnings, r.dem_source);
            const dem       = demStatus(r.dem_source);
            return (
              <div key={r.id} className={styles.siteResultBlock}>
                <div className={styles.siteResultTitle}>
                  {siteName}
                  <span className={`${styles.demBadge} ${styles[`dem_${dem}`] ?? ""}`}>
                    {dem === "unknown" ? "DEM ?" : demLabel(r.dem_source)}
                  </span>
                </div>

                {warnings.length > 0 && (
                  <div className={styles.warnBox}>
                    {warnings.map((w, i) => (
                      <div key={i} className={styles.warnLine}><span>⚠</span><span>{w}</span></div>
                    ))}
                  </div>
                )}

                <div className={styles.coveredMain}>
                  <div className={styles.coveredValue}>{fmtCoveredVsThreshold(r.covered_pct, threshold)}</div>
                  <div className={styles.statLabel}>
                    {legacy ? "תוצאה ישנה — האחוז מתייחס לאות כלשהו (> −110 dBm)" : "שטח מכוסה מעל סף הקליטה בעת הניתוח"}
                  </div>
                  <div className={styles.coveredSecondary}>
                    אות כלשהו (&gt; −110 dBm): <strong>{fmtPct(r.covered_pct_any_signal)}</strong>
                    {r.weak_pct != null && <> · חלש/שולי: <strong>{fmtPct(r.weak_pct)}</strong></>}
                  </div>
                </div>

                <div className={styles.statsGrid3}>
                  <div className={styles.statBox}>
                    <div className={styles.statValue}>{fmtNum(r.rssi_avg)}</div>
                    <div className={styles.statLabel}>RSSI ממוצע</div>
                  </div>
                  <div className={styles.statBox}>
                    <div className={styles.statValue}>{fmtNum(r.rssi_max)}</div>
                    <div className={styles.statLabel}>מקסימום</div>
                  </div>
                  <div className={styles.statBox}>
                    <div className={styles.statValue}>{fmtNum(r.rssi_min)}</div>
                    <div className={styles.statLabel}>מינימום</div>
                  </div>
                </div>

                <div className={styles.metaRow}>
                  <span>רזולוציה: <strong>{r.resolution != null ? `${r.resolution}×${r.resolution}` : DASH}</strong></span>
                  <span>DEM: <strong>{r.dem_source ?? DASH}</strong>
                    {r.dem_valid_pct != null && ` (${fmtPct(r.dem_valid_pct, 0)} תקין)`}
                  </span>
                  {r.dem_m_per_px != null && <span>{fmtNum(r.dem_m_per_px, 0)} מ׳/פיקסל</span>}
                  {threshold != null && <span>סף: <strong>{fmtDbm(threshold)}</strong></span>}
                </div>
                <div className={styles.duration}>
                  ⏱ חושב ב-{fmtNum(r.duration_sec, 1)}s | מודל {r.mode}
                  {r.diffraction_method && ` | ${r.diffraction_method}`}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
