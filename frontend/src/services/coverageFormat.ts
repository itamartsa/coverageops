/**
 * CoverageOps – shared formatting helpers for coverage results.
 * All helpers tolerate null/undefined (legacy rows) and return "—".
 */
import type { CoverageResult } from "./api";
import type { MapLayer } from "../store/useStore";

export const DASH = "—";

/** Number → fixed string, or "—" when missing / not finite. */
export function fmtNum(v: number | null | undefined, digits = 1): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : DASH;
}

/** Percentage with "%" suffix, or "—". */
export function fmtPct(v: number | null | undefined, digits = 1): string {
  const s = fmtNum(v, digits);
  return s === DASH ? DASH : `${s}%`;
}

/** dBm value using a typographic minus, e.g. −90 dBm. */
export function fmtDbm(v: number | null | undefined, digits = 0): string {
  const s = fmtNum(v, digits);
  return s === DASH ? DASH : `${s.replace("-", "−")} dBm`;
}

/** Label for a percentage whose threshold is unknown (legacy rows/reports). */
export const LEGACY_PCT_LABEL = "אות כלשהו (תוצאה ישנה)";

/** True when the threshold that came WITH a result/report is missing (legacy data). */
export function isLegacyThreshold(threshold: number | null | undefined): boolean {
  return !(typeof threshold === "number" && Number.isFinite(threshold));
}

/** Threshold label for a percentage: "≥ −90 dBm", or the legacy label when unknown. */
export function fmtThresholdLabel(threshold: number | null | undefined): string {
  return isLegacyThreshold(threshold) ? LEGACY_PCT_LABEL : `≥ ${fmtDbm(threshold)}`;
}

/**
 * "72% ≥ −90 dBm". `threshold` MUST be the threshold_dbm that came with the
 * result/report — never the site's current rx_threshold.
 * When unknown: "72% אות כלשהו (תוצאה ישנה)", or just "72%" with `legacyLabel: false`.
 */
export function fmtCoveredVsThreshold(
  pct: number | null | undefined,
  threshold: number | null | undefined,
  opts: { legacyLabel?: boolean } = {},
): string {
  const p = fmtPct(pct);
  if (p === DASH) return DASH;
  if (isLegacyThreshold(threshold)) return opts.legacyLabel === false ? p : `${p} ${LEGACY_PCT_LABEL}`;
  return `${p} ≥ ${fmtDbm(threshold)}`;
}

export type DemStatus = "real" | "simulated" | "unknown";

/** Classifies a dem_source: "terrarium" = real DEM, anything else known = simulated. */
export function demStatus(src: string | null | undefined): DemStatus {
  if (src == null || src === "") return "unknown";
  return src === "terrarium" ? "real" : "simulated";
}

export function demLabel(src: string | null | undefined): string {
  switch (demStatus(src)) {
    case "real":      return "DEM אמיתי";
    case "simulated": return "סימולציה";
    default:          return DASH;
  }
}

export const SIMULATED_TERRAIN_WARNING =
  "התוצאה מבוססת על טרן מדומה (סימולציה) ולא על נתוני גובה אמיתיים (DEM) — יש להתייחס לערכים כהערכה בלבד.";

/** Server warnings; if none were sent and DEM is not real, a client-side simulated-terrain warning. */
export function collectWarnings(
  warnings: string[] | null | undefined,
  demSource: string | null | undefined,
): string[] {
  const list = (warnings ?? []).filter((w) => typeof w === "string" && w.trim());
  // Client-side fallback only when the server sent no warnings (avoids duplicates)
  if (!list.length && demStatus(demSource) === "simulated") list.push(SIMULATED_TERRAIN_WARNING);
  return list;
}

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Builds a layer-manager entry for a coverage result. */
export function layerFromResult(result: CoverageResult, siteName: string): MapLayer {
  const hasBbox = isNum(result.sw_lat) && isNum(result.sw_lon)
               && isNum(result.ne_lat) && isNum(result.ne_lon);
  return {
    id:           `coverage-${result.id}`,
    name:         `${siteName} — ${result.mode}`,
    type:         "coverage",
    visible:      true,
    resultId:     result.id,
    bbox:         hasBbox
      ? { sw_lat: result.sw_lat!, sw_lon: result.sw_lon!, ne_lat: result.ne_lat!, ne_lon: result.ne_lon! }
      : undefined,
    mode:         result.mode,
    coveredPct:   isNum(result.covered_pct) ? result.covered_pct : undefined,
    thresholdDbm: result.threshold_dbm ?? null,
    demSource:    result.dem_source ?? null,
    siteName,
    createdAt:    result.created_at,
  };
}

/**
 * Runs `fn` over `items` with at most `limit` calls in flight.
 * Resolves (never rejects) with per-item settled results in input order.
 */
export async function runWithConcurrency<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const results: PromiseSettledResult<R>[] = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      try {
        results[i] = { status: "fulfilled", value: await fn(items[i], i) };
      } catch (reason) {
        results[i] = { status: "rejected", reason };
      }
    }
  };
  const n = Math.max(1, Math.min(Math.floor(limit) || 1, items.length));
  await Promise.all(Array.from({ length: n }, worker));
  return results;
}
