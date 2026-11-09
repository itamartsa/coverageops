/**
 * 🎯 קישור נקודתי — point-to-point link budget (transmitter → device) and
 * "מי משרת את המכשיר" (best civil cell sites for a receiver point).
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import api, {
  apiErrorText, apiErrorTextAsync, cellSitesApi, deviceProfilesApi, linkApi,
} from "../services/api";
import type {
  BestServerEntry, BestServersOperator, BestServersResult, CellSiteDetail, LinkReceiverIn, LinkResult, LinkSummary,
} from "../services/api";
import { useStore } from "../store/useStore";
import type { LinkTransmitterSel } from "../store/useStore";
import { CELL_OPERATORS, cellSiteBands, isManualFrequency, isManualHeight, isManualTxPower } from "../lib/cellSites";
import {
  BEST_SERVER_OPERATORS, estimatedFieldHe, fmtDb, fmtDeg, fmtKm, levelColor, levelHe,
} from "../lib/link";
import {
  DASH, collectWarnings, demLabel, demStatus, fmtDbm, fmtNum, fmtPct,
} from "../services/coverageFormat";
import { notify } from "./Notification";
import InfoTooltip from "./InfoTooltip";
import LinkProfileChart from "./LinkProfileChart";
import cs from "./CrossSectionPanel.module.css";
import styles from "./LinkPanel.module.css";

const TIPS = {
  margin:  "מרווח הקישור = RSSI − רגישות המקלט.\nמעל 10 dB = קישור יציב; 0–10 dB = גבולי; מתחת ל-0 = אין קישור צפוי.",
  fresnel: "אחוז הפינוי של אזור פרנל הראשון במכשול הקריטי.\n60% ומעלה ≈ התפשטות כמו במרחב חופשי.\nמתחת ל-0% = המכשול חוסם את קו הראייה.",
  dtm_dsm: "DTM = טופוגרפיה בלבד (שטח פתוח).\nDSM = כולל הפסד תכסית (מבנים/צמחייה) — מתאים לעירוני.",
  overrides: "ערכים שיוזנו כאן גוברים על ערכי פרופיל המכשיר עבור חישוב זה בלבד.",
  band: "\"הנמוך\" = התדר הנמוך מבין הדורות שבאתר (טווח ארוך יותר).\n\"הגבוה\" = התדר הגבוה (מקרה גרוע).",
};

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const fmtDate = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("he-IL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : DASH;

function cellSiteName(d: Pick<CellSiteDetail, "operator" | "operator_he" | "city" | "address" | "site_code">) {
  const where = d.city || d.address || "";
  return `${d.operator_he ?? d.operator}${where ? ` – ${where}` : ""}${d.site_code ? ` (${d.site_code})` : ""}`;
}

const OpBadge = ({ op, label }: { op: string; label?: string | null }) => {
  const m = CELL_OPERATORS[op] ?? CELL_OPERATORS.OTHER;
  return <span className={styles.opBadge} style={{ borderColor: m.color, color: m.color }}>{label || m.label}</span>;
};

const EstBadge = ({ manual, title }: { manual: boolean; title?: string }) =>
  manual
    ? <span className={styles.manTag} title={title}>✓ ידני</span>
    : <span className={styles.estTag} title={title ?? "ערך משוער — אינו חלק מהמאגר הממשלתי"}>⚠ משוער</span>;

// ─────────────────────────────────────────────────────────────────────────────

export default function LinkPanel() {
  const {
    sites, analysisMode, setAnalysisMode, drawMode, setDrawMode,
    linkSubMode, setLinkSubMode,
    linkTransmitter, setLinkTransmitter,
    linkReceiver, setLinkReceiver,
    linkReceiverOpts, setLinkReceiverOpts,
    linkResult, setLinkResult,
    bestServersResult, setBestServersResult,
    highlightedServerId, setHighlightedServerId,
    linkPickTransmitter, setLinkPickTransmitter,
  } = useStore();
  const qc = useQueryClient();

  const [infoOpen, setInfoOpen] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState<number | null>(null);
  const [rxElev, setRxElev] = useState<number | null>(null);
  const [cellDetail, setCellDetail] = useState<CellSiteDetail | null>(null);
  const [cellErr, setCellErr] = useState(false);
  const [confirmDel, setConfirmDel] = useState<number | null>(null);
  // best-servers parameters
  const [radiusKm, setRadiusKm] = useState(10);
  const [operator, setOperator] = useState<"" | BestServersOperator>("");
  const [topN, setTopN] = useState(5);
  const [bandPolicy, setBandPolicy] = useState<"lowest" | "highest">("lowest");

  const profilesQ = useQuery({
    queryKey: ["device-profiles"], queryFn: deviceProfilesApi.list,
    staleTime: 60_000, refetchOnWindowFocus: false,
  });
  const historyQ = useQuery({
    queryKey: ["link-history"], queryFn: linkApi.list, refetchOnWindowFocus: false, retry: false,
  });
  const profiles = profilesQ.data ?? [];
  const profile = profiles.find((p) => p.id === linkReceiverOpts.device_profile_id) ?? null;

  // Default device profile once the list arrives (or if the chosen one disappeared)
  useEffect(() => {
    if (!profiles.length) return;
    if (linkReceiverOpts.device_profile_id != null && profiles.some((p) => p.id === linkReceiverOpts.device_profile_id)) return;
    const def = profiles.find((p) => p.is_default) ?? profiles[0];
    setLinkReceiverOpts({ device_profile_id: def.id });
  }, [profiles, linkReceiverOpts.device_profile_id, setLinkReceiverOpts]);

  // Initial transmitter kind: my sites if I have any, otherwise civil cell sites
  useEffect(() => {
    if (!linkTransmitter) setLinkTransmitter({ kind: sites.length ? "site" : "cell_site", id: null });
  }, [linkTransmitter, sites.length, setLinkTransmitter]);

  // Cell-site transmitter details (name / type / estimated badges / bands)
  const cellId = linkTransmitter?.kind === "cell_site" ? linkTransmitter.id : null;
  useEffect(() => {
    setCellErr(false);
    if (cellId == null) { setCellDetail(null); return; }
    let alive = true;
    cellSitesApi.get(cellId)
      .then((d) => { if (alive) setCellDetail(d); })
      .catch(() => { if (alive) { setCellDetail(null); setCellErr(true); } });
    return () => { alive = false; };
  }, [cellId]);

  // Receiver ground elevation (same endpoint as the map's cursor read-out)
  useEffect(() => {
    setRxElev(null);
    if (!linkReceiver) return;
    const t = setTimeout(async () => {
      try {
        const { data } = await api.get<{ elevation_m: number }>(
          "/api/coverage/point-elevation", { params: { lat: linkReceiver.lat, lon: linkReceiver.lon } });
        if (isNum(data?.elevation_m)) setRxElev(data.elevation_m);
      } catch { /* ignore */ }
    }, 300);
    return () => clearTimeout(t);
  }, [linkReceiver]);

  // Leave map-pick modes when the panel unmounts (tab change)
  useEffect(() => () => {
    const st = useStore.getState();
    if (st.drawMode === "linkpoint") st.setDrawMode(null);
    if (st.linkPickTransmitter) st.setLinkPickTransmitter(false);
  }, []);

  const txKind = linkTransmitter?.kind ?? "site";
  const mySite = txKind === "site" ? sites.find((s) => s.id === linkTransmitter?.id) ?? null : null;
  const bands = cellDetail ? cellSiteBands(cellDetail) : [];
  // Band shown in the select: explicit pick, else the site's current (estimated/manual) frequency
  const shownBand = linkTransmitter?.frequency_mhz ?? cellDetail?.frequency_mhz ?? bands[0] ?? null;
  const bandOptions = shownBand != null && !bands.includes(shownBand)
    ? [...bands, shownBand].sort((a, b) => a - b) : bands;

  function setKind(kind: "site" | "cell_site") {
    if (linkTransmitter?.kind === kind) return;
    setLinkTransmitter({ kind, id: null });
    if (kind === "site") setLinkPickTransmitter(false);
  }

  function togglePickTx() {
    if (linkPickTransmitter) { setLinkPickTransmitter(false); return; }
    setDrawMode(null);
    setLinkPickTransmitter(true);
    notify("לחץ על אתר סלולרי במפה לבחירתו כמשדר");
  }

  function togglePickRx() {
    if (drawMode === "linkpoint") { setDrawMode(null); return; }
    setLinkPickTransmitter(false);
    setDrawMode("linkpoint");
    notify("לחץ על המפה לבחירת מיקום המקלט");
  }

  /** Receiver payload, or an error message. */
  function buildReceiver(): LinkReceiverIn | string {
    if (!linkReceiver) return "בחר מיקום מקלט במפה";
    const o = linkReceiverOpts;
    const num = (s: string) => (s.trim() === "" ? undefined : Number(s));
    const h = num(o.height_agl), sens = num(o.sensitivity_dbm), gain = num(o.antenna_gain_dbi);
    if (h !== undefined && (!isNum(h) || h < 0.1 || h > 300)) return "גובה מקלט חייב להיות בין 0.1 ל-300 מ'";
    if (sens !== undefined && (!isNum(sens) || sens < -150 || sens > -30)) return "רגישות חייבת להיות בין −150 ל-−30 dBm";
    if (gain !== undefined && (!isNum(gain) || gain < -20 || gain > 40)) return "רווח אנטנה חייב להיות בין −20 ל-40 dBi";
    return {
      lat: linkReceiver.lat, lon: linkReceiver.lon,
      device_profile_id: o.device_profile_id ?? undefined,
      height_agl: h, sensitivity_dbm: sens, antenna_gain_dbi: gain,
      name: o.name.trim() || undefined,
    };
  }

  async function runLink(txOverride?: LinkTransmitterSel) {
    const tx = txOverride ?? linkTransmitter;
    if (!tx || tx.id == null) { notify("⚠ בחר משדר"); return; }
    const rx = buildReceiver();
    if (typeof rx === "string") { notify(`⚠ ${rx}`); return; }
    setLoading(true);
    setDrawMode(null);
    setLinkPickTransmitter(false);
    try {
      const r = await linkApi.run({
        transmitter: {
          kind: tx.kind, id: tx.id,
          ...(tx.kind === "cell_site" && isNum(tx.frequency_mhz) ? { frequency_mhz: tx.frequency_mhz } : {}),
        },
        receiver: rx, mode: analysisMode, save: true,
      });
      setLinkResult(r);
      qc.invalidateQueries({ queryKey: ["link-history"] });
      notify(`${r.margin_db >= 0 ? "✅" : "⚠"} קישור: ${fmtDbm(r.rssi_dbm, 1)} · מרווח ${fmtDb(r.margin_db)} · ${levelHe(r.level)}`);
    } catch (e) {
      notify(`⚠ ${apiErrorText(e, "שגיאה בחישוב הקישור")}`);
    } finally {
      setLoading(false);
    }
  }

  async function runBest() {
    const rx = buildReceiver();
    if (typeof rx === "string") { notify(`⚠ ${rx}`); return; }
    setLoading(true);
    setDrawMode(null);
    setLinkPickTransmitter(false);
    try {
      const r = await linkApi.bestServers({
        receiver: rx, mode: analysisMode, radius_km: radiusKm,
        ...(operator ? { operator } : {}),
        top_n: topN, band_policy: bandPolicy,
      });
      setBestServersResult(r);
      notify(r.results?.length
        ? `✅ נמצאו ${r.results.length} אתרים משרתים (נבדקו ${r.candidates_evaluated ?? "—"})`
        : `⚠ לא נמצאו אתרים ברדיוס ${radiusKm} ק"מ`);
    } catch (e) {
      notify(`⚠ ${apiErrorText(e, "שגיאה בחיפוש אתרים משרתים")}`);
    } finally {
      setLoading(false);
    }
  }

  function analyseServer(e: BestServerEntry) {
    // No explicit band → the backend's default / estimated frequency applies
    const tx: LinkTransmitterSel = { kind: "cell_site", id: e.cell_site.id, frequency_mhz: null };
    setLinkTransmitter(tx);
    setLinkSubMode("link");
    void runLink(tx);
  }

  async function loadHistory(h: LinkSummary) {
    try {
      const r = await linkApi.get(h.id);
      setLinkResult(r);
      if (isNum(r.receiver?.lat) && isNum(r.receiver?.lon)) setLinkReceiver({ lat: r.receiver.lat, lon: r.receiver.lon });
      if (r.transmitter?.id != null) {
        setLinkTransmitter({ kind: r.transmitter.kind, id: r.transmitter.id, frequency_mhz: null });
      }
      if (r.mode === "DTM" || r.mode === "DSM") setAnalysisMode(r.mode);
      setLinkSubMode("link");
    } catch (e) {
      notify(`⚠ ${apiErrorText(e, "טעינת הניתוח נכשלה")}`);
    }
  }

  async function deleteHistory(id: number) {
    try {
      await linkApi.remove(id);
      if (linkResult?.id === id) setLinkResult({ ...linkResult, id: null });
      qc.invalidateQueries({ queryKey: ["link-history"] });
      notify("ניתוח נמחק");
    } catch (e) {
      notify(`⚠ ${apiErrorText(e, "המחיקה נכשלה")}`);
    } finally {
      setConfirmDel(null);
    }
  }

  async function downloadDocx(id: number) {
    setExporting(id);
    try {
      await linkApi.downloadDocx(id);
      notify("✅ הדוח הורד בהצלחה");
    } catch (e) {
      notify(`⚠ ${await apiErrorTextAsync(e, "שגיאה בייצוא הדוח")}`);
    } finally {
      setExporting(null);
    }
  }

  function clearResults() {
    setLinkResult(null);
    setBestServersResult(null);
  }

  const rxStale = useMemo(() => {
    const r = linkSubMode === "link" ? linkResult?.receiver : bestServersResult?.receiver;
    if (!r || !linkReceiver) return false;
    return Math.abs(r.lat - linkReceiver.lat) > 1e-6 || Math.abs(r.lon - linkReceiver.lon) > 1e-6;
  }, [linkSubMode, linkResult, bestServersResult, linkReceiver]);

  const txReady = linkTransmitter?.id != null;
  const rxReady = !!linkReceiver;
  const history = (historyQ.data ?? []).slice(0, 12);

  return (
    <div>
      <div className={cs.header}>
        🎯 קישור נקודתי
        <button className={cs.infoBtn} onClick={() => setInfoOpen((v) => !v)} title="מידע על החישוב">ⓘ</button>
      </div>

      {infoOpen && (
        <div className={cs.infoPanel}>
          <div className={cs.infoPanelTitle}>
            <span>מה מחושב כאן?</span>
            <button className={cs.infoPanelClose} onClick={() => setInfoOpen(false)}>✕</button>
          </div>
          <div className={cs.infoSection}>
            <div className={cs.infoSectionTitle}>🎯 קישור</div>
            <div className={cs.infoText}>
              תקציב קישור בין משדר (אתר שלך או אתר סלולרי אזרחי) לבין מכשיר בנקודה אחת:
              <span className={cs.formula}>RSSI = Pₜ − FSPL − Ldiff − Lclutter + Gh + Gᵣₓ</span>
              <b>מרווח</b> = RSSI − רגישות המכשיר. הפרופיל מציג את הטרן, קו הראייה ואזור פרנל הראשון.
            </div>
          </div>
          <div className={cs.infoSection}>
            <div className={cs.infoSectionTitle}>📶 מי משרת את המכשיר</div>
            <div className={cs.infoText}>
              מחשב קישור מכל האתרים הסלולריים האזרחיים ברדיוס שנבחר ומדרג לפי עוצמת האות.
              לאתרים אזרחיים <b>גובה, הספק ותדר משוערים</b> (אינם במאגר הממשלתי) — יש להתייחס לתוצאה כהערכה.
            </div>
          </div>
        </div>
      )}

      {/* Sub-mode */}
      <div className={`${cs.modeToggle} ${styles.subMode}`}>
        <button className={`${cs.modeBtn} ${linkSubMode === "link" ? cs.active : ""}`} onClick={() => setLinkSubMode("link")}>
          🎯 קישור
        </button>
        <button className={`${cs.modeBtn} ${linkSubMode === "best" ? cs.active : ""}`} onClick={() => setLinkSubMode("best")}>
          📶 מי משרת את המכשיר
        </button>
      </div>

      {/* ── Transmitter ─────────────────────────────────────────────────── */}
      {linkSubMode === "link" && (
        <div className={`${cs.stepCard} ${txReady ? cs.stepDone : ""}`}>
          <div className={cs.stepTitle}>
            1. משדר
            {txReady ? <span className={`${cs.badge} ${cs.badgeDone}`}>✔ נבחר</span> : <span className={cs.badge}>ממתין</span>}
          </div>
          <div className={styles.radioRow}>
            <label className={styles.radio}>
              <input type="radio" checked={txKind === "site"} onChange={() => setKind("site")} /> האתרים שלי
            </label>
            <label className={styles.radio}>
              <input type="radio" checked={txKind === "cell_site"} onChange={() => setKind("cell_site")} /> אתר סלולרי אזרחי
            </label>
          </div>

          {txKind === "site" ? (
            sites.length === 0 ? (
              <div className={styles.hint}>אין אתרים מוגדרים — הקם אתר בלשונית "הקמת אתר" או בחר אתר סלולרי אזרחי.</div>
            ) : (
              <>
                <select className={cs.select} value={linkTransmitter?.id ?? ""}
                        onChange={(e) => setLinkTransmitter({ kind: "site", id: e.target.value ? +e.target.value : null })}>
                  <option value="">-- בחר אתר --</option>
                  {sites.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.frequency} MHz)</option>)}
                </select>
                {mySite && (
                  <div className={styles.txInfo}>
                    <div>{mySite.frequency} MHz · {mySite.tx_power} dBm · אנטנה {mySite.ant_height} מ'
                      {mySite.params_source === "estimated" && (
                        <> <EstBadge manual={false} title={mySite.source_note ?? undefined} /></>
                      )}
                    </div>
                  </div>
                )}
              </>
            )
          ) : (
            <>
              {cellId != null && cellDetail && (
                <div className={styles.txInfo}>
                  <div className={styles.txName}>
                    <OpBadge op={cellDetail.operator} label={cellDetail.operator_he} />
                    <span>{cellSiteName(cellDetail)}</span>
                  </div>
                  <div className={styles.txMeta}>{cellDetail.site_type_he ?? cellDetail.site_type ?? DASH}
                    {cellDetail.technologies && <> · {cellDetail.technologies.split(",").join(" · ")}</>}
                  </div>
                  <div className={styles.txMeta}>
                    גובה {fmtNum(cellDetail.ant_height_m, 1)} מ' <EstBadge manual={isManualHeight(cellDetail)} />
                    {" · "}הספק {fmtNum(cellDetail.tx_power_dbm, 1)} dBm <EstBadge manual={isManualTxPower(cellDetail)} />
                  </div>
                  {bandOptions.length > 1 ? (
                    <label className={styles.inlineField}>
                      תדר:
                      <select className={styles.smallSelect}
                              value={shownBand ?? ""}
                              onChange={(e) => setLinkTransmitter({ kind: "cell_site", id: cellId, frequency_mhz: +e.target.value })}>
                        {bandOptions.map((b) => (
                          <option key={b} value={b}>
                            {b} MHz{b === bands[0] ? " (הנמוך)" : ""}{!bands.includes(b) ? " (נוכחי)" : ""}
                          </option>
                        ))}
                      </select>
                      {linkTransmitter?.frequency_mhz == null
                        ? <EstBadge manual={isManualFrequency(cellDetail)} />
                        : <span className={styles.manTag} title="נבחר ידנית לחישוב זה">✓ נבחר</span>}
                    </label>
                  ) : (
                    <div className={styles.txMeta}>תדר: {shownBand ?? DASH} MHz <EstBadge manual={isManualFrequency(cellDetail)} /></div>
                  )}
                </div>
              )}
              {cellId != null && !cellDetail && (
                <div className={styles.hint}>{cellErr ? "⚠ לא ניתן לטעון את פרטי האתר" : "טוען פרטי אתר…"}</div>
              )}
              {cellId == null && (
                <div className={styles.hint}>בחר אתר במפה (שכבת אתרי הסלולר), או מחלון אתר: "🎯 קישור למכשיר מאתר זה".</div>
              )}
              <div className={cs.routeBtns}>
                <button className={`${cs.btn} ${cs.small} ${linkPickTransmitter ? cs.active : ""}`} onClick={togglePickTx}>
                  {linkPickTransmitter ? "🛑 בטל בחירה במפה" : "🗺 בחר במפה"}
                </button>
                {cellId != null && (
                  <button className={`${cs.btn} ${cs.small} ${cs.danger}`}
                          onClick={() => setLinkTransmitter({ kind: "cell_site", id: null })}>✕ נקה</button>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* ── Receiver ────────────────────────────────────────────────────── */}
      <div className={`${cs.stepCard} ${rxReady ? cs.stepDone : ""}`}>
        <div className={cs.stepTitle}>
          {linkSubMode === "link" ? "2." : "1."} מקלט (מכשיר)
          {rxReady ? <span className={`${cs.badge} ${cs.badgeDone}`}>✔ מוקם</span> : <span className={cs.badge}>ממתין</span>}
        </div>

        <label className={styles.fieldLabel}>פרופיל מכשיר</label>
        <select className={cs.select} value={linkReceiverOpts.device_profile_id ?? ""}
                disabled={!profiles.length}
                onChange={(e) => setLinkReceiverOpts({ device_profile_id: e.target.value ? +e.target.value : null })}>
          {!profiles.length && <option value="">{profilesQ.isError ? "⚠ שגיאה בטעינת פרופילים" : "טוען…"}</option>}
          {profiles.map((p) => <option key={p.id} value={p.id}>{p.name_he}{p.is_default ? " (ברירת מחדל)" : ""}</option>)}
        </select>
        {profile && (
          <div className={styles.txMeta} title={profile.description_he ?? undefined}>
            גובה {fmtNum(profile.rx_height_m, 1)} מ' · רגישות {fmtDbm(profile.rx_sensitivity_dbm)} · רווח {fmtNum(profile.antenna_gain_dbi, 1)} dBi
          </div>
        )}

        <button className={styles.more} onClick={() => setAdvanced((v) => !v)}>
          {advanced ? "▲ מתקדם" : "▼ מתקדם"}
          {(linkReceiverOpts.height_agl || linkReceiverOpts.sensitivity_dbm || linkReceiverOpts.antenna_gain_dbi) && !advanced && (
            <span className={styles.overrideDot} title="הוגדרו ערכים עוקפים">●</span>
          )}
        </button>
        {advanced && (
          <div className={styles.advanced}>
            <div className={styles.hint}>ערכים עוקפים (ריק = לפי הפרופיל) <InfoTooltip text={TIPS.overrides} /></div>
            <div className={styles.grid3}>
              <label>גובה (מ' AGL)
                <input type="number" step="0.5" min="0.1" max="300" placeholder={profile ? String(profile.rx_height_m) : ""}
                       value={linkReceiverOpts.height_agl} onChange={(e) => setLinkReceiverOpts({ height_agl: e.target.value })} />
              </label>
              <label>רגישות (dBm)
                <input type="number" step="1" min="-150" max="-30" placeholder={profile ? String(profile.rx_sensitivity_dbm) : ""}
                       value={linkReceiverOpts.sensitivity_dbm} onChange={(e) => setLinkReceiverOpts({ sensitivity_dbm: e.target.value })} />
              </label>
              <label>רווח (dBi)
                <input type="number" step="0.5" min="-20" max="40" placeholder={profile ? String(profile.antenna_gain_dbi) : ""}
                       value={linkReceiverOpts.antenna_gain_dbi} onChange={(e) => setLinkReceiverOpts({ antenna_gain_dbi: e.target.value })} />
              </label>
            </div>
          </div>
        )}

        <label className={styles.fieldLabel}>שם הנקודה (רשות)</label>
        <input className={styles.input} value={linkReceiverOpts.name} maxLength={64} placeholder="לדוגמה: עמדה 3"
               onChange={(e) => setLinkReceiverOpts({ name: e.target.value })} />

        <button className={`${cs.btn} ${drawMode === "linkpoint" ? cs.active : ""}`} style={{ marginTop: 8 }} onClick={togglePickRx}>
          {drawMode === "linkpoint" ? "🛑 בטל בחירת מיקום" : linkReceiver ? "📍 בחר מיקום אחר במפה" : "📍 בחר מיקום במפה"}
        </button>
        {linkReceiver && (
          <div className={styles.coords}>
            <span>{linkReceiver.lat.toFixed(5)}°N, {linkReceiver.lon.toFixed(5)}°E</span>
            <span>{rxElev != null ? `${Math.round(rxElev)} מ' ASL` : DASH}</span>
            <button className={styles.linkBtn} onClick={() => setLinkReceiver(null)} title="הסר את המקלט">✕</button>
          </div>
        )}
        {linkReceiver && <div className={styles.hintSmall}>ניתן לגרור את הסמן 📱 במפה לכוונון</div>}
      </div>

      {/* ── Mode + run ──────────────────────────────────────────────────── */}
      <div className={`${cs.stepCard} ${(linkSubMode === "link" ? txReady && rxReady : rxReady) ? cs.stepDone : ""}`}>
        <div className={cs.stepTitle}>
          {linkSubMode === "link" ? "3. חישוב" : "2. חיפוש"}
          <InfoTooltip text={TIPS.dtm_dsm} />
        </div>
        <div className={cs.modeToggle} style={{ marginBottom: 8 }}>
          <button className={`${cs.modeBtn} ${analysisMode === "DTM" ? cs.active : ""}`} onClick={() => setAnalysisMode("DTM")}>📏 DTM</button>
          <button className={`${cs.modeBtn} ${analysisMode === "DSM" ? cs.active : ""}`} onClick={() => setAnalysisMode("DSM")}>🏢 DSM</button>
        </div>

        {linkSubMode === "best" && (
          <div className={styles.bestParams}>
            <label className={styles.sliderRow}>
              <span>רדיוס חיפוש</span>
              <input type="range" min={1} max={30} step={1} value={radiusKm} onChange={(e) => setRadiusKm(+e.target.value)} />
              <b>{radiusKm} ק"מ</b>
            </label>
            <div className={styles.grid2}>
              <label>מפעיל
                <select value={operator} onChange={(e) => setOperator(e.target.value as "" | BestServersOperator)}>
                  {BEST_SERVER_OPERATORS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
              </label>
              <label>מספר תוצאות
                <input type="number" min={1} max={10} value={topN}
                       onChange={(e) => setTopN(Math.max(1, Math.min(10, Math.round(+e.target.value || 5))))} />
              </label>
            </div>
            <label className={styles.inlineField}>
              תדר לאתר רב-דורי <InfoTooltip text={TIPS.band} />
              <select className={styles.smallSelect} value={bandPolicy} onChange={(e) => setBandPolicy(e.target.value as "lowest" | "highest")}>
                <option value="lowest">הנמוך</option>
                <option value="highest">הגבוה</option>
              </select>
            </label>
          </div>
        )}

        {linkSubMode === "link" ? (
          <button className={cs.runBtn} onClick={() => runLink()} disabled={!txReady || !rxReady || loading}>
            {loading ? "⏳ מחשב קישור..." : "▶ חשב קישור"}
          </button>
        ) : (
          <button className={cs.runBtn} onClick={runBest} disabled={!rxReady || loading}>
            {loading ? "⏳ מחפש..." : "▶ מצא אתרים משרתים"}
          </button>
        )}
        {rxStale && <div className={styles.stale}>⚠ המקלט הוזז מאז החישוב — יש לחשב מחדש</div>}
        {(linkResult || bestServersResult) && (
          <button className={`${cs.btn} ${cs.small} ${cs.danger}`} onClick={clearResults}>✕ נקה תוצאות מהמפה</button>
        )}
      </div>

      {/* ── Results ─────────────────────────────────────────────────────── */}
      {linkSubMode === "link" && linkResult && (
        <LinkResultCard result={linkResult} exporting={exporting} onDocx={downloadDocx} />
      )}

      {linkSubMode === "best" && bestServersResult && (
        <BestServersList
          result={bestServersResult}
          highlighted={highlightedServerId}
          onHighlight={(id) => setHighlightedServerId(highlightedServerId === id ? null : id)}
          onAnalyse={analyseServer}
          busy={loading}
        />
      )}

      {/* ── History ─────────────────────────────────────────────────────── */}
      <div className={cs.subHeader}>ניתוחים אחרונים</div>
      {historyQ.isLoading && <div className={styles.hint}>טוען…</div>}
      {historyQ.isError && <div className={styles.hint}>⚠ לא ניתן לטעון היסטוריה</div>}
      {!historyQ.isLoading && !historyQ.isError && history.length === 0 && (
        <div className={styles.hint}>אין ניתוחים שמורים</div>
      )}
      <div className={styles.history}>
        {history.map((h) => (
          <div key={h.id} className={`${styles.histRow} ${linkResult?.id === h.id ? styles.histActive : ""}`}
               onClick={() => loadHistory(h)} title="טען ניתוח">
            <span className={styles.levelDot} style={{ background: levelColor(h.level) }} />
            <div className={styles.histMain}>
              <div className={styles.histName}>{h.tx_kind === "cell_site" ? "📡 " : ""}{h.tx_name ?? `#${h.id}`}</div>
              <div className={styles.histMeta}>
                {fmtDbm(h.rssi_dbm, 1)} · {fmtDb(h.margin_db)} · {fmtKm(h.distance_km)} · {h.mode ?? ""} · {fmtDate(h.created_at)}
              </div>
            </div>
            <div className={styles.histActions} onClick={(e) => e.stopPropagation()}>
              <button className={styles.iconBtn} title="דוח Word" disabled={exporting === h.id}
                      onClick={() => downloadDocx(h.id)}>{exporting === h.id ? "…" : "⬇"}</button>
              {confirmDel === h.id ? (
                <>
                  <button className={`${styles.iconBtn} ${styles.iconDanger}`} onClick={() => deleteHistory(h.id)}>מחק</button>
                  <button className={styles.iconBtn} onClick={() => setConfirmDel(null)}>✕</button>
                </>
              ) : (
                <button className={styles.iconBtn} title="מחק" onClick={() => setConfirmDel(h.id)}>🗑</button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Result card ──────────────────────────────────────────────────────────────

function LinkResultCard({ result: r, exporting, onDocx }: {
  result: LinkResult; exporting: number | null; onDocx: (id: number) => void;
}) {
  const color = levelColor(r.level);
  const warnings = collectWarnings(r.warnings, r.dem_source);
  const dem = demStatus(r.dem_source);
  const est = (r.transmitter?.estimated_fields ?? []).filter(Boolean);
  const losses: [string, number | null | undefined, string?][] = [
    ["FSPL (מרחב חופשי)", isNum(r.fspl_db) ? -r.fspl_db : r.fspl_db],
    ["עקיפה (טרן)", isNum(r.diffraction_db) ? -r.diffraction_db : r.diffraction_db],
    ["תכסית (DSM)", isNum(r.clutter_db) ? -r.clutter_db : r.clutter_db],
    ["רווח גובה", r.height_gain_db],
    ["רווח אנטנת מקלט", r.rx_gain_db],
  ];

  return (
    <div className={styles.result}>
      <div className={cs.header}>
        תוצאת קישור
        {dem !== "unknown" && (
          <span className={`${cs.demBadge} ${dem === "simulated" ? cs.demSim : cs.demReal}`}
                title={r.dem_valid_pct != null ? `${fmtPct(r.dem_valid_pct, 0)} פיקסלי DEM תקינים` : undefined}>
            {dem === "simulated" ? "⚠ " : ""}{demLabel(r.dem_source)}
          </span>
        )}
      </div>

      <div className={styles.txLine}>
        <b>{r.transmitter?.name ?? DASH}</b> → {r.receiver?.name || r.receiver?.profile_name || "מקלט"}
        <div className={styles.txMeta}>
          {fmtNum(r.transmitter?.frequency_mhz, 0)} MHz · {fmtNum(r.transmitter?.tx_power_dbm, 1)} dBm
          · אנטנה {fmtNum(r.transmitter?.ant_height_m, 1)} מ' · מקלט {fmtNum(r.receiver?.height_agl, 1)} מ'
          · {r.mode}
        </div>
        {est.length > 0 && (
          <div className={styles.estLine}>
            <span className={styles.estTag}>⚠ משוער:</span> {est.map(estimatedFieldHe).join(", ")}
          </div>
        )}
      </div>

      <div className={styles.bigRow} style={{ borderColor: color, background: `${color}14` }}>
        <div>
          <div className={styles.bigVal} style={{ color }}>{fmtDbm(r.rssi_dbm, 1)}</div>
          <div className={cs.statLbl}>RSSI במקלט</div>
        </div>
        <div>
          <div className={styles.bigVal} style={{ color }}>{fmtDb(r.margin_db)}</div>
          <div className={cs.statLbl}>מרווח <InfoTooltip text={TIPS.margin} /></div>
        </div>
        <div className={styles.levelPill} style={{ background: color }}>{levelHe(r.level)}</div>
      </div>

      <div className={styles.chips}>
        <span className={styles.chip}>📏 {fmtKm(r.distance_km)}</span>
        <span className={styles.chip}>🧭 {fmtDeg(r.bearing_deg)}</span>
        <span className={`${styles.chip} ${r.is_los ? styles.chipOk : styles.chipBad}`}>{r.is_los ? "👁 LOS" : "⛰ NLOS"}</span>
        <span className={styles.chip} title="פינוי אזור פרנל הראשון">
          פרנל {fmtPct(r.fresnel_clearance_pct, 0)} <InfoTooltip text={TIPS.fresnel} />
        </span>
      </div>

      {isNum(r.required_rx_height_m) && r.required_rx_height_m > 0 && (
        <div className={styles.callout}>
          ⬆ לקבלת קשר (מרווח ≥ 0) נדרש גובה מקלט של <b>{fmtNum(r.required_rx_height_m, 1)} מ'</b>
        </div>
      )}
      {r.required_rx_height_m === null && isNum(r.margin_db) && r.margin_db < 0 && (
        <div className={styles.callout}>⬆ לא נמצא גובה מקלט (עד 60 מ') שמשיג מרווח חיובי</div>
      )}

      {warnings.length > 0 && (
        <div className={cs.warnBox}>
          {warnings.map((w, i) => <div key={i} className={cs.warnLine}><span>⚠</span><span>{w}</span></div>)}
        </div>
      )}

      <div className={cs.subHeader}>פרופיל גובה</div>
      <LinkProfileChart result={r} />

      <div className={cs.subHeader}>תקציב קישור</div>
      <table className={styles.lossTable}>
        <tbody>
          <tr><td>הספק משדר (EIRP)</td><td>{fmtDbm(r.transmitter?.tx_power_dbm, 1)}</td></tr>
          {losses.map(([k, v]) => <tr key={k}><td>{k}</td><td>{fmtDb(v)}</td></tr>)}
          <tr className={styles.lossTotal}><td>RSSI</td><td>{fmtDbm(r.rssi_dbm, 1)}</td></tr>
          <tr><td>רגישות מקלט</td><td>{fmtDbm(r.receiver?.sensitivity_dbm)}</td></tr>
          <tr className={styles.lossTotal}><td>מרווח</td><td style={{ color }}>{fmtDb(r.margin_db)}</td></tr>
        </tbody>
      </table>
      <div className={styles.hintSmall}>
        גובה משדר {fmtNum(r.tx_h_asl, 0)} מ' ASL · גובה מקלט {fmtNum(r.rx_h_asl, 0)} מ' ASL
        {r.worst_obstacle && <> · מכשול עיקרי {fmtNum(r.worst_obstacle.terrain_m, 0)} מ' ב-{fmtKm(r.worst_obstacle.dist_km)}</>}
      </div>

      {(r.recommendations ?? []).length > 0 && (
        <>
          <div className={cs.subHeader}>המלצות</div>
          {(r.recommendations ?? []).map((t, i) => (
            <div key={i} className={cs.rec}><span className={cs.recNum}>{i + 1}</span><span>{t}</span></div>
          ))}
        </>
      )}

      <button className={`${cs.btn} ${cs.exportBtn}`} style={{ marginTop: 8 }}
              disabled={r.id == null || exporting === r.id}
              title={r.id == null ? "הניתוח לא נשמר — אין דוח" : undefined}
              onClick={() => r.id != null && onDocx(r.id)}>
        {exporting != null && exporting === r.id ? "מייצא..." : "⬇ דוח Word"}
      </button>
    </div>
  );
}

// ── Best servers list ────────────────────────────────────────────────────────

function BestServersList({ result, highlighted, onHighlight, onAnalyse, busy }: {
  result: BestServersResult;
  highlighted: number | null;
  onHighlight: (id: number) => void;
  onAnalyse: (e: BestServerEntry) => void;
  busy: boolean;
}) {
  const warnings = collectWarnings(result.warnings, result.dem_source);
  const dem = demStatus(result.dem_source);
  const rows = [...(result.results ?? [])].sort((a, b) => a.rank - b.rank);
  return (
    <div className={styles.result}>
      <div className={cs.header}>
        אתרים משרתים
        {dem !== "unknown" && (
          <span className={`${cs.demBadge} ${dem === "simulated" ? cs.demSim : cs.demReal}`}>
            {dem === "simulated" ? "⚠ " : ""}{demLabel(result.dem_source)}
          </span>
        )}
      </div>
      {result.candidates_evaluated != null && (
        <div className={styles.hintSmall}>נבדקו {result.candidates_evaluated} אתרים · לחץ על שורה להדגשה במפה</div>
      )}
      {warnings.length > 0 && (
        <div className={cs.warnBox}>
          {warnings.map((w, i) => <div key={i} className={cs.warnLine}><span>⚠</span><span>{w}</span></div>)}
        </div>
      )}
      {rows.length === 0 && <div className={styles.hint}>לא נמצאו אתרים מתאימים</div>}
      {rows.map((e) => {
        const c = e.cell_site, l = e.link;
        const color = levelColor(l?.level);
        const hl = highlighted === c.id;
        return (
          <div key={c.id} className={`${styles.server} ${hl ? styles.serverHl : ""}`}
               style={{ borderRightColor: color }} onClick={() => onHighlight(c.id)}>
            <div className={styles.serverHead}>
              <span className={styles.rank} style={{ background: color }}>{e.rank}</span>
              <OpBadge op={c.operator} label={c.operator_he} />
              <span className={styles.serverType}>{c.site_type_he ?? c.site_type ?? ""}</span>
              <span className={styles.serverRssi} style={{ color }}>{fmtDbm(l?.rssi_dbm, 1)}</span>
            </div>
            <div className={styles.txMeta}>
              {c.city || c.address || DASH} · {fmtKm(l?.distance_km)} · {fmtDeg(l?.bearing_deg)}
              · מרווח <b style={{ color }}>{fmtDb(l?.margin_db)}</b> · {l?.is_los ? "LOS" : "NLOS"}
            </div>
            <div className={styles.txMeta}>
              {fmtNum(c.frequency_mhz, 0)} MHz
              {!isManualFrequency(c) && <> <span className={styles.estTag} title="תדר משוער">⚠</span></>}
              {" · "}{fmtNum(c.tx_power_dbm, 1)} dBm
              {!isManualTxPower(c) && <> <span className={styles.estTag} title="הספק משוער">⚠</span></>}
              {" · "}אנטנה {fmtNum(c.ant_height_m, 1)} מ'
              {c.height_source !== "manual" && <> <span className={styles.estTag}>⚠</span></>}
            </div>
            <button className={`${cs.btn} ${cs.small} ${styles.serverBtn}`} disabled={busy}
                    onClick={(ev) => { ev.stopPropagation(); onAnalyse(e); }}>
              🎯 נתח קישור מלא
            </button>
          </div>
        );
      })}
    </div>
  );
}
