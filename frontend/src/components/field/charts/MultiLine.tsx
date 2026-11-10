/**
 * Several series over time (e.g. % time at medium-or-better per operator) — hand-rolled SVG.
 * Each line is labelled with its name and last value at its end (direct labelling, spec 8.6).
 */
import { useState } from "react";
import { fmtDateTime, fmtNum } from "../../../lib/fieldFormat";
import { linear, niceTicks, timeTickLabel, timeTicks } from "./scale";
import { useWidth } from "./useWidth";
import s from "../field.module.css";
import c from "./charts.module.css";

export interface LineSeries { key: string; label: string; color: string; points: { t: number; v: number | null; n?: number }[] }

export default function MultiLine({ series, from, to, unit, yTitle, ariaLabel, yMin, yMax, bucketMs }: {
  series: LineSeries[]; from: string; to: string; unit: string; yTitle: string; ariaLabel: string;
  yMin?: number; yMax?: number; bucketMs: number;
}) {
  const [asTable, setAsTable] = useState(false);
  const [wrapRef, cw] = useWidth<HTMLDivElement>(640);
  const W = Math.max(300, cw), H = 240, M = { l: 40, r: W < 480 ? 96 : 118, t: 14, b: 40 };
  const t0 = Date.parse(from), t1 = Date.parse(to);
  const vals = series.flatMap((sr) => sr.points.map((p) => p.v).filter((v): v is number => v != null));
  let lo = yMin ?? Math.min(...vals), hi = yMax ?? Math.max(...vals);
  if (!vals.length) { lo = yMin ?? 0; hi = yMax ?? 100; }
  if (yMin == null) lo = Math.floor(lo / 5) * 5 - 5;
  if (yMax == null) hi = Math.ceil(hi / 5) * 5 + 5;
  if (hi - lo < 10) { lo -= 5; hi += 5; }
  const x = linear([t0, t1], [M.l, W - M.r]);
  const y = linear([lo, hi], [H - M.b, M.t]);
  const xT = timeTicks(t0, t1, W < 480 ? 4 : 6);
  const yT = niceTicks(lo, hi, 5);

  // End labels, nudged apart so they never overlap
  const ends = series.map((sr) => {
    const last = [...sr.points].reverse().find((p) => p.v != null);
    return { sr, last, yy: last ? y(last.v!) : NaN };
  }).filter((e) => e.last).sort((a, b) => a.yy - b.yy);
  for (let i = 1; i < ends.length; i++) if (ends[i].yy - ends[i - 1].yy < 13) ends[i].yy = ends[i - 1].yy + 13;

  const path = (pts: LineSeries["points"]) => {
    let d = "", prev: number | null = null;
    for (const p of pts) {
      if (p.v == null) { prev = null; continue; }
      const px = x(p.t + bucketMs / 2), py = y(p.v);
      d += `${prev == null || p.t - prev > 2.5 * bucketMs ? "M" : "L"}${px.toFixed(1)},${py.toFixed(1)}`;
      prev = p.t;
    }
    return d;
  };

  return (
    <div className={c.wrap} ref={wrapRef}>
      <svg className={c.chart} viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={ariaLabel} data-testid="multiline-chart">
        <title>{ariaLabel}</title>
        {yT.map((v) => (
          <g key={v}>
            <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className={c.grid} />
            <text x={M.l - 6} y={y(v) + 3.5} textAnchor="end" className={c.ax}>{`${v}${unit === "%" ? "%" : ""}`}</text>
          </g>
        ))}
        {xT.map((t) => (
          <text key={t} x={x(t)} y={H - M.b + 14} textAnchor="middle" className={c.ax}>{timeTickLabel(t, t1 - t0)}</text>
        ))}
        <line x1={M.l} x2={W - M.r} y1={H - M.b} y2={H - M.b} className={c.axis} />
        <text x={(M.l + W - M.r) / 2} y={H - 6} textAnchor="middle" className={c.axTitle}>{`זמן (שעון ישראל) · ${yTitle}`}</text>
        {series.map((sr) => (
          <g key={sr.key}>
            <path d={path(sr.points)} fill="none" stroke={sr.color} strokeWidth={2} />
            {sr.points.filter((p) => p.v != null).length === 1 && sr.points.filter((p) => p.v != null).map((p) => (
              <circle key={p.t} cx={x(p.t + bucketMs / 2)} cy={y(p.v!)} r={3} fill={sr.color} />
            ))}
          </g>
        ))}
        {ends.map((e) => (
          <g key={e.sr.key}>
            <circle cx={W - M.r + 8} cy={e.yy} r={3.5} fill={e.sr.color} />
            <text x={W - M.r + 15} y={e.yy + 4} className={c.axOp}>{`${e.sr.label} ${fmtNum(e.last!.v, unit === "%" ? 0 : 0)}${unit === "%" ? "%" : ""}`}</text>
          </g>
        ))}
        {!vals.length && (
          <text x={(M.l + W - M.r) / 2} y={H / 2} textAnchor="middle" className={c.axTitle}>אין נתונים בחלון</text>
        )}
      </svg>
      <button className={s.lnk} onClick={() => setAsTable((v) => !v)} aria-expanded={asTable} style={{ fontSize: 11 }}>
        {asTable ? "הסתר טבלה" : "הצג כטבלה"}
      </button>
      {asTable && (
        <div className={s.tw} style={{ maxHeight: 240, overflowY: "auto" }}>
          <table className={s.t}>
            <caption className={s.srOnly}>{ariaLabel}</caption>
            <thead><tr><th scope="col">סדרה</th><th scope="col">זמן</th><th scope="col" className={s.numCell}>ערך ({unit})</th><th scope="col" className={s.numCell}>n</th></tr></thead>
            <tbody>
              {series.flatMap((sr) => sr.points.map((p) => (
                <tr key={`${sr.key}${p.t}`}><td>{sr.label}</td><td>{fmtDateTime(new Date(p.t))}</td>
                  <td className={s.numCell}>{fmtNum(p.v, 1)}</td><td className={s.numCell}>{p.n ?? "—"}</td></tr>
              )))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
