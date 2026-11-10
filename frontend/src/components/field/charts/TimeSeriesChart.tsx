/**
 * Device signal over time — hand-rolled SVG (spec 8.3 "מגירת מכשיר", 8.5).
 * One metric, one series (LTE RSRP and NR SS-RSRP are never on the same axis), tier bands in the
 * background labelled by name, min–max envelope + average line, and shaded gaps for
 * out-of-service / no data / paused / stale cell data. A table view is offered for accessibility.
 */
import { useMemo, useState } from "react";
import type { DeviceTimeseries } from "../../../services/api";
import { TIERS, TIER_ORDER, tierDef } from "../../../lib/fieldTiers";
import { fmtDateTime, fmtNum } from "../../../lib/fieldFormat";
import { axisNum, linear, niceTicks, timeTickLabel, timeTicks } from "./scale";
import { useWidth } from "./useWidth";
import s from "../field.module.css";
import c from "./charts.module.css";

const M = { l: 44, r: 58, t: 16, b: 40 };

export const GAP_LOOK: Record<string, { label: string; fill: string; pattern: string }> = {
  oos:     { label: "ללא שירות",       fill: "rgba(255,59,92,.16)",  pattern: "url(#gp-oos)" },
  no_data: { label: "אין נתונים",      fill: "rgba(90,122,154,.14)", pattern: "url(#gp-nodata)" },
  paused:  { label: "מושהה",           fill: "rgba(143,183,217,.12)", pattern: "url(#gp-paused)" },
  stale:   { label: "נתוני תא ישנים", fill: "rgba(127,155,184,.10)", pattern: "url(#gp-stale)" },
};

export default function TimeSeriesChart({ data, from, to, dbm = true }: {
  data: DeviceTimeseries; from: string; to: string; dbm?: boolean;
}) {
  const [asTable, setAsTable] = useState(false);
  const [wrapRef, cw] = useWidth<HTMLDivElement>(640);
  const W = Math.max(300, cw), H = W < 480 ? 220 : 250;
  const series = data.series[0];
  const pts = useMemo(() => (series?.points ?? []).map((p) => ({
    t: Date.parse(p[0]), min: p[1], avg: p[2], max: p[3], n: p[4], tier: p[5],
  })), [series]);
  const tFrom = Date.parse(from), t1 = Date.parse(to);
  // Start the axis where this device has data (or gaps) when most of the window is empty —
  // the empty part is stated in a note instead of drawn as a blank band.
  const firstT = Math.min(...pts.map((p) => p.t), ...data.gaps.map((g) => Date.parse(g[0])));
  const trimmed = Number.isFinite(firstT) && firstT - tFrom > 0.15 * (t1 - tFrom);
  const t0 = trimmed ? firstT - data.bucket_s * 1000 : tFrom;
  const x = linear([t0, t1], [M.l, W - M.r]);

  let lo = dbm ? -125 : Math.min(...pts.map((p) => p.min), 0);
  let hi = dbm ? -65 : Math.max(...pts.map((p) => p.max), 1);
  for (const p of pts) { lo = Math.min(lo, p.min); hi = Math.max(hi, p.max); }
  if (dbm) { lo = Math.floor(lo / 10) * 10; hi = Math.ceil(hi / 10) * 10; }
  const y = linear([lo, hi], [H - M.b, M.t]);
  const yTicks = niceTicks(lo, hi, 6);
  const xTicks = timeTicks(t0, t1, W < 480 ? 4 : 6);
  const bucketMs = data.bucket_s * 1000;

  // Break the line on gaps and on missing buckets
  const gapRanges = data.gaps.map(([a, b, r]) => ({ a: Date.parse(a), b: Date.parse(b), r }));
  const runs: typeof pts[] = [];
  let cur: typeof pts = [];
  for (const p of pts) {
    const prev = cur[cur.length - 1];
    const broken = prev && (p.t - prev.t > 2.5 * bucketMs
      || gapRanges.some((g) => g.r !== "stale" && g.a >= prev.t && g.b <= p.t + bucketMs && g.b - g.a > bucketMs));
    if (broken) { runs.push(cur); cur = []; }
    cur.push(p);
  }
  if (cur.length) runs.push(cur);

  const mid = (p: { t: number }) => x(Math.min(t1, p.t + bucketMs / 2));
  const linePath = (r: typeof pts) => r.map((p, i) => `${i ? "L" : "M"}${mid(p).toFixed(1)},${y(p.avg).toFixed(1)}`).join("");
  const areaPath = (r: typeof pts) =>
    r.map((p, i) => `${i ? "L" : "M"}${mid(p).toFixed(1)},${y(p.max).toFixed(1)}`).join("")
    + r.slice().reverse().map((p) => `L${mid(p).toFixed(1)},${y(p.min).toFixed(1)}`).join("") + "Z";

  const last = pts[pts.length - 1];
  const unit = data.unit;
  const label = data.label_he;
  const ariaSummary = pts.length
    ? `${label} (${unit}), ${pts.length} נקודות, ממוצע אחרון ${fmtNum(last.avg)} ${unit}; ${data.gaps.length} פערים`
    : `${label}: אין נתונים בחלון`;
  const usedGapKinds = Array.from(new Set(data.gaps.map((g) => g[2])));

  return (
    <div className={c.wrap} ref={wrapRef}>
      <svg className={c.chart} viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={ariaSummary} data-testid="device-ts-chart">
        <title>{ariaSummary}</title>
        <defs>
          <pattern id="gp-oos" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="rgba(255,59,92,.10)" /><line x1="0" y1="0" x2="0" y2="6" stroke="rgba(255,59,92,.55)" strokeWidth="1.5" />
          </pattern>
          <pattern id="gp-nodata" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="rgba(90,122,154,.08)" /><line x1="0" y1="0" x2="0" y2="6" stroke="rgba(90,122,154,.45)" strokeWidth="1" />
          </pattern>
          <pattern id="gp-paused" width="8" height="8" patternUnits="userSpaceOnUse">
            <rect width="8" height="8" fill="rgba(143,183,217,.10)" /><circle cx="4" cy="4" r="1" fill="rgba(143,183,217,.6)" />
          </pattern>
          <pattern id="gp-stale" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
            <rect width="6" height="6" fill="rgba(127,155,184,.06)" /><line x1="0" y1="0" x2="0" y2="6" stroke="rgba(127,155,184,.35)" strokeWidth="1" strokeDasharray="2 2" />
          </pattern>
          <clipPath id="ts-plot"><rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} /></clipPath>
        </defs>

        {/* Tier bands (dBm metrics only) with their names on the right edge */}
        {dbm && TIER_ORDER.map((t) => {
          const top = t === 4 ? hi : TIERS[(t + 1) as 1 | 2 | 3 | 4].min_dbm!;
          const bot = t === 0 ? lo : TIERS[t].min_dbm!;
          const yt = y(Math.min(hi, top)), yb = y(Math.max(lo, bot));
          if (yb - yt <= 0) return null;
          const def = tierDef(t);
          return (
            <g key={t}>
              <rect x={M.l} y={yt} width={W - M.l - M.r} height={yb - yt} fill={def.color} opacity={0.07} />
              <rect x={W - M.r + 4} y={yt + 1} width={3} height={Math.max(0, yb - yt - 2)} fill={def.color} />
              <text x={W - M.r + 10} y={(yt + yb) / 2 + 3.5} className={c.axTier} fill={def.color}>{def.label_he}</text>
            </g>
          );
        })}

        {/* Grid + axes */}
        {yTicks.map((v) => (
          <g key={`y${v}`}>
            <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className={c.grid} />
            <text x={M.l - 6} y={y(v) + 3.5} textAnchor="end" className={c.ax}>{axisNum(v)}</text>
          </g>
        ))}
        {xTicks.map((t) => (
          <g key={`x${t}`}>
            <line x1={x(t)} x2={x(t)} y1={M.t} y2={H - M.b} className={c.grid} />
            <text x={x(t)} y={H - M.b + 14} textAnchor="middle" className={c.ax}>{timeTickLabel(t, t1 - t0)}</text>
          </g>
        ))}
        <line x1={M.l} x2={W - M.r} y1={H - M.b} y2={H - M.b} className={c.axis} />
        <line x1={M.l} x2={M.l} y1={M.t} y2={H - M.b} className={c.axis} />
        <text x={14} y={(H - M.b + M.t) / 2} textAnchor="middle" className={c.axTitle}
          transform={`rotate(-90 14 ${(H - M.b + M.t) / 2})`}>{`${data.metric === "battery" ? "סוללה" : label.split(" ")[0]} (${unit})`}</text>
        <text x={(M.l + W - M.r) / 2} y={H - 6} textAnchor="middle" className={c.axTitle}>שעה (שעון ישראל)</text>

        {/* Gaps */}
        <g clipPath="url(#ts-plot)">
          {gapRanges.map((g, i) => {
            const a = x(Math.max(t0, g.a)), b = x(Math.min(t1, g.b));
            const w = Math.max(2, b - a);
            const look = GAP_LOOK[g.r] ?? GAP_LOOK.no_data;
            return (
              <g key={i}>
                <rect x={a} y={M.t} width={w} height={H - M.t - M.b} fill={look.pattern} data-gap={g.r}>
                  <title>{`${look.label}: ${fmtDateTime(new Date(g.a))} – ${fmtDateTime(new Date(g.b))}`}</title>
                </rect>
                {w > 46 && <text x={a + w / 2} y={M.t + 11} textAnchor="middle" className={c.axWarn}>{look.label}</text>}
              </g>
            );
          })}

          {/* Envelope + average line */}
          {runs.map((r, i) => r.length > 1 && <path key={`a${i}`} d={areaPath(r)} fill="rgba(0,212,255,.14)" />)}
          {runs.map((r, i) => r.length > 1
            ? <path key={`l${i}`} d={linePath(r)} fill="none" stroke="var(--accent)" strokeWidth={1.6} />
            : <circle key={`p${i}`} cx={mid(r[0])} cy={y(r[0].avg)} r={2.2} fill="var(--accent)" />)}
        </g>

        {last && (
          <g>
            <circle cx={mid(last)} cy={y(last.avg)} r={3.5} fill="var(--accent)" stroke="#0a0e14" strokeWidth={1.5} />
            <text x={Math.min(mid(last), W - M.r - 4)} y={Math.max(M.t + 10, y(last.avg) - 8)} textAnchor="end"
              className={c.axHi}>{`אחרון ${axisNum(Math.round(last.avg))}`}</text>
          </g>
        )}
        {!pts.length && (
          <text x={(M.l + W - M.r) / 2} y={(M.t + H - M.b) / 2} textAnchor="middle" className={c.axTitle}>
            אין מדידות בחלון הזמן
          </text>
        )}
      </svg>

      {trimmed && <p className={c.keys} style={{ display: "block" }}>אין נתונים מהמכשיר לפני {fmtDateTime(new Date(firstT))} — הציר מתחיל שם.</p>}
      <ul className={c.keys} aria-label="מקרא הגרף">
        <li><span className={c.keyLine} /> ממוצע לכל {bucketText(data.bucket_s)}</li>
        <li><span className={c.keyArea} /> טווח מינ׳–מקס׳</li>
        {usedGapKinds.map((k) => (
          <li key={k}><span className={c.keyGap} style={{ background: GAP_LOOK[k]?.fill }} /> {GAP_LOOK[k]?.label ?? k}</li>
        ))}
      </ul>
      <button className={s.lnk} onClick={() => setAsTable((v) => !v)} aria-expanded={asTable} style={{ fontSize: 11 }}>
        {asTable ? "הסתר טבלה" : "הצג כטבלה"}
      </button>
      {asTable && (
        <div className={s.tw} style={{ maxHeight: 220, overflowY: "auto", marginTop: 6 }}>
          <table className={s.t}>
            <caption className={s.srOnly}>{label}</caption>
            <thead><tr><th scope="col">זמן</th><th scope="col" className={s.numCell}>ממוצע ({unit})</th>
              <th scope="col" className={s.numCell}>מינ׳</th><th scope="col" className={s.numCell}>מקס׳</th>
              <th scope="col" className={s.numCell}>n</th><th scope="col">רמה</th></tr></thead>
            <tbody>
              {pts.map((p) => (
                <tr key={p.t}>
                  <td>{fmtDateTime(new Date(p.t))}</td>
                  <td className={s.numCell}>{fmtNum(p.avg, 1)}</td><td className={s.numCell}>{fmtNum(p.min, 1)}</td>
                  <td className={s.numCell}>{fmtNum(p.max, 1)}</td><td className={s.numCell}>{p.n}</td>
                  <td>{tierDef(p.tier).label_he}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function bucketText(sec: number): string {
  if (sec < 3600) return `${Math.round(sec / 60)} דק׳`;
  if (sec < 86400) return `${Math.round(sec / 3600)} שע׳`;
  return `${Math.round(sec / 86400)} ימים`;
}
