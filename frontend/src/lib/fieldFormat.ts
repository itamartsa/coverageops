/**
 * Field reception — formatting helpers (Hebrew relative time, dBm, percentages, Israel time).
 * Numbers that end up inside Hebrew text must be wrapped in <bdi dir="ltr"> by the caller
 * (`Num` in components/field/primitives.tsx) so a leading minus never jumps to the wrong side.
 */

const MINUS = "−";
/** Left-to-right isolate: keeps "−97 dBm" / "10.11 14:30" in order inside Hebrew text. */
const LRI = "\u2066", PDI = "\u2069";
const TZ = "Asia/Jerusalem";

/** "−91" (U+2212 minus) or "—". */
export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return (v < 0 && Number(s.replace(/,/g, "")) !== 0 ? MINUS : "") + s;
}

/** "−91 dBm" */
export const fmtDbm = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : `${LRI}${fmtNum(v, digits)} dBm${PDI}`;

/** "84.6%" */
export const fmtPct = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${fmtNum(v, digits)}%`;

/** Thousands separators ("52,410"). */
export const fmtInt = (v: number | null | undefined) =>
  v == null ? "—" : Math.round(v).toLocaleString("en-US");

export function parseIso(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "לפני 25 שנ׳" / "לפני 3 דק׳" / "לפני 2 שע׳" / "לפני 3 ימים". */
export function relTime(iso: string | Date | null | undefined, now: number = Date.now()): string {
  const d = iso instanceof Date ? iso : parseIso(iso);
  if (!d) return "—";
  const s = Math.max(0, Math.round((now - d.getTime()) / 1000));
  return `לפני ${durationText(s)}`;
}

/** "25 שנ׳" / "3 דק׳" / "2 שע׳" / "2:15 שע׳" / "3 ימים". */
export function durationText(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} שנ׳`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} דק׳`;
  const h = Math.floor(s / 3600);
  const mm = Math.round((s % 3600) / 60);
  if (h < 48) return mm && h < 10 ? `${h}:${String(mm).padStart(2, "0")} שע׳` : `${h} שע׳`;
  return `${Math.round(h / 24)} ימים`;
}

/** "14:30" in Israel time. */
export function fmtTime(iso: string | Date | null | undefined): string {
  const d = iso instanceof Date ? iso : parseIso(iso);
  if (!d) return "—";
  return d.toLocaleTimeString("he-IL", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
}

/** "11/11 14:30" in Israel time. */
export function fmtDateTime(iso: string | Date | null | undefined): string {
  const d = iso instanceof Date ? iso : parseIso(iso);
  if (!d) return "—";
  const date = d.toLocaleDateString("he-IL", { timeZone: TZ, day: "2-digit", month: "2-digit" });
  return `${LRI}${date} ${fmtTime(d)}${PDI}`;
}

/** "11/11" in Israel time. */
export function fmtDay(iso: string | Date | null | undefined): string {
  const d = iso instanceof Date ? iso : parseIso(iso);
  if (!d) return "—";
  return d.toLocaleDateString("he-IL", { timeZone: TZ, day: "2-digit", month: "2-digit" });
}

/** True when the timestamp is older than `minutes`. */
export function olderThan(iso: string | null | undefined, minutes: number, now = Date.now()): boolean {
  const d = parseIso(iso);
  return !!d && now - d.getTime() > minutes * 60_000;
}
