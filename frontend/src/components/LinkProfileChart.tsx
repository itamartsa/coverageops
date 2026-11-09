/**
 * Terrain profile of a point-to-point link: terrain (gaps where DEM is missing),
 * line of sight, first Fresnel zone envelope, tx/rx antennas and the worst obstacle.
 * Plain SVG, measured to its container width.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { LinkResult } from "../services/api";
import styles from "./LinkPanel.module.css";

const H = 190;
const M = { top: 12, right: 10, bottom: 26, left: 40 };

/** ~n "nice" ticks covering [lo, hi]. */
function niceTicks(lo: number, hi: number, n = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return [lo];
  const raw = (hi - lo) / Math.max(1, n);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toFixed(6));
  return out;
}

const fmtTick = (v: number) => (Math.abs(v) >= 100 || Number.isInteger(v) ? String(Math.round(v)) : v.toFixed(1));

export default function LinkProfileChart({ result }: { result: LinkResult }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(300);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const update = () => setWidth(Math.max(200, Math.floor(el.clientWidth)));
    update();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pts = useMemo(
    () => (result.profile ?? []).filter((p) => Number.isFinite(p?.dist_km) && Number.isFinite(p?.los_m)),
    [result.profile],
  );

  const chart = useMemo(() => {
    if (pts.length < 2) return null;
    const W = width;
    const iw = W - M.left - M.right;
    const ih = H - M.top - M.bottom;

    const xMax = Math.max(pts[pts.length - 1].dist_km, result.distance_km || 0, 0.001);
    const ys: number[] = [];
    for (const p of pts) {
      const f = Number.isFinite(p.fresnel_m) ? p.fresnel_m : 0;
      ys.push(p.los_m + f, p.los_m - f);
      if (p.terrain_m != null && Number.isFinite(p.terrain_m)) ys.push(p.terrain_m);
    }
    for (const v of [result.tx_h_asl, result.rx_h_asl, result.tx_ground_asl, result.rx_ground_asl]) {
      if (typeof v === "number" && Number.isFinite(v)) ys.push(v);
    }
    let yMin = Math.min(...ys), yMax = Math.max(...ys);
    const pad = Math.max(5, (yMax - yMin) * 0.08);
    yMin -= pad; yMax += pad;
    const yTicks = niceTicks(yMin, yMax, 4);
    yMin = Math.min(yMin, yTicks[0]);
    yMax = Math.max(yMax, yTicks[yTicks.length - 1]);

    const x = (d: number) => M.left + (d / xMax) * iw;
    const y = (h: number) => M.top + (1 - (h - yMin) / (yMax - yMin)) * ih;
    const base = M.top + ih;

    // Terrain: one filled polygon per run of non-null samples (gaps stay empty)
    const terrainPaths: string[] = [];
    let run: { d: number; h: number }[] = [];
    const flush = () => {
      if (run.length >= 2) {
        terrainPaths.push(
          `M${x(run[0].d)},${base} ` + run.map((p) => `L${x(p.d)},${y(p.h)}`).join(" ") +
          ` L${x(run[run.length - 1].d)},${base} Z`);
      }
      run = [];
    };
    for (const p of pts) {
      if (p.terrain_m == null || !Number.isFinite(p.terrain_m)) flush();
      else run.push({ d: p.dist_km, h: p.terrain_m });
    }
    flush();
    const hasGaps = pts.some((p) => p.terrain_m == null || !Number.isFinite(p.terrain_m));

    const los = pts.map((p, i) => `${i ? "L" : "M"}${x(p.dist_km)},${y(p.los_m)}`).join(" ");
    const fr = (p: (typeof pts)[number]) => (Number.isFinite(p.fresnel_m) ? p.fresnel_m : 0);
    const fresnel =
      pts.map((p, i) => `${i ? "L" : "M"}${x(p.dist_km)},${y(p.los_m + fr(p))}`).join(" ") + " " +
      [...pts].reverse().map((p) => `L${x(p.dist_km)},${y(p.los_m - fr(p))}`).join(" ") + " Z";
    const fresnelLower = pts.map((p, i) => `${i ? "L" : "M"}${x(p.dist_km)},${y(p.los_m - fr(p))}`).join(" ");

    const first = pts[0], last = pts[pts.length - 1];
    const txTop = result.tx_h_asl ?? first.los_m;
    const rxTop = result.rx_h_asl ?? last.los_m;
    const txGround = result.tx_ground_asl ?? first.terrain_m ?? txTop;
    const rxGround = result.rx_ground_asl ?? last.terrain_m ?? rxTop;

    const ob = result.worst_obstacle;
    const obstacle = ob && Number.isFinite(ob.dist_km) && Number.isFinite(ob.terrain_m)
      ? { cx: x(ob.dist_km), cy: y(ob.terrain_m), ly: y(ob.los_m), ob } : null;

    return {
      W, iw, ih, base, x, y, xMax, yTicks, xTicks: niceTicks(0, xMax, Math.max(3, Math.floor(iw / 60))),
      terrainPaths, hasGaps, los, fresnel, fresnelLower,
      tx: { x: x(0), top: y(txTop), ground: y(txGround) },
      rx: { x: x(xMax), top: y(rxTop), ground: y(rxGround) },
      obstacle,
    };
  }, [pts, width, result]);

  if (!chart) {
    return <div ref={wrapRef} className={styles.chartEmpty}>אין נתוני פרופיל להצגה</div>;
  }

  const c = chart;
  return (
    <div ref={wrapRef} className={styles.chartWrap}>
      <svg width={c.W} height={H} viewBox={`0 0 ${c.W} ${H}`} className={styles.chartSvg}
           role="img" aria-label="פרופיל גובה לאורך הקישור">
        {/* grid + y axis */}
        {c.yTicks.map((t) => (
          <g key={`y${t}`}>
            <line x1={M.left} x2={M.left + c.iw} y1={c.y(t)} y2={c.y(t)} className={styles.chartGrid} />
            <text x={M.left - 4} y={c.y(t) + 3} textAnchor="end" className={styles.chartTick}>{fmtTick(t)}</text>
          </g>
        ))}
        {c.xTicks.map((t) => (
          <g key={`x${t}`}>
            <line x1={c.x(t)} x2={c.x(t)} y1={c.base} y2={c.base + 4} className={styles.chartAxis} />
            <text x={c.x(t)} y={c.base + 15} textAnchor="middle" className={styles.chartTick}>{fmtTick(t)}</text>
          </g>
        ))}
        <line x1={M.left} x2={M.left + c.iw} y1={c.base} y2={c.base} className={styles.chartAxis} />
        <line x1={M.left} x2={M.left} y1={M.top} y2={c.base} className={styles.chartAxis} />
        <text x={M.left + c.iw} y={H - 2} textAnchor="end" className={styles.chartUnit}>ק"מ</text>
        <text x={2} y={M.top - 3} className={styles.chartUnit}>מ' ASL</text>

        {/* Fresnel zone */}
        <path d={c.fresnel} className={styles.chartFresnel} />
        <path d={c.fresnelLower} className={styles.chartFresnelEdge} />

        {/* terrain */}
        {c.terrainPaths.map((d, i) => <path key={i} d={d} className={styles.chartTerrain} />)}

        {/* line of sight */}
        <path d={c.los} className={styles.chartLos} />

        {/* antennas */}
        <line x1={c.tx.x} x2={c.tx.x} y1={c.tx.ground} y2={c.tx.top} className={styles.chartMast} />
        <circle cx={c.tx.x} cy={c.tx.top} r={4} className={styles.chartTx}><title>משדר</title></circle>
        <line x1={c.rx.x} x2={c.rx.x} y1={c.rx.ground} y2={c.rx.top} className={styles.chartMast} />
        <circle cx={c.rx.x} cy={c.rx.top} r={4} className={styles.chartRx}><title>מקלט</title></circle>

        {/* worst obstacle */}
        {c.obstacle && (
          <g>
            <line x1={c.obstacle.cx} x2={c.obstacle.cx} y1={c.obstacle.cy} y2={c.obstacle.ly} className={styles.chartObLine} />
            <path d={`M${c.obstacle.cx},${c.obstacle.cy - 9} l5,8 l-10,0 Z`} className={styles.chartOb}>
              <title>{`מכשול עיקרי: ${Math.round(c.obstacle.ob.terrain_m)} מ' ב-${c.obstacle.ob.dist_km.toFixed(2)} ק"מ (ν=${c.obstacle.ob.nu?.toFixed?.(2) ?? "—"})`}</title>
            </path>
          </g>
        )}
      </svg>
      <div className={styles.chartLegend}>
        <span><i className={styles.lgTerrain} />טרן</span>
        <span><i className={styles.lgLos} />קו ראייה</span>
        <span><i className={styles.lgFresnel} />אזור פרנל 1</span>
        {c.obstacle && <span><i className={styles.lgOb} />מכשול עיקרי</span>}
        {c.hasGaps && <span className={styles.chartGapNote}>פערים = אין נתוני גובה</span>}
      </div>
    </div>
  );
}
