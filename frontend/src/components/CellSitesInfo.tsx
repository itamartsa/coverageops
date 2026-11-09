/**
 * In-app explanation + filters for the civil cellular sites layer.
 */
import { useEffect, useState } from "react";
import { useStore } from "../store/useStore";
import { apiErrorText, cellSitesApi } from "../services/api";
import type { CellSitesMeta } from "../services/api";
import { CELL_OPERATORS, ALL_OPERATORS } from "../lib/cellSites";
import { notify } from "./Notification";
import styles from "./CellSitesInfo.module.css";

const fmt = (n?: number | null) => (n ?? 0).toLocaleString("he-IL");

type DefKind = "height" | "tx" | "band";
const DEF_LIMITS: Record<DefKind, { min: number; max: number; step: number; unit: string; err: string }> = {
  height: { min: 0.5, max: 300,  step: 0.5, unit: "מ'",   err: "גובה חייב להיות בין 0.5 ל-300 מטר" },
  tx:     { min: 0,   max: 70,   step: 0.5, unit: "dBm", err: "הספק חייב להיות בין 0 ל-70 dBm" },
  band:   { min: 100, max: 6000, step: 1,   unit: "MHz", err: "תדר חייב להיות מספר שלם בין 100 ל-6000 MHz" },
};
const fmtDateTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("he-IL", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

export default function CellSitesInfo() {
  const { cellSiteFilter, setCellSiteFilter, cellSitesVersion, bumpCellSitesVersion, user } = useStore();
  const [meta, setMeta] = useState<CellSitesMeta | null>(null);
  const [err, setErr] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [showHeights, setShowHeights] = useState(false);
  const [showRf, setShowRf] = useState(false);
  // ADMIN inline edit of a default: value being typed, then an explicit confirm step.
  // kind: "height" / "tx" = per site type (key = site_type); "band" = per generation (key = "4G").
  const [editDef, setEditDef] = useState<{ kind: DefKind; type: string; value: string; confirm: boolean } | null>(null);
  const [savingDef, setSavingDef] = useState(false);
  const isAdmin = user?.role === "ADMIN";

  useEffect(() => {
    cellSitesApi.meta().then(setMeta).catch(() => setErr(true));
  }, [cellSitesVersion]);

  function toggleOperator(op: string) {
    const on = cellSiteFilter.operators.includes(op);
    setCellSiteFilter({
      operators: on ? cellSiteFilter.operators.filter((o) => o !== op) : [...cellSiteFilter.operators, op],
    });
  }

  async function sync() {
    setSyncing(true);
    try {
      const r = await cellSitesApi.sync();
      notify(r.source === "live"
        ? `✓ עודכן מ-data.gov.il: ${fmt(r.active)} פעילים, ${fmt(r.construction)} בהקמה`
        : "⚠ data.gov.il לא זמין מהשרת — נטען העותק השמור במערכת");
      bumpCellSitesVersion();
    } catch {
      notify("⚠ הרענון נכשל");
    } finally {
      setSyncing(false);
    }
  }

  async function saveTypeDefault() {
    if (!editDef) return;
    const lim = DEF_LIMITS[editDef.kind];
    const v = Number(editDef.value);
    if (editDef.value.trim() === "" || !Number.isFinite(v) || v < lim.min || v > lim.max
        || (editDef.kind === "band" && !Number.isInteger(v))) {
      notify(`⚠ ${lim.err}`);
      return;
    }
    if (!editDef.confirm) { setEditDef({ ...editDef, confirm: true }); return; }
    setSavingDef(true);
    try {
      if (editDef.kind === "height") {
        const r = await cellSitesApi.setTypeDefault(editDef.type, { default_height_m: v });
        notify(`✓ ברירת המחדל עודכנה ל-${v} מ' — ${fmt(r.updated_sites)} אתרים משוערים עודכנו`);
      } else if (editDef.kind === "tx") {
        const r = await cellSitesApi.setTypeDefault(editDef.type, { default_tx_power_dbm: v });
        notify(`✓ הספק ברירת המחדל עודכן ל-${v} dBm` +
               (r.updated_rf_sites != null ? ` — ${fmt(r.updated_rf_sites)} אתרים משוערים עודכנו` : ""));
      } else {
        const r = await cellSitesApi.setBandDefault(editDef.type, v);
        const n = Object.entries(r).find(([k, x]) => k.startsWith("updated") && typeof x === "number")?.[1] as number | undefined;
        notify(`✓ התדר של ${editDef.type} עודכן ל-${v} MHz` + (n != null ? ` — ${fmt(n)} אתרים משוערים עודכנו` : ""));
      }
      setEditDef(null);
      bumpCellSitesVersion();   // re-fetch meta + map rows
    } catch (e) {
      notify(`⚠ ${apiErrorText(e, "העדכון נכשל")}`);
    } finally {
      setSavingDef(false);
    }
  }

  /** Value cell of a defaults table: ADMIN click-to-edit, otherwise plain. */
  function defValue(kind: DefKind, key: string, value: number | null | undefined) {
    const lim = DEF_LIMITS[kind];
    const editing = editDef?.kind === kind && editDef.type === key;
    if (editing) {
      return (
        <span className={styles.defEdit}>
          <input type="number" min={lim.min} max={lim.max} step={lim.step} value={editDef.value} autoFocus
                 disabled={savingDef}
                 onChange={(e) => setEditDef({ kind, type: key, value: e.target.value, confirm: false })}
                 onKeyDown={(e) => {
                   if (e.key === "Enter") saveTypeDefault();
                   if (e.key === "Escape") setEditDef(null);
                 }} />
          {lim.unit}
          <button onClick={saveTypeDefault} disabled={savingDef}>{editDef.confirm ? "אישור" : "שמור"}</button>
          <button onClick={() => setEditDef(null)} disabled={savingDef}>ביטול</button>
        </span>
      );
    }
    const txt = value == null ? "—" : `${value} ${lim.unit}`;
    return isAdmin ? (
      <button className={styles.defVal} title="ערוך ברירת מחדל"
              onClick={() => setEditDef({ kind, type: key, value: value == null ? "" : String(value), confirm: false })}>
        {txt} ✎
      </button>
    ) : <b>{txt}</b>;
  }

  const isEditing = (kind: DefKind, key: string) => editDef?.kind === kind && editDef.type === key;

  const ops = meta?.by_operator ?? {};
  const hEst = meta?.height_estimation;
  const rfEst = meta?.rf_estimation;
  const last = meta?.last_sync;

  return (
    <div className={styles.root}>
      {/* ── What is this ─────────────────────────────────────────────────── */}
      <div className={styles.title}>על הנתונים</div>
      <p className={styles.p}>
        כל מוקדי השידור הסלולריים האזרחיים בישראל שקיבלו היתר מהמשרד להגנת הסביבה
        לפי חוק הקרינה הבלתי מייננת — אתרים <b>פעילים</b> ואתרים <b>בהקמה</b> (בקשות להיתר).
        המאגר מתפרסם כמידע פתוח ב-data.gov.il.
      </p>

      {err && <div className={styles.warn}>לא ניתן לטעון את נתוני השכבה מהשרת.</div>}

      {/* ── Stats ────────────────────────────────────────────────────────── */}
      {meta && (
        <div className={styles.stats}>
          <div className={styles.stat}><span>{fmt(meta.by_status.active)}</span>אתרים פעילים</div>
          <div className={styles.stat}><span>{fmt(meta.by_status.construction)}</span>בהקמה</div>
          <div className={styles.stat}><span>{fmt(meta.by_technology["5G"])}</span>עם 5G</div>
        </div>
      )}

      {/* ── Operator filter + legend ─────────────────────────────────────── */}
      <div className={styles.sub}>מפעילים (לחץ לסינון)</div>
      <div className={styles.chips}>
        {ALL_OPERATORS.filter((op) => op !== "OTHER" || ops.OTHER).map((op) => {
          const m = CELL_OPERATORS[op];
          const on = cellSiteFilter.operators.includes(op);
          return (
            <button key={op} className={`${styles.chip} ${on ? styles.chipOn : ""}`}
                    onClick={() => toggleOperator(op)} title={m.note ?? m.label}>
              <i style={{ background: on ? m.color : "transparent", borderColor: m.color }} />
              {m.label}
              {meta && <small>{fmt(ops[op])}</small>}
            </button>
          );
        })}
      </div>
      <div className={styles.note}>PHI = התשתית המשותפת של הוט מובייל ופרטנר.</div>

      <label className={styles.check}>
        <input type="checkbox" checked={cellSiteFilter.showConstruction}
               onChange={(e) => setCellSiteFilter({ showConstruction: e.target.checked })} />
        הצג אתרים בהקמה
        <span className={styles.legendDash} />
      </label>

      <button className={styles.more} onClick={() => setShowDetails((v) => !v)}>
        {showDetails ? "▲ פחות מידע" : "▼ מה יש במאגר ומה חסר בו"}
      </button>

      {showDetails && (
        <>
          <div className={styles.sub}>מה יש לכל אתר</div>
          <ul className={styles.ul}>
            <li>מיקום מדויק (הומר מרשת ישראל החדשה ITM ל-WGS84, דיוק ~3 מ')</li>
            <li>מפעיל, מספר אתר, כתובת, רשות מקומית</li>
            <li>סוג אתר: תורן קרקעי, תורן על גג, אתר זעיר, אנטנת עוקץ ועוד</li>
            <li>טכנולוגיות שידור: 2G / 3G / 4G / 5G</li>
            <li>תאריכי היתר הקמה, היתר הפעלה ובדיקה תקופתית אחרונה</li>
            <li>תוצאת קרינה מרבית (% מסף הבריאות) וקישור ל-PDF של ההיתרים</li>
          </ul>

          <div className={styles.sub}>מה <u>אין</u> במאגר — חשוב לניתוח כיסוי</div>
          <ul className={styles.ul}>
            <li>אזימוט וכיוון אנטנות, תדרים והספק שידור — המערכת משתמשת בערכים <b>משוערים</b> (ראה "פרמטרי RF" למטה)</li>
            <li>גובה אנטנה — המערכת מציגה גובה <b>משוער</b> לפי סוג האתר (ראה "גובה אנטנות" למטה)</li>
            <li>מזהי תאים (Cell ID / eNB) ונתוני כיסוי בפועל</li>
            <li>אתרים ביטחוניים / מסווגים אינם מפורסמים</li>
          </ul>
          <p className={styles.p}>
            לכן השכבה מציגה <b>היכן</b> יש אתרים, ולא את הכיסוי שלהם. כדי לחשב כיסוי מאתר מסוים,
            לחץ עליו במפה ובחר "📡 נתח כיסוי מאתר זה" (נוצר אתר ניתוח עם פרמטרים משוערים),
            או "🎯 קישור למכשיר מאתר זה" לחישוב קישור לנקודה.
          </p>

          {meta && (
            <>
              <div className={styles.sub}>סוגי אתרים (פעילים)</div>
              <div className={styles.table}>
                {Object.entries(meta.by_site_type).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
                  <div key={k}><span>{k}</span><b>{fmt(v)}</b></div>
                ))}
              </div>
            </>
          )}
        </>
      )}

      {/* ── Antenna heights (estimated) ──────────────────────────────────── */}
      <button className={styles.more} onClick={() => setShowHeights((v) => !v)}>
        {showHeights ? "▲ הסתר" : "▼ גובה אנטנות (משוער)"}
      </button>

      {showHeights && (
        <>
          <div className={styles.sub}>גובה אנטנות (משוער)</div>
          <p className={styles.p}>
            <b>המאגר הממשלתי אינו כולל את גובה האנטנות.</b> לכל אתר מוצג גובה <b>משוער</b> לפי
            סוג האתר (הטבלה למטה) — מסומן בחלון האתר ב-<span className={styles.estTag}>⚠ משוער</span>.
            מי שיודע את הגובה האמיתי של אתר מסוים יכול להזין אותו בחלון האתר במפה
            ("ערוך גובה"); אתר כזה מסומן <span className={styles.manTag}>✓ הוזן ידנית</span> ובמפה
            מקבל טבעת כהה. גובה שהוזן ידנית נשמר גם אחרי רענון מ-data.gov.il.
          </p>

          {hEst && (
            <>
              <div className={styles.stats2}>
                <div className={styles.stat}><span>{fmt(hEst.manual_count)}</span>הוזנו ידנית</div>
                <div className={styles.stat}><span>{fmt(hEst.estimated_count)}</span>משוערים</div>
              </div>

              <div className={styles.sub}>ברירת מחדל לפי סוג אתר{isAdmin && " (לחץ על ערך לעריכה)"}</div>
              <div className={styles.defTable}>
                {hEst.defaults.map((d) => {
                  const editing = isEditing("height", d.site_type);
                  return (
                    <div key={d.site_type} className={styles.defRow}>
                      <div className={styles.defHead}>
                        <span>{d.label_he ?? d.site_type}</span>
                        {defValue("height", d.site_type, d.default_height_m)}
                      </div>
                      {editing && editDef?.confirm && (
                        <div className={styles.warn}>
                          הפעולה תעדכן את הגובה של <b>כל</b> האתרים מסוג "{d.label_he ?? d.site_type}"
                          שגובהם עדיין משוער ל-{editDef.value} מ'. אתרים שהוזנו ידנית לא ישתנו. ללחוץ "אישור" להמשך.
                        </div>
                      )}
                      {d.note_he && <div className={styles.note}>{d.note_he}</div>}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </>
      )}

      {/* ── RF parameters (estimated) ────────────────────────────────────── */}
      <button className={styles.more} onClick={() => setShowRf((v) => !v)}>
        {showRf ? "▲ הסתר" : "▼ פרמטרי RF (משוערים)"}
      </button>

      {showRf && (
        <>
          <div className={styles.sub}>פרמטרי RF (משוערים)</div>
          <p className={styles.p}>
            <b>המאגר הממשלתי אינו כולל תדרים והספקי שידור.</b> לצורך ניתוח קישור וכיסוי מאתר סלולרי
            המערכת משתמשת ב<b>הספק משוער (EIRP)</b> לפי סוג האתר, וב<b>תדר מייצג</b> לכל דור טכנולוגיה
            (לאתר רב-דורי — הדור עם התדר הנמוך). ערכים אלה מסומנים <span className={styles.estTag}>⚠ משוער</span>.
            מי שיודע את הערכים האמיתיים יכול להזין אותם בחלון האתר במפה ("ערוך RF");
            אתר כזה מסומן <span className={styles.manTag}>✓ ידני</span>.
          </p>
          {rfEst?.note_he && <p className={styles.p} style={{ color: "var(--text-dim)" }}>{rfEst.note_he}</p>}

          {!rfEst && meta && <div className={styles.warn}>השרת אינו מחזיר עדיין נתוני RF משוערים.</div>}

          {rfEst && (
            <>
              <div className={styles.stats2}>
                <div className={styles.stat}><span>{fmt(rfEst.manual_count)}</span>הוזנו ידנית</div>
                <div className={styles.stat}><span>{fmt(rfEst.estimated_count)}</span>משוערים</div>
              </div>

              <div className={styles.sub}>הספק (EIRP) לפי סוג אתר{isAdmin && " (לחץ על ערך לעריכה)"}</div>
              <div className={styles.defTable}>
                {(rfEst.type_defaults ?? []).map((d) => {
                  const editing = isEditing("tx", d.site_type);
                  return (
                    <div key={d.site_type} className={styles.defRow}>
                      <div className={styles.defHead}>
                        <span>{d.label_he ?? d.site_type}</span>
                        {defValue("tx", d.site_type, d.default_tx_power_dbm)}
                      </div>
                      {editing && editDef?.confirm && (
                        <div className={styles.warn}>
                          הפעולה תעדכן את ההספק של <b>כל</b> האתרים מסוג "{d.label_he ?? d.site_type}"
                          שערכי ה-RF שלהם עדיין משוערים ל-{editDef.value} dBm. אתרים שהוזנו ידנית לא ישתנו. ללחוץ "אישור" להמשך.
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              <div className={styles.sub}>תדר מייצג לפי דור{isAdmin && " (לחץ על ערך לעריכה)"}</div>
              <div className={styles.defTable}>
                {(rfEst.band_defaults ?? []).map((b) => {
                  const editing = isEditing("band", b.generation);
                  return (
                    <div key={b.generation} className={styles.defRow}>
                      <div className={styles.defHead}>
                        <span><b>{b.generation}</b>{b.label_he && <span className={styles.note}> · {b.label_he}</span>}</span>
                        {defValue("band", b.generation, b.frequency_mhz)}
                      </div>
                      {editing && editDef?.confirm && (
                        <div className={styles.warn}>
                          הפעולה תשנה את התדר המייצג של {b.generation} ל-{editDef.value} MHz, ותעדכן את התדר
                          של אתרים משוערים שהתדר שלהם נגזר מדור זה. אתרים שהוזנו ידנית לא ישתנו. ללחוץ "אישור" להמשך.
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </>
      )}

      {/* ── Source & freshness ───────────────────────────────────────────── */}
      <div className={styles.sub}>מקור ועדכון</div>
      <div className={styles.source}>
        <div>מפרסם: {meta?.source.publisher ?? "המשרד להגנת הסביבה"}</div>
        <div>
          מאגרים:{" "}
          {(meta?.source.datasets ?? []).map((d, i) => (
            <span key={d.id}>{i > 0 && " · "}<a href={d.url} target="_blank" rel="noopener noreferrer">{d.title}</a></span>
          ))}
        </div>
        <div>נתונים נמשכו מ-data.gov.il: {fmtDateTime(last?.data_fetched_at)}</div>
        <div>נטען למערכת: {fmtDateTime(last?.synced_at)}
          {last && <span className={styles.badge}>{last.source === "live" ? "עדכני" : "עותק שמור"}</span>}
        </div>
      </div>

      {user?.role === "ADMIN" && (
        <button className={styles.syncBtn} onClick={sync} disabled={syncing}>
          {syncing ? "מרענן…" : "⟳ רענן מ-data.gov.il"}
        </button>
      )}
    </div>
  );
}
