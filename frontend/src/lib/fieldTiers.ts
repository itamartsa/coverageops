/**
 * Field reception — reception tiers: the ONE place in the frontend that maps a tier to its
 * colour, Hebrew label and bars glyph.
 *
 * Mirrors the server's single source of truth (backend/app/services/field_quality.py:
 * TIERS / UNCLASSIFIED / classify_tier), which is also pinned by the shared vectors file
 * backend/tests/fixtures/field_tier_vectors.json. The e2e spec `field-tiers.spec.ts` checks
 * that this module agrees with GET /api/field/meta/tiers and with every vector in that file.
 *
 * Tiers are half-open on RSRP (LTE / NR_NSA LTE anchor) or SS-RSRP (NR_SA):
 *   4 excellent  x ≥ −85 · 3 good −95 ≤ x < −85 · 2 medium −105 ≤ x < −95 ·
 *   1 weak −115 ≤ x < −105 · 0 marginal x < −115 or out_of_service / emergency_only.
 *   null = unclassified (power_off, or in service without RSRP — e.g. 2G/3G).
 * Colour is never the only cue: every tier also carries a label and a 0–4 bars glyph.
 */

export type TierValue = 0 | 1 | 2 | 3 | 4;
export type TierKey = "excellent" | "good" | "medium" | "weak" | "marginal" | "unclassified";

export interface TierDef {
  tier: TierValue | null;
  key: TierKey;
  label_he: string;
  /** Lower bound (inclusive) in dBm; null for marginal (open below) and unclassified */
  min_dbm: number | null;
  /** CSS custom property in index.css */
  token: string;
  /** Same colour as a literal, for canvas / Leaflet / SVG fills */
  color: string;
  /** Number of filled bars in the glyph (0–4); unclassified shows an empty glyph */
  bars: number;
}

export const TIERS: Record<TierValue, TierDef> = {
  4: { tier: 4, key: "excellent", label_he: "מצוין",   min_dbm: -85,  token: "--cov-excellent", color: "#00ff88", bars: 4 },
  3: { tier: 3, key: "good",      label_he: "טוב",     min_dbm: -95,  token: "--cov-good",      color: "#7dff6b", bars: 3 },
  2: { tier: 2, key: "medium",    label_he: "בינוני",  min_dbm: -105, token: "--cov-medium",    color: "#ffe600", bars: 2 },
  1: { tier: 1, key: "weak",      label_he: "חלש",     min_dbm: -115, token: "--cov-weak",      color: "#ff8c00", bars: 1 },
  0: { tier: 0, key: "marginal",  label_he: "שולי/אין", min_dbm: null, token: "--cov-marginal",  color: "#ff3b5c", bars: 0 },
};

export const UNCLASSIFIED: TierDef = {
  tier: null, key: "unclassified", label_he: "לא מסווג", min_dbm: null, token: "--cov-unk", color: "#5a7a9a", bars: 0,
};

/** Tiers best → worst (legend order). */
export const TIER_ORDER: TierValue[] = [4, 3, 2, 1, 0];

/** Name of the measured metric — every legend that shows tiers names it. */
export const TIER_MEASURE_HE = "RSRP dBm";

export const OOS_STATES = ["out_of_service", "emergency_only"];
const RSRP_TECHS = ["LTE", "NR_NSA", "NR_SA"];

/** Pure mirror of field_quality.classify_tier(network_type, rsrp_dbm, service_state). */
export function classifyTier(
  networkType: string | null | undefined,
  rsrpDbm: number | null | undefined,
  serviceState: string | null | undefined,
): TierValue | null {
  if (serviceState && OOS_STATES.includes(serviceState)) return 0;
  if (serviceState === "power_off") return null;
  if (!networkType || !RSRP_TECHS.includes(networkType) || rsrpDbm == null) return null;
  const x = Number(rsrpDbm);
  if (x >= -85) return 4;
  if (x >= -95) return 3;
  if (x >= -105) return 2;
  if (x >= -115) return 1;
  return 0;
}

/** Tier of a bare RSRP value (heat-map P5 etc.) — no service-state information. */
export function tierOfDbm(dbm: number | null | undefined): TierValue | null {
  return dbm == null ? null : classifyTier("LTE", dbm, "in_service");
}

export function tierDef(t: number | null | undefined): TierDef {
  return t != null && t >= 0 && t <= 4 ? TIERS[t as TierValue] : UNCLASSIFIED;
}

/** `var(--cov-good)` etc. — for CSS. */
export const tierColorVar = (t: number | null | undefined) => `var(${tierDef(t).token})`;

/** Threshold text of a tier, e.g. "−95…−85" / "≥ −85" / "< −115". Uses U+2212 minus. */
export function tierRangeText(t: TierValue): string {
  const m = (v: number) => `−${Math.abs(v)}`;
  if (t === 4) return `≥ ${m(-85)}`;
  if (t === 0) return `< ${m(-115)}`;
  const lo = TIERS[t].min_dbm!;
  const hi = TIERS[(t + 1) as TierValue].min_dbm!;
  return `${m(lo)}…${m(hi)}`;
}

/**
 * Bars glyph as an SVG string (Leaflet divIcons, canvas tooltips). 4 bars of rising height;
 * `filled` of them drawn in `fill`, the rest as outlines. `empty` (unclassified) = all outlines.
 */
export function barsSvg(filled: number, opts: { size?: number; fill?: string; dim?: string; title?: string } = {}): string {
  const size = opts.size ?? 14;
  const fill = opts.fill ?? "currentColor";
  const dim = opts.dim ?? "rgba(127,155,184,.45)";
  const w = 14, h = 11;
  const bars = [0, 1, 2, 3].map((i) => {
    const bh = 3 + i * 2.6;
    const x = 0.5 + i * 3.5;
    const y = h - bh;
    const on = i < filled;
    return `<rect x="${x}" y="${y.toFixed(1)}" width="2.6" height="${bh.toFixed(1)}" rx="0.5" `
      + (on ? `fill="${fill}"` : `fill="none" stroke="${dim}" stroke-width="0.8"`) + "/>";
  }).join("");
  const title = opts.title ? `<title>${opts.title}</title>` : "";
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${size}" height="${Math.round(size * h / w)}" aria-hidden="${opts.title ? "false" : "true"}">${title}${bars}</svg>`;
}
