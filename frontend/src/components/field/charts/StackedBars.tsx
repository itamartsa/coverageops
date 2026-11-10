/**
 * Horizontal 100%-stacked bars of the time-weighted tier distribution (spec 8.3 "ניתוח").
 * Segments go best → worst left to right; each segment ≥ 26 px carries its % label, every
 * segment has a <title>; the row label (operator name) is written next to the bar — never a
 * colour-only key (operators' blue/purple are hard to tell apart, spec 8.6).
 */
import { useState } from "react";
import { TIER_ORDER, tierDef } from "../../../lib/fieldTiers";
import { fmtNum } from "../../../lib/fieldFormat";
import { linear } from "./scale";
import { useWidth } from "./useWidth";
import s from "../field.module.css";
import c from "./charts.module.css";

export interface StackRow { key: string; label: string; tiers: (number | null)[]; note?: string }

export default function StackedBars({ rows, ariaLabel }: { rows: StackRow[]; ariaLabel: string }) {
  const [asTable, setAsTable] = useState(false);
  const [wrapRef, cw] = useWidth<HTMLDivElement>(640);
  const W = Math.max(300, cw), rowH = 30, labelW = W < 480 ? 70 : 92, top = 6, axisH = 34;
  const H = top + rows.length * (rowH + 12) + axisH;
  const x = linear([0, 100], [8, W - labelW - 8]);

  return (
    <div className={c.wrap} ref={wrapRef}>
      <svg className={c.chart} viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={ariaLabel} data-testid="tier-stack-chart">
        <title>{ariaLabel}</title>
        {[0, 25, 50, 75, 100].map((v) => (
          <g key={v}>
            <line x1={x(v)} x2={x(v)} y1={top} y2={H - axisH + 4} className={c.grid} />
            <text x={x(v)} y={H - axisH + 18} textAnchor="middle" className={c.ax}>{`${v}%`}</text>
          </g>
        ))}
        <text x={(x(0) + x(100)) / 2} y={H - 4} textAnchor="middle" className={c.axTitle}>% מזמן הניטור המסווג (משוקלל זמן)</text>
        {rows.map((r, i) => {
          const y0 = top + i * (rowH + 12);
          let acc = 0;
          return (
            <g key={r.key}>
              <text x={W - labelW + 8} y={y0 + rowH / 2 + 4} className={c.axOp}>{r.label}</text>
              {TIER_ORDER.map((t) => {
                const v = r.tiers[t] ?? 0;
                const a = x(acc), b = x(acc + v);
                acc += v;
                if (v <= 0) return null;
                const def = tierDef(t);
                return (
                  <g key={t}>
                    <rect x={a} y={y0} width={Math.max(0, b - a - 1)} height={rowH} fill={def.color} rx={2}>
                      <title>{`${r.label} · ${def.label_he}: ${fmtNum(v, 1)}%`}</title>
                    </rect>
                    {b - a >= 26 && (
                      <text x={(a + b) / 2} y={y0 + rowH / 2 + 4} textAnchor="middle" className={c.seg}>{`${Math.round(v)}%`}</text>
                    )}
                  </g>
                );
              })}
              {r.tiers.every((v) => v == null) && (
                <text x={x(50)} y={y0 + rowH / 2 + 4} textAnchor="middle" className={c.axTitle}>אין זמן מסווג</text>
              )}
            </g>
          );
        })}
      </svg>
      <button className={s.lnk} onClick={() => setAsTable((v) => !v)} aria-expanded={asTable} style={{ fontSize: 11 }}>
        {asTable ? "הסתר טבלה" : "הצג כטבלה"}
      </button>
      {asTable && (
        <div className={s.tw}>
          <table className={s.t}>
            <caption className={s.srOnly}>{ariaLabel}</caption>
            <thead><tr><th scope="col">שורה</th>
              {TIER_ORDER.map((t) => <th key={t} scope="col" className={s.numCell}>{tierDef(t).label_he}</th>)}</tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}><th scope="row">{r.label}</th>
                  {TIER_ORDER.map((t) => <td key={t} className={s.numCell}>{r.tiers[t] == null ? "—" : `${fmtNum(r.tiers[t], 1)}%`}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
