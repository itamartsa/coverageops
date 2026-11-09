/**
 * Point-to-point link — shared constants & formatting (sidebar panel + map).
 */
import { DASH, fmtNum } from "../services/coverageFormat";

/** Signal level colours — same scale as the coverage overlays / cross-section line. */
export const LEVEL_COLORS: Record<string, string> = {
  excellent: "#00ff88", good: "#7dff6b", medium: "#ffe600",
  weak: "#ff8c00", marginal: "#ff3b5c", none: "#8a8f98",
};

export const LEVEL_HE: Record<string, string> = {
  excellent: "מצוין", good: "טוב", medium: "בינוני",
  weak: "חלש", marginal: "שולי", none: "אין קליטה",
};

export const levelColor = (level?: string | null) => LEVEL_COLORS[level ?? ""] ?? LEVEL_COLORS.none;
export const levelHe = (level?: string | null) => (level ? LEVEL_HE[level] ?? level : DASH);

/** "+12.3" / "−4.0" with a typographic minus, or "—". */
export function fmtSigned(v: number | null | undefined, digits = 1): string {
  const s = fmtNum(v, digits);
  if (s === DASH) return DASH;
  return (v as number) > 0 ? `+${s}` : s.replace("-", "−");
}

/** dB value with sign: "+6.0 dB", or "—". */
export const fmtDb = (v: number | null | undefined, digits = 1) => {
  const s = fmtSigned(v, digits);
  return s === DASH ? DASH : `${s} dB`;
};

export const fmtKm = (v: number | null | undefined) => {
  const s = fmtNum(v, (v ?? 0) < 10 ? 2 : 1);
  return s === DASH ? DASH : `${s} ק"מ`;
};

export const fmtDeg = (v: number | null | undefined) => {
  const s = fmtNum(v, 0);
  return s === DASH ? DASH : `${s}°`;
};

/** Hebrew names of the transmitter fields the server reports as estimated. */
export const ESTIMATED_FIELD_HE: Record<string, string> = {
  ant_height_m: "גובה אנטנה", ant_height: "גובה אנטנה", height: "גובה אנטנה",
  tx_power_dbm: "הספק שידור", tx_power: "הספק שידור",
  frequency_mhz: "תדר", frequency: "תדר",
};
export const estimatedFieldHe = (f: string) => ESTIMATED_FIELD_HE[f] ?? f;

export const BEST_SERVER_OPERATORS = [
  { key: "",          label: "כל המפעילים" },
  { key: "PHI",       label: "PHI (הוט/פרטנר)" },
  { key: "PELEPHONE", label: "פלאפון" },
  { key: "CELLCOM",   label: "סלקום" },
] as const;
