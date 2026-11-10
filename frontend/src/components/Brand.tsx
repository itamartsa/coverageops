/**
 * COVIRADYX brand: the antenna mark + wordmark (top bars), and the full logo (login).
 * The wordmark is live text (crisp at any size, selectable, screen-reader friendly).
 */
import mark from "../assets/brand/coviradyx-mark.png";
import fullLogo from "../assets/brand/coviradyx-logo.webp";

export const PRODUCT_NAME = "COVIRADYX";
export const PRODUCT_TAGLINE = "Mission-Critical Connectivity Intelligence";

export function BrandMark({ size = 30, className }: { size?: number; className?: string }) {
  return (
    <span className={className} style={{ display: "inline-flex", alignItems: "center", gap: Math.round(size * 0.3) }}
      aria-label={PRODUCT_NAME} role="img" data-testid="brand-mark">
      <img src={mark} width={size} height={size} alt="" aria-hidden="true" style={{ display: "block", flexShrink: 0 }} />
      <span className="wordmark" aria-hidden="true" style={{ fontSize: Math.round(size * 0.62) }}>
        COVIRA<b>DYX</b>
      </span>
    </span>
  );
}

export function BrandLogo({ width = 340, className }: { width?: number; className?: string }) {
  return (
    <img src={fullLogo} className={className} width={width} height={Math.round(width * 366 / 1100)}
      alt={`${PRODUCT_NAME} — ${PRODUCT_TAGLINE}`} data-testid="brand-logo"
      style={{ display: "block", maxWidth: "100%", height: "auto" }} />
  );
}
