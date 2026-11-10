/**
 * Minimal linear scales + tick generation for the hand-rolled SVG charts (no chart library —
 * spec 8.5). Charts are drawn LTR (time flows left → right) inside the RTL page.
 */

export interface Linear { (v: number): number; domain: [number, number]; range: [number, number]; invert(px: number): number }

export function linear(domain: [number, number], range: [number, number]): Linear {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
  const f = ((v: number) => r0 + (v - d0) * k) as Linear;
  f.domain = domain;
  f.range = range;
  f.invert = (px: number) => (k === 0 ? d0 : d0 + (px - r0) / k);
  return f;
}

/** "Nice" tick values covering [lo, hi] with about `count` ticks. */
export function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [];
  if (lo === hi) return [lo];
  const raw = (hi - lo) / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

const HOUR = 3600_000;
const DAY = 24 * HOUR;

/** Time ticks (ms) at whole hours / days in Israel time, about `count` of them. */
export function timeTicks(t0: number, t1: number, count = 6): number[] {
  const span = t1 - t0;
  const steps = [15 * 60_000, 30 * 60_000, HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY];
  const step = steps.find((s) => span / s <= count) ?? 7 * DAY;
  // Israel offset (DST-aware) at t0, so ticks fall on local whole hours / midnights
  const offMin = israelOffsetMin(new Date(t0));
  const off = offMin * 60_000;
  const first = Math.ceil((t0 + off) / step) * step - off;
  const out: number[] = [];
  for (let t = first; t <= t1; t += step) out.push(t);
  return out;
}

/** Minutes east of UTC for Asia/Jerusalem at `d`. */
export function israelOffsetMin(d: Date): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Jerusalem", hour12: false, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(d);
  const g = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  const asUtc = Date.UTC(g("year"), g("month") - 1, g("day"), g("hour") % 24, g("minute"), g("second"));
  // whole minutes; the sub-second part of d must not shift the offset
  return Math.round((asUtc - Math.floor(d.getTime() / 1000) * 1000) / 60_000);
}

/** Axis label of a time tick: "14:00" within a day, "11/11" across days. */
export function timeTickLabel(t: number, span: number): string {
  const d = new Date(t);
  if (span > 2 * DAY) {
    return d.toLocaleDateString("he-IL", { timeZone: "Asia/Jerusalem", day: "2-digit", month: "2-digit" });
  }
  return d.toLocaleTimeString("he-IL", { timeZone: "Asia/Jerusalem", hour: "2-digit", minute: "2-digit", hour12: false });
}

/** "−95" with U+2212 for SVG axis text. */
export const axisNum = (v: number) => (v < 0 ? `−${Math.abs(v)}` : String(v));
