/**
 * "ניתוח" tab (spec 8.3, 5.3, 5.4): tier distribution per operator (time-weighted share of
 * classified time), operator comparison table (+ "fair comparison" over shared grid cells),
 * trend over time, dead-zone list and data-quality figures. All aggregate — VIEWER sees it too
 * (the server applies k-anonymity and day resolution for VIEWER).
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiErrorText, fieldApi, type FieldOperator, type FieldSummary } from "../../services/api";
import type { FieldFilters } from "../../store/useStore";
import { TIER_ORDER, TIER_MEASURE_HE, tierDef, tierRangeText } from "../../lib/fieldTiers";
import { fmtDateTime, fmtInt, fmtNum, fmtPct } from "../../lib/fieldFormat";
import { operatorColor, operatorName, windowRange, WINDOW_LABEL_HE } from "../../lib/fieldQueries";
import StackedBars from "./charts/StackedBars";
import MultiLine from "./charts/MultiLine";
import { ErrorState, Loading, Num, TierSwatch } from "./primitives";
import s from "./field.module.css";
import t from "./FieldTabs.module.css";

export default function AnalysisTab({ filters, isViewer, operators, summary }: {
  filters: FieldFilters; isViewer: boolean; operators?: FieldOperator[]; summary?: FieldSummary;
}) {
  const [fair, setFair] = useState(false);
  const op = filters.operators.join(",") || null;
  const group = isViewer ? null : filters.groupId;
  const tech = filters.techs.length === 1 ? filters.techs[0] : null;
  const win = filters.window;

  const brQ = useQuery({
    queryKey: ["field", "breakdown", "operator", win, op, group, fair],
    queryFn: () => fieldApi.breakdown({ ...windowRange(win), by: "operator", fair, operator: op, group_id: group }),
    refetchInterval: 5 * 60_000, refetchIntervalInBackground: false,
  });
  const techQ = useQuery({
    queryKey: ["field", "breakdown", "tech", win, op, group],
    queryFn: () => fieldApi.breakdown({ ...windowRange(win), by: "tech", operator: op, group_id: group }),
    refetchInterval: 5 * 60_000, refetchIntervalInBackground: false,
  });
  // VIEWER: day buckets only (server rule); short windows get hourly buckets for A/O
  const bucket: "1h" | "1d" = isViewer || win === "7d" || win === "30d" ? "1d" : "1h";
  const tsQ = useQuery({
    queryKey: ["field", "agg-ts", "pct_time_ge_medium", win, bucket, op, group, tech],
    queryFn: () => fieldApi.timeseriesAgg({ ...windowRange(win === "1h" && bucket === "1d" ? "24h" : win), metric: "pct_time_ge_medium",
      bucket, operator: op, group_id: group, meas_tech: tech }),
    refetchInterval: 5 * 60_000, refetchIntervalInBackground: false,
  });
  const dzQ = useQuery({
    queryKey: ["field", "dead-zones", win, op, tech],
    queryFn: () => fieldApi.deadZones({ ...windowRange(win === "1h" ? "24h" : win), operator: op, tech }),
    refetchInterval: 5 * 60_000, refetchIntervalInBackground: false,
  });

  const rows = brQ.data?.rows ?? [];
  const k = summary?.kpis;

  return (
    <div className={t.stack}>
      <div className={t.g2}>
        <section className={s.card} aria-label="התפלגות רמות לפי מפעיל">
          <div className={s.cardH}>
            <h2>התפלגות רמות לפי מפעיל</h2>
            <span className={s.sub}>% זמן משוקלל · LTE כולל עוגן NSA · ללא "לא מסווג" במכנה · {WINDOW_LABEL_HE[win]}</span>
            <div className={s.tools}>
              <label className={s.chip} style={{ cursor: "pointer" }}>
                <input type="checkbox" checked={fair} onChange={(e) => setFair(e.target.checked)} /> השוואה הוגנת (תאים משותפים)
              </label>
            </div>
          </div>
          <div className={s.cardB}>
            {brQ.isLoading ? <Loading /> : brQ.error ? <ErrorState error={brQ.error} onRetry={() => brQ.refetch()} /> : rows.length === 0 ? (
              <Insufficient text="אין מדידות מסווגות בחלון ובסינון שנבחרו." />
            ) : (
              <>
                <StackedBars ariaLabel="התפלגות זמן לפי רמת קליטה לכל מפעיל"
                  rows={rows.map((r) => ({ key: String(r.key), label: r.label, tiers: r.pct_time_by_tier }))} />
                <ul className={s.legend} style={{ borderTop: 0, paddingInline: 0 }} aria-label="מקרא רמות">
                  <li className={s.lgH}>{TIER_MEASURE_HE}</li>
                  {TIER_ORDER.map((x) => <li key={x}><TierSwatch tier={x} /> {tierDef(x).label_he} <bdi dir="ltr" className={s.num}>{tierRangeText(x)}</bdi></li>)}
                </ul>
                <p className={s.note}>
                  {rows.filter((r) => r.pct_time_unclassified).map((r) => `${r.label}: לא מסווג ${fmtPct(r.pct_time_unclassified)}`).join(" · ") || "אין זמן לא מסווג"} — מחוץ לגרף.
                  {fair && " השוואה הוגנת: רק תאי 100 מ׳ שנמדדו ע״י כל המפעילים שבחלון."}
                </p>
                {!!brQ.data?.suppressed && <p className={s.note}>{brQ.data.suppressed} שורות הוסתרו (פחות מ-3 מכשירים).</p>}
              </>
            )}
          </div>
        </section>

        <section className={s.card} aria-label="מגמה לפי מפעיל">
          <div className={s.cardH}>
            <h2>{bucket === "1d" ? "מגמה יומית" : "מגמה שעתית"} לפי מפעיל</h2>
            <span className={s.sub}>% זמן ברמה בינוני ומעלה{tech ? ` · ${tech === "LTE" ? "LTE" : "NR"}` : ""}{isViewer ? " · רזולוציה יומית לצופה" : ""}</span>
          </div>
          <div className={s.cardB}>
            {tsQ.isLoading ? <Loading /> : tsQ.error ? <ErrorState error={tsQ.error} onRetry={() => tsQ.refetch()} /> : (
              (tsQ.data?.series.length ?? 0) === 0 ? <Insufficient text="אין מספיק נתונים למגמה בחלון זה." /> : (
                <>
                  <MultiLine
                    ariaLabel="אחוז זמן ברמה בינוני ומעלה לאורך זמן, לכל מפעיל"
                    from={tsQ.data!.from} to={tsQ.data!.to} unit="%" yTitle="% זמן ≥ בינוני" yMax={100}
                    bucketMs={bucket === "1d" ? 86400_000 : 3600_000}
                    series={tsQ.data!.series.map((sr) => ({
                      key: `${sr.key}:${sr.meas_tech}`, label: operatorName(sr.key, operators), color: operatorColor(sr.key, operators),
                      points: sr.points.map(([tt, v, n]) => ({ t: Date.parse(tt), v, n })),
                    }))} />
                  {bucket === "1d" && win !== "30d" && win !== "7d" && <p className={s.note}>חלון קצר ביום אחד — נקודה אחת לכל מפעיל.</p>}
                </>
              )
            )}
          </div>
        </section>
      </div>

      <section className={s.card} aria-label="השוואת מפעילים">
        <div className={s.cardH}>
          <h2>השוואת מפעילים</h2>
          <span className={s.sub}>{WINDOW_LABEL_HE[win]} · {fair ? "תאים משותפים בלבד" : "כל הדגימות"} · ללא נדידה</span>
          <div className={s.tools}>
            <button className={s.btn} onClick={() => fieldApi.exportKpisCsv({ ...windowRange(win), by: "operator", fair, operator: op })}>ייצוא CSV</button>
          </div>
        </div>
        {brQ.isLoading ? <Loading /> : brQ.error ? <ErrorState error={brQ.error} /> : (
          <div className={s.tw}>
            <table className={s.t}>
              <thead><tr>
                <th scope="col">מפעיל</th><th scope="col" className={s.numCell}>דגימות</th>
                {!isViewer && <th scope="col" className={s.numCell}>מכשירים</th>}
                <th scope="col" className={s.numCell}>RSRP P5 · LTE</th><th scope="col" className={s.numCell}>RSRP P50 · LTE</th>
                <th scope="col" className={s.numCell}>SS-RSRP P50 · NR SA</th>
                <th scope="col" className={s.numCell}>% זמן ≥ בינוני</th><th scope="col" className={s.numCell}>דקות ללא שירות</th>
                <th scope="col" className={s.numCell}>שינויי תא / ש׳</th><th scope="col" className={s.numCell}>5G NSA %</th>
                <th scope="col" className={s.numCell}>סוננו כישנות</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => {
                  const ge = r.pct_time_by_tier[0] == null ? null : (r.pct_time_by_tier[2] ?? 0) + (r.pct_time_by_tier[3] ?? 0) + (r.pct_time_by_tier[4] ?? 0);
                  return (
                    <tr key={String(r.key)}>
                      <th scope="row"><span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, background: operatorColor(String(r.key), operators), marginInlineEnd: 6 }} aria-hidden="true" />{r.label}</th>
                      <td className={s.numCell}>{fmtInt(r.n)}</td>
                      {!isViewer && <td className={s.numCell}>{r.n_devices ?? "—"}</td>}
                      <td className={s.numCell}>{fmtNum(r.rsrp.lte.p5)}</td><td className={s.numCell}>{fmtNum(r.rsrp.lte.p50)}</td>
                      <td className={s.numCell}>{fmtNum(r.rsrp.nr_sa.p50)}</td>
                      <td className={s.numCell}>{fmtPct(ge)}</td><td className={s.numCell}>{fmtNum(r.oos_min, 0)}</td>
                      <td className={s.numCell}>{fmtNum(r.cell_changes_per_h, 1)}</td><td className={s.numCell}>{fmtPct(r.nsa_share, 0)}</td>
                      <td className={s.numCell}>{fmtPct(r.stale_excluded_pct)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className={s.note} style={{ margin: "10px 14px" }}>
          RSRP P5/P50 של LTE (כולל עוגן NSA) ו-SS-RSRP של NR SA מחושבים בנפרד ולעולם אינם מעורבבים. אחוזי זמן משוקללים לפי הזמן בין דגימות (תקרה 600 ש׳); "שינויי תא" הם שינויים נצפים, לא handover רשמי.
        </p>
      </section>

      <div className={t.g2}>
        <section className={s.card} aria-label="אזורים מתים" data-testid="dead-zones-list">
          <div className={s.cardH}>
            <h2>אזורים מתים</h2>
            <span className={s.sub}>תאי 100 מ׳: ≥ 10 דגימות מ-≥ 2 מכשירים או ימים, ו-≥ 60% מהזמן "שולי/אין" או P50 &lt; −115 · אשכולות 8-שכנים</span>
          </div>
          {dzQ.isLoading ? <Loading /> : dzQ.error ? <ErrorState error={dzQ.error} onRetry={() => dzQ.refetch()} /> : (
            (dzQ.data?.length ?? 0) === 0 ? <div className={s.cardB}><Insufficient text="לא זוהו אזורים מתים בחלון זה." /></div> : (
              <div className={s.tw}>
                <table className={s.t}>
                  <thead><tr><th scope="col">חומרה</th><th scope="col">מפעיל · טכנולוגיה</th><th scope="col">מיקום (מרכז)</th>
                    <th scope="col" className={s.numCell}>תאים</th><th scope="col" className={s.numCell}>שטח</th>
                    <th scope="col" className={s.numCell}>P50</th><th scope="col" className={s.numCell}>ללא שירות</th>
                    {!isViewer && <th scope="col" className={s.numCell}>מכשירים</th>}<th scope="col">נראה לאחרונה</th></tr></thead>
                  <tbody>
                    {dzQ.data!.map((z) => (
                      <tr key={z.id}>
                        <td>{z.severity === "critical" ? <span className={`${s.al} ${s["al-crit"]}`}>⛔ קריטי</span> : <span className={`${s.al} ${s["al-warn"]}`}>⚠ אזהרה</span>}</td>
                        <td>{operatorName(z.operator_key, operators)} · {z.tech}</td>
                        <td><Num>{`${z.centroid_lat.toFixed(3)}, ${z.centroid_lon.toFixed(3)}`}</Num></td>
                        <td className={s.numCell}>{z.cells}</td><td className={s.numCell}>{fmtNum(z.area_km2, 2)} km²</td>
                        <td className={s.numCell}>{z.p50 != null ? `${fmtNum(z.p50)} dBm` : "—"}</td>
                        <td className={s.numCell}>{fmtPct(z.oos_pct, 0)}</td>
                        {!isViewer && <td className={s.numCell}>{z.n_devices ?? "—"}</td>}
                        <td>{fmtDateTime(z.last_seen)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}
        </section>

        <section className={s.card} aria-label="איכות נתונים" data-testid="data-quality">
          <div className={s.cardH}><h2>איכות נתונים</h2><span className={s.sub}>{WINDOW_LABEL_HE[win]} · כל המפעילים בסינון</span></div>
          <div className={s.cardB}>
            {!k ? <Loading /> : (
              <dl className={t.stats}>
                <Stat l="סוננו כנתוני תא ישנים (> 10 ש׳)" v={fmtPct(k.stale_excluded_pct)} hint="% מהדגימות; לא נכנסות ל-RSRP ולרמות" />
                <Stat l="גיל נתוני תא לא ידוע" v={fmtPct(k.age_unknown_pct)} hint="נחשבות טריות, נספרות בנפרד" />
                <Stat l="זמן לא ידוע" v={fmtPct(k.pct_time_unknown)} hint="פערים > 600 ש׳ ונתונים ישנים" />
                <Stat l="זמן לא מסווג" v={fmtPct(k.pct_time_unclassified)} hint="2G/3G, מצב טיסה, ללא RSRP" />
                <Stat l="מכשירים מדווחים (15 דק׳)" v={k.devices_reporting_15m.pct == null ? "—" : fmtPct(k.devices_reporting_15m.pct, 0)}
                  hint={`${k.devices_reporting_15m.reporting} מתוך ${k.devices_reporting_15m.expected} שאמורים לנטר`} />
                <Stat l="נפילה ל-2G/3G/ללא רשת" v={fmtPct(k.fallback_pct)} hint="% מזמן עם שירות" />
                <Stat l="חיבור נתונים פעיל" v={fmtPct(k.data_connected_pct, 0)} hint="% זמן" />
                <Stat l="דגימות בחלון" v={fmtInt(k.samples)} hint={`עדכון אחרון: ${fmtDateTime(summary?.freshness.last_sample_received_at)}`} />
              </dl>
            )}
            {techQ.data && techQ.data.rows.length > 0 && (
              <>
                <h3 className={t.h3}>זמן לפי טכנולוגיה</h3>
                <ul className={t.techList}>
                  {techQ.data.rows.map((r) => (
                    <li key={String(r.key)}><span>{r.label}</span><Num>{fmtNum(r.time_s / 3600, 1)}</Num> <span className={s.muted}>שע׳ ·</span> <Num>{fmtInt(r.n)}</Num> <span className={s.muted}>דגימות</span></li>
                  ))}
                </ul>
              </>
            )}
            {techQ.error && <p className={s.note}>{apiErrorText(techQ.error)}</p>}
          </div>
        </section>
      </div>

      <section className={s.card} aria-label="נמדד מול חזוי">
        <div className={s.cardH}><h2>נמדד מול חזוי</h2><span className={s.sub}>השוואה לניתוחי הכיסוי של המערכת (RSSI מומר ל-RSRP)</span></div>
        <div className={s.cardB}>
          <div className={s.insuff} data-testid="compare-not-built">
            <span className={s.insuffIc} aria-hidden="true">⏳</span>
            <div>
              <b>בפיתוח — שלב הבא (M4).</b> כאן תוצג השוואה בין ניתוח כיסוי שנבחר לבין המדידות בשטח: גרף פיזור חזוי מול נמדד,
              הטיה / MAE / RMSE ומטריצת הסכמה ברמות. היכולת דורשת צבירת מדידות בתחום הניתוח (לפחות 10 דגימות לתא מ-2 ימים)
              ואת ממשק ההשוואה בשרת, שטרם מומש. לא מוצגים כאן מספרים עד אז.
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function Stat({ l, v, hint }: { l: string; v: string; hint?: string }) {
  return (
    <div className={t.stat}>
      <dt>{l}</dt>
      <dd><bdi dir="ltr">{v}</bdi></dd>
      {hint && <dd className={t.hint}>{hint}</dd>}
    </div>
  );
}

export function Insufficient({ text }: { text: string }) {
  return (
    <div className={s.insuff}>
      <span className={s.insuffIc} aria-hidden="true">?</span>
      <div>{text}</div>
    </div>
  );
}
