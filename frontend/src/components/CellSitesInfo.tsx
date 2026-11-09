/**
 * In-app explanation + filters for the civil cellular sites layer.
 */
import { useEffect, useState } from "react";
import { useStore } from "../store/useStore";
import { cellSitesApi } from "../services/api";
import type { CellSitesMeta } from "../services/api";
import { CELL_OPERATORS, ALL_OPERATORS } from "../lib/cellSites";
import { notify } from "./Notification";
import styles from "./CellSitesInfo.module.css";

const fmt = (n?: number) => (n ?? 0).toLocaleString("he-IL");
const fmtDateTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("he-IL", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

export default function CellSitesInfo() {
  const { cellSiteFilter, setCellSiteFilter, cellSitesVersion, bumpCellSitesVersion, user } = useStore();
  const [meta, setMeta] = useState<CellSitesMeta | null>(null);
  const [err, setErr] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [showDetails, setShowDetails] = useState(false);

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

  const ops = meta?.by_operator ?? {};
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
            <li>אזימוט וכיוון אנטנות, גובה אנטנה, תדרים והספק שידור</li>
            <li>מזהי תאים (Cell ID / eNB) ונתוני כיסוי בפועל</li>
            <li>אתרים ביטחוניים / מסווגים אינם מפורסמים</li>
          </ul>
          <p className={styles.p}>
            לכן השכבה מציגה <b>היכן</b> יש אתרים, ולא את הכיסוי שלהם. כדי לחשב כיסוי מאתר מסוים,
            הוסף אותו בלשונית "אתר חדש" עם גובה, תדר והספק משוערים, והרץ ניתוח.
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
