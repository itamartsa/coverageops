/**
 * KPI tiles (spec 8.3 "סקירה", 5.3). LTE RSRP (incl. NSA anchor) and NR SS-RSRP are separate
 * tiles — never one mixed number. % time per tier is time-weighted over classified time only;
 * "unclassified" is shown separately. VIEWER: fleet totals only, no alerts tile.
 */
import type { FieldAlert, FieldSummary } from "../../services/api";
import { TIER_ORDER, tierColorVar, tierDef } from "../../lib/fieldTiers";
import { fmtInt, fmtNum, fmtPct } from "../../lib/fieldFormat";
import { Num } from "./primitives";
import s from "./field.module.css";
import k from "./KpiTiles.module.css";

export default function KpiTiles({ summary, alerts, showAlerts }: {
  summary: FieldSummary; alerts?: FieldAlert[]; showAlerts: boolean;
}) {
  const { fleet, kpis } = summary;
  const tiers = kpis.pct_time_by_tier;
  const geMedium = tiers[0] == null ? null : (tiers[2] ?? 0) + (tiers[3] ?? 0) + (tiers[4] ?? 0);
  const rep = kpis.devices_reporting_15m;
  const lte = kpis.rsrp.lte, nr = kpis.rsrp.nr_sa, leg = kpis.rsrp.nsa_nr_leg;
  const crit = alerts?.filter((a) => a.severity === "critical").length ?? 0;
  const warn = alerts?.filter((a) => a.severity === "warning").length ?? 0;
  const info = alerts?.filter((a) => a.severity === "info").length ?? 0;

  return (
    <div className={k.kpis} data-testid="kpi-tiles">
      <div className={k.kpi}>
        <span className={k.l}>מקוונים מתוך אמורים לנטר</span>
        <span className={k.v}><Num>{rep.reporting}</Num><small> / <Num>{rep.expected}</Num></small></span>
        <span className={k.d}>
          {fleet.offline} לא מקוון · {fleet.degraded} ניטור מוגבל · בנוסף {fleet.paused} מושהים, {fleet.no_contact} ללא קשר
          {fleet.never_seen ? `, ${fleet.never_seen} טרם התחברו` : ""}
        </span>
      </div>

      <div className={k.kpi}>
        <span className={k.l}>% זמן ברמה בינוני ומעלה</span>
        <span className={k.v}>{geMedium == null ? "—" : <><Num>{fmtNum(geMedium, 1)}</Num><small>%</small></>}</span>
        <div className={k.tiers} role="img"
          aria-label={`התפלגות זמן: ${TIER_ORDER.map((t) => `${tierDef(t).label_he} ${fmtPct(tiers[t])}`).join(", ")}`}>
          {TIER_ORDER.map((t) => (tiers[t] ?? 0) > 0 && (
            <span key={t} style={{ flex: tiers[t] ?? 0, background: tierColorVar(t) }} title={`${tierDef(t).label_he}: ${fmtPct(tiers[t])}`} />
          ))}
        </div>
        <span className={k.d}>לא מסווג: <Num>{fmtPct(kpis.pct_time_unclassified)}</Num> מהזמן (מחוץ למכנה)</span>
      </div>

      <div className={k.kpi}>
        <span className={k.l}>RSRP חציון · LTE + עוגן NSA</span>
        <span className={k.v}><Num>{fmtNum(lte.p50)}</Num><small> dBm</small></span>
        <span className={k.d}>P5 <Num>{fmtNum(lte.p5)}</Num> · <Num>{fmtInt(lte.n)}</Num> דגימות</span>
      </div>

      <div className={k.kpi}>
        <span className={k.l}>RSRP אחוזון 5 · LTE</span>
        <span className={k.v}><Num>{fmtNum(lte.p5)}</Num><small> dBm</small></span>
        <span className={k.d}>שולי — 5% הגרועים · סוננו <Num>{fmtPct(kpis.stale_excluded_pct)}</Num> דגימות ישנות</span>
      </div>

      <div className={k.kpi}>
        <span className={k.l}>SS-RSRP חציון · 5G NR</span>
        <span className={k.v}><Num>{fmtNum(nr.p50)}</Num><small> dBm</small></span>
        <span className={k.d}><bdi>5G SA:</bdi> <Num>{fmtInt(nr.n)}</Num> דגימות · רגל NR ב-NSA (בנפרד): <Num>{fmtNum(leg.p50)}</Num>, <Num>{fmtInt(leg.n)}</Num> דגימות</span>
      </div>

      <div className={k.kpi}>
        <span className={k.l}>דקות ללא שירות</span>
        <span className={k.v}><Num>{fmtNum(kpis.oos_minutes, 0)}</Num><small> <bdi dir="rtl">דק׳</bdi></small></span>
        <span className={k.d}>ללא שירות / חירום בלבד · נספרות תמיד, גם בנתוני תא ישנים</span>
      </div>

      <div className={k.kpi}>
        <span className={k.l}>שינויי תא לשעה</span>
        <span className={k.v}><Num>{fmtNum(kpis.cell_change_per_hour, 1)}</Num></span>
        <span className={k.d}>זמן ב-5G NSA: <Num>{fmtPct(kpis.nsa_share_pct, 0)}</Num> מזמן LTE · שינויים נצפים</span>
      </div>

      {showAlerts && (
        <div className={`${k.kpi} ${crit ? k.kpiAlert : ""}`}>
          <span className={k.l}>התראות פתוחות</span>
          <span className={k.v}><Num>{alerts?.length ?? "—"}</Num></span>
          <span className={k.d}><span className={k.crit}>{crit} קריטיות</span> · {warn} אזהרות · {info} מידע</span>
        </div>
      )}
    </div>
  );
}

export function KpiSkeleton() {
  return (
    <div className={k.kpis} aria-busy="true" aria-label="טוען מדדים">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className={k.kpi}><div className={s.skel} style={{ height: 12, width: "70%" }} />
          <div className={s.skel} style={{ height: 28, width: "45%", marginTop: 6 }} /></div>
      ))}
    </div>
  );
}
