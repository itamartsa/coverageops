import { useState } from "react";
import { apiErrorText, apiErrorTextAsync, coverageApi, type CrossSectionResult } from "../services/api";
import { useStore } from "../store/useStore";
import { notify } from "./Notification";
import InfoTooltip from "./InfoTooltip";
import { canWrite, READ_ONLY_NOTE } from "../lib/roles";
import { collectWarnings, demLabel, demStatus, fmtCoveredVsThreshold, fmtNum, fmtPct } from "../services/coverageFormat";
import styles from "./CrossSectionPanel.module.css";

const LEVEL_COLORS: Record<string, string> = {
  excellent: "#00ff88", good: "#7dff6b", medium: "#ffe600",
  weak: "#ff8c00", marginal: "#ff3b5c", none: "#555",
};
const LEVEL_HE: Record<string, string> = {
  excellent: "מצוין", good: "טוב", medium: "בינוני",
  weak: "חלש", marginal: "שולי", none: "אין",
};
const RISK_COLORS: Record<string, string> = {
  CRITICAL: "#cc0000", HIGH: "#ff4444", MEDIUM: "#ffa500", LOW: "#00aa44",
};
const RISK_HE: Record<string, string> = {
  CRITICAL: "קריטי", HIGH: "גבוה", MEDIUM: "בינוני", LOW: "נמוך",
};
const TIPS = {
  rssi_min:  "RSSI מינימלי (dBm) לאורך הנתיב.\nמייצג את נקודת הקשר הקריטית ביותר בציר.\nמתחת ל-100 dBm = קשר לא אמין בשטח.",
  risk:      "רמת סיכון מבצעי של הנתיב:\nLOW = כיסוי תקין\nMEDIUM = גבולי, מומלץ לבדוק\nHIGH = בעייתי, נדרשת פעולה\nCRITICAL = לא מתאים לתקשורת בלא ממסר",
  covered:   "אחוז הנתיב בו עוצמת האות מעל סף הקליטה.\nמתחת ל-80% = ציר מסוכן לתקשורת רציפה.",
  dead_zones:"אזורים ללא קשר בכלל לאורך הנתיב.\nכל אזור מת = אפלה קשר מוחלטת. יש לתכנן ממסרים.",
  risk_score:"ציון סיכון בסולם 1–4 המחושב בשרת:\n1 = נמוך (LOW)\n2 = בינוני (MEDIUM)\n3 = גבוה (HIGH)\n4 = קריטי (CRITICAL)",
  dtm_dsm:   "DTM = מודל טרן בלבד (שטח פתוח, ללא בניינים).\nDSM = מודל עם בניינים ועצים (מתאים לעירוני).\nבשטח לחימה – DSM מדויק יותר.",
};

export default function CrossSectionPanel() {
  const {
    sites, analysisMode, setAnalysisMode,
    drawMode, setDrawMode,
    routeWaypoints, setRouteWaypoints,
    setCrossSectionResult, user,
    crossSectionSiteId: siteId, setCrossSectionSiteId: setSiteId,
  } = useStore();
  const writable = canWrite(user?.role);

  // The chosen site lives in the store: its marker is shown on the map while it is chosen here
  const [result,    setResult]    = useState<CrossSectionResult | null>(null);
  const [loading,   setLoading]   = useState(false);
  const [exporting, setExporting] = useState(false);
  const [infoOpen,  setInfoOpen]  = useState(false);

  const inWaypointMode = drawMode === "waypoint";

  const step1Done = siteId !== null;
  const step2Done = routeWaypoints.length >= 2;
  const readyToRun = step1Done && step2Done;

  function toggleWaypointMode() {
    if (inWaypointMode) {
      setDrawMode(null);
    } else {
      setDrawMode("waypoint");
      notify("לחץ על המפה להוספת נקודות מסלול. לחץ 'סיים' כשתסיים.");
    }
  }

  function removeLastWaypoint() {
    setRouteWaypoints(routeWaypoints.slice(0, -1));
  }

  /** Clears the route and its result; the chosen site (and its map marker) stays chosen. */
  function clearRoute() {
    setRouteWaypoints([]);
    setResult(null);
    setCrossSectionResult(null);
    setDrawMode(null);
  }

  async function runAnalysis() {
    if (!siteId)                   { notify("⚠ בחר אתר קשר"); return; }
    if (routeWaypoints.length < 2) { notify("⚠ הגדר לפחות 2 נקודות מסלול"); return; }

    setLoading(true);
    setDrawMode(null);
    try {
      const res = await coverageApi.crossSection({
        site_id: siteId, mode: analysisMode,
        waypoints: routeWaypoints, resolution: 5,
      });
      setResult(res);
      setCrossSectionResult(res);
      const sim = demStatus(res.dem_source) === "simulated";
      notify(`${sim ? "⚠" : "✅"} חתך הושלם – ${fmtCoveredVsThreshold(res.covered_pct, res.threshold_dbm, { legacyLabel: false })} כיסוי | סיכון: ${RISK_HE[res.risk_level] ?? res.risk_level}` +
             (sim ? " | טרן מדומה" : ""));
    } catch (e: any) {
      notify(`⚠ ${apiErrorText(e, "שגיאה בחישוב")}`);
    } finally {
      setLoading(false);
    }
  }

  async function downloadDocx() {
    if (!siteId || routeWaypoints.length < 2) return;
    setExporting(true);
    try {
      const blob = await coverageApi.crossSectionDocx({
        site_id: siteId, mode: analysisMode,
        waypoints: routeWaypoints, resolution: 5,
      });
      const siteName = sites.find(s => s.id === siteId)?.name ?? "site";
      const url = URL.createObjectURL(blob);
      const a   = document.createElement("a");
      a.href = url; a.download = `חתך_רדיו_${siteName}.docx`; a.click();
      URL.revokeObjectURL(url);
      notify("✅ הדוח הורד בהצלחה");
    } catch (e: any) {
      notify(`⚠ ${await apiErrorTextAsync(e, "שגיאה בייצוא הדוח")}`);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div>
      <div className={styles.header}>
        חתך רדיו – ניתוח ציר תנועה
        <button
          className={styles.infoBtn}
          onClick={() => setInfoOpen(v => !v)}
          title="מידע על שיטת החישוב"
        >ⓘ</button>
      </div>

      {/* Info panel */}
      {infoOpen && (
        <div className={styles.infoPanel}>
          <div className={styles.infoPanelTitle}>
            <span>כיצד מחושב חתך הרדיו?</span>
            <button className={styles.infoPanelClose} onClick={() => setInfoOpen(false)}>✕</button>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>🛰 מודל הפצה</div>
            <div className={styles.infoText}>
              לכל נקודה לאורך הציר מחושבת עוצמת האות לפי:<br />
              <span className={styles.formula}>RSSI = Pₜ − FSPL(d,f) − Ldiff(ν) + Ghₐ</span>
              שבו <b>Ldiff</b> הוא הפסד עקיפת סכין (ITU-R P.526) המחושב
              מפרופיל גובה <b>אמיתי</b> לאורך הנתיב.
            </div>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>🗺 פרופיל גובה</div>
            <div className={styles.infoText}>
              לכל נקודה על הציר המערכת מורידה DEM מ-<b>Terrarium RGB</b>,
              מדגמת <b>24 נקודות ביניים</b> בין המשדר לנקודה, ומחשבת את
              המחסום הגבוה ביותר על הנתיב לפי ITU-R P.526.
            </div>
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>🎨 צבעי הקו במפה</div>
            {[
              { label: "מצוין",  value: "> −70",   color: "#00ff88" },
              { label: "טוב",    value: "−70/−80", color: "#7dff6b" },
              { label: "בינוני", value: "−80/−90", color: "#ffe600" },
              { label: "חלש",    value: "−90/−100",color: "#ff8c00" },
              { label: "שולי",   value: "−100/−110",color: "#ff3b5c"},
              { label: "אין",    value: "< −110",  color: "#555"   },
            ].map(row => (
              <div key={row.label} className={styles.infoLegendRow}>
                <div style={{ width: 22, height: 4, borderRadius: 2, background: row.color, flexShrink: 0, marginTop: 6 }} />
                <div>
                  <span style={{ color: "var(--text)", fontSize: 11 }}><b>{row.label}</b></span>
                  <span style={{ color: "var(--text-dim)", fontSize: 10, marginRight: 6 }}>{row.value} dBm</span>
                </div>
              </div>
            ))}
          </div>

          <div className={styles.infoSection}>
            <div className={styles.infoSectionTitle}>⚠ רמות סיכון</div>
            <div className={styles.infoText}>
              <b style={{ color: "#00aa44" }}>LOW</b> — כיסוי תקין (≥80%, RSSI ≥ −105)<br />
              <b style={{ color: "#ffa500" }}>MEDIUM</b> — גבולי, מומלץ לבדוק<br />
              <b style={{ color: "#ff4444" }}>HIGH</b> — בעייתי, נדרשת פעולה<br />
              <b style={{ color: "#cc0000" }}>CRITICAL</b> — לא מתאים לתקשורת ללא ממסר
            </div>
          </div>
        </div>
      )}

      {/* Step 1 – Site */}
      <div className={`${styles.stepCard} ${step1Done ? styles.stepDone : ""}`}>
        <div className={styles.stepTitle}>
          1. בחירת אתר קשר
          {step1Done
            ? <span className={`${styles.badge} ${styles.badgeDone}`}>✔ {sites.find(s => s.id === siteId)?.name}</span>
            : <span className={styles.badge}>ממתין</span>}
        </div>
        <select
          className={styles.select}
          value={siteId ?? ""}
          onChange={e => setSiteId(e.target.value ? +e.target.value : null)}
        >
          <option value="">-- בחר אתר --</option>
          {sites.map(s => (
            <option key={s.id} value={s.id}>{s.name} ({s.frequency} MHz)</option>
          ))}
        </select>
      </div>

      {/* Step 2 – Mode */}
      <div className={styles.stepCard}>
        <div className={styles.stepTitle}>
          2. מודל חישוב
          <InfoTooltip text={TIPS.dtm_dsm} />
        </div>
        <div className={styles.modeToggle}>
          <button
            className={`${styles.modeBtn} ${analysisMode === "DTM" ? styles.active : ""}`}
            onClick={() => setAnalysisMode("DTM")}
          >📏 DTM</button>
          <button
            className={`${styles.modeBtn} ${analysisMode === "DSM" ? styles.active : ""}`}
            onClick={() => setAnalysisMode("DSM")}
          >🏢 DSM</button>
        </div>
      </div>

      {/* Step 3 – Route */}
      <div className={`${styles.stepCard} ${step2Done ? styles.stepDone : ""}`}>
        <div className={styles.stepTitle}>
          3. הגדרת מסלול על המפה
          {step2Done
            ? <span className={`${styles.badge} ${styles.badgeDone}`}>✔ {routeWaypoints.length} נקודות</span>
            : <span className={styles.badge}>ממתין</span>}
        </div>

        {!step2Done ? (
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 10, lineHeight: 1.4 }}>
            לחץ "הוסף נקודות" ולאחר מכן לחץ על המפה לסימון ציר התנועה.
          </div>
        ) : null}

        <button
          className={`${styles.btn} ${inWaypointMode ? styles.active : ""}`}
          onClick={toggleWaypointMode}
        >
          {inWaypointMode ? "🛑 סיים הוספת נקודות" : "📍 הוסף נקודות מסלול"}
        </button>

        {routeWaypoints.length > 0 && (
          <div className={styles.waypointList}>
            {routeWaypoints.map((wp, i) => (
              <div key={i} className={styles.waypointRow}>
                <span className={styles.wpNum}>{i + 1}</span>
                <span>{wp.lat.toFixed(4)}°N, {wp.lon.toFixed(4)}°E</span>
              </div>
            ))}
          </div>
        )}

        <div className={styles.routeBtns}>
          {routeWaypoints.length > 0 && (
            <button className={`${styles.btn} ${styles.small}`} onClick={removeLastWaypoint}>
              ↩ הסר אחרונה
            </button>
          )}
          {routeWaypoints.length > 0 && (
            <button className={`${styles.btn} ${styles.small} ${styles.danger}`} onClick={clearRoute}>
              ✕ נקה מסלול
            </button>
          )}
        </div>
      </div>

      {/* Step 4 – Run */}
      <div className={`${styles.stepCard} ${readyToRun ? styles.stepDone : ""}`}>
        <div className={styles.stepTitle}>4. הרץ חתך רדיו</div>
        {writable ? (
          <button
            className={styles.runBtn}
            onClick={runAnalysis}
            disabled={!readyToRun || loading}
          >
            {loading ? "⏳ מחשב חתך..." : "▶ הרץ חתך רדיו"}
          </button>
        ) : (
          <div style={{ fontSize: 11, color: "var(--text-dim)" }}>{READ_ONLY_NOTE}</div>
        )}
        {result && writable && (
          <button
            className={`${styles.btn} ${styles.exportBtn}`}
            onClick={downloadDocx}
            disabled={exporting}
          >
            {exporting ? "מייצא..." : "📄 הורד דוח Word (.docx)"}
          </button>
        )}
      </div>

      {/* Results */}
      {result && (
        <div className={styles.results}>
          <div className={styles.header}>
            תוצאות חתך
            {demStatus(result.dem_source) !== "unknown" && (
              <span
                className={`${styles.demBadge} ${demStatus(result.dem_source) === "simulated" ? styles.demSim : styles.demReal}`}
                title={result.dem_valid_pct != null ? `${fmtPct(result.dem_valid_pct, 0)} פיקסלי DEM תקינים` : undefined}
              >
                {demStatus(result.dem_source) === "simulated" ? "⚠ " : ""}{demLabel(result.dem_source)}
              </span>
            )}
          </div>

          {(() => {
            const warnings = collectWarnings(result.warnings, result.dem_source);
            return warnings.length > 0 ? (
              <div className={styles.warnBox}>
                {warnings.map((w, i) => (
                  <div key={i} className={styles.warnLine}><span>⚠</span><span>{w}</span></div>
                ))}
              </div>
            ) : null;
          })()}

          {/* Stats */}
          <div className={styles.statRow}>
            <div className={styles.statBox}>
              <div className={styles.statVal} style={{ fontSize: 14, direction: "ltr" }}>
                {fmtCoveredVsThreshold(result.covered_pct, result.threshold_dbm, { legacyLabel: false })}
              </div>
              <div className={styles.statLbl}>כיסוי ציר <InfoTooltip text={TIPS.covered} /></div>
              <div className={styles.statLbl} title="אחוז הציר עם אות כלשהו (> −110 dBm)">
                אות כלשהו: {fmtPct(result.covered_pct_any_signal)}
              </div>
            </div>
            <div className={styles.statBox}>
              <div className={styles.statVal}>{result.dead_zones.length}</div>
              <div className={styles.statLbl}>אזורי מתים <InfoTooltip text={TIPS.dead_zones} /></div>
            </div>
            <div className={styles.statBox}>
              <div className={styles.statVal}>{result.rssi_min}</div>
              <div className={styles.statLbl}>RSSI מינ' <InfoTooltip text={TIPS.rssi_min} /></div>
            </div>
            <div
              className={styles.statBox}
              style={{ background: RISK_COLORS[result.risk_level] + "33",
                       borderColor: RISK_COLORS[result.risk_level] }}
            >
              <div className={styles.statVal} style={{ color: RISK_COLORS[result.risk_level] }}>
                {RISK_HE[result.risk_level] ?? result.risk_level}
              </div>
              <div className={styles.statLbl}>סיכון <InfoTooltip text={TIPS.risk} /></div>
            </div>
          </div>

          <div className={styles.riskScoreRow}>
            <span>ציון סיכון <InfoTooltip text={TIPS.risk_score} /></span>
            <div className={styles.distBarBg}>
              {result.risk_score != null && (
                <div className={styles.distBar} style={{
                  width: `${Math.max(0, Math.min(100, (result.risk_score / 4) * 100))}%`,
                  background: RISK_COLORS[result.risk_level] ?? "var(--accent)",
                }} />
              )}
            </div>
            <strong style={{ color: RISK_COLORS[result.risk_level] ?? "var(--text)" }}>
              {result.risk_score != null ? `${fmtNum(result.risk_score, 0)} / 4` : fmtNum(result.risk_score, 0)}
            </strong>
          </div>

          {/* Legend – color guide */}
          <div className={styles.subHeader}>מקרא צבעי הקו במפה</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
            {["excellent","good","medium","weak","marginal","none"].map(lvl => (
              <span key={lvl} style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                fontSize: 10, color: "var(--text-dim)",
              }}>
                <span style={{ display: "inline-block", width: 18, height: 4,
                  borderRadius: 2, background: LEVEL_COLORS[lvl] }} />
                {LEVEL_HE[lvl]}
              </span>
            ))}
          </div>

          {/* Dead zones */}
          {result.dead_zones.length > 0 && (
            <>
              <div className={styles.subHeader}>אזורי מתים</div>
              {result.dead_zones.map((dz, i) => (
                <div key={i} className={styles.deadZone}>
                  <span className={styles.dzNum}>#{i + 1}</span>
                  <span>{dz.start_km.toFixed(2)} – {dz.end_km.toFixed(2)} ק"מ</span>
                  <span className={styles.dzLen}>{dz.length_km.toFixed(3)} ק"מ</span>
                </div>
              ))}
            </>
          )}

          {/* Signal distribution */}
          <div className={styles.subHeader}>התפלגות אות</div>
          <div className={styles.distBars}>
            {["excellent","good","medium","weak","marginal","none"].map(lvl => {
              const d = result.signal_distribution?.[lvl];
              if (!d?.count) return null;
              return (
                <div key={lvl} className={styles.distRow}>
                  <span className={styles.distLabel}>{LEVEL_HE[lvl]}</span>
                  <div className={styles.distBarBg}>
                    <div className={styles.distBar}
                      style={{ width: `${d.pct}%`, background: LEVEL_COLORS[lvl] }} />
                  </div>
                  <span className={styles.distPct}>{d.pct}%</span>
                </div>
              );
            })}
          </div>

          {/* Recommendations */}
          <div className={styles.subHeader}>המלצות מבצעיות</div>
          {(result.recommendations ?? []).map((r, i) => (
            <div key={i} className={styles.rec}>
              <span className={styles.recNum}>{i + 1}</span>
              <span>{r}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
