/**
 * Small presentational building blocks shared by the field-reception components.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { apiErrorText } from "../../services/api";
import { barsSvg, tierDef, tierColorVar } from "../../lib/fieldTiers";
import { fmtDbm, fmtNum } from "../../lib/fieldFormat";
import { statusLook, statusSentence, unclassifiedReason } from "../../lib/fieldStatus";
import s from "./field.module.css";

/** A number / dBm value isolated as LTR inside RTL text (no jumping minus signs). */
export function Num({ children, className }: { children: ReactNode; className?: string }) {
  return <bdi dir="ltr" className={`${s.num} ${className ?? ""}`}>{children}</bdi>;
}

/** 0–4 bars glyph (SVG). `empty` = unclassified. */
export function Bars({ bars, size = 14, empty = false, color }: { bars: number; size?: number; empty?: boolean; color?: string }) {
  return (
    <span
      aria-hidden="true"
      style={{ display: "inline-flex", color }}
      dangerouslySetInnerHTML={{ __html: barsSvg(empty ? 0 : bars, { size }) }}
    />
  );
}

/**
 * Tier badge: colour + bars glyph + Hebrew label (+ optional dBm). Never colour alone.
 * `stale` greys it out with "נתוני תא ישנים"; `unclassified` explains why (3G / airplane mode).
 */
export function TierBadge({ tier, dbm, stale, networkType, serviceState, basis, compact }: {
  tier: number | null | undefined; dbm?: number | null; stale?: boolean;
  networkType?: string | null; serviceState?: string | null; basis?: string | null; compact?: boolean;
}) {
  const def = tierDef(tier);
  const cls = stale ? s["tb-stale"] : s[`tb-${def.key}`];
  let label = def.label_he;
  if (def.tier == null && networkType) label = `לא מסווג (${unclassifiedReason(networkType, serviceState)})`;
  const title = `${stale ? "נתוני תא ישנים — " : ""}רמת קליטה: ${label}${dbm != null ? ` · ${fmtDbm(dbm)}` : ""}`
    + (basis ? ` · ${basis}` : "");
  return (
    <span className={`${s.tb} ${cls}`} title={title} data-tier={def.tier ?? "null"}>
      <Bars bars={def.bars} empty={def.tier == null} />
      <span>{label}</span>
      {!compact && dbm != null && <bdi dir="ltr">{fmtNum(dbm)}</bdi>}
      {stale && <span style={{ fontSize: 10 }}>· ישן</span>}
    </span>
  );
}

export function Ownership({ value }: { value: string }) {
  return <span className={`${s.own} ${s[`own-${value}`] ?? ""}`}>{value}</span>;
}

export function StatusText({ row, short }: { row: Parameters<typeof statusSentence>[0]; short?: boolean }) {
  const look = statusLook(row.status);
  return (
    <span className={`${s.st} ${s[`st-${look.tone}`]}`} data-status={row.status}>
      <i aria-hidden="true">{look.icon}</i>
      <span>{short ? look.label : statusSentence(row)}</span>
    </span>
  );
}

export function Battery({ pct, charging }: { pct: number | null | undefined; charging?: boolean | null }) {
  if (pct == null) return <span className={s.muted}>—</span>;
  const cls = pct <= 15 ? s.btLow : pct <= 35 ? s.btMid : "";
  return (
    <span className={`${s.bt} ${cls}`} title={`סוללה ${pct}%${charging ? " · בטעינה" : ""}`}>
      <span className={s.btBody} aria-hidden="true"><span style={{ width: `${Math.max(4, pct)}%` }} /></span>
      <bdi dir="ltr">{pct}%</bdi>
      {charging && <span aria-label="בטעינה">⚡</span>}
    </span>
  );
}

/** Colour swatch with bars glyph for legends. */
export function TierSwatch({ tier }: { tier: number | null }) {
  const def = tierDef(tier);
  return (
    <span className={s.lgSw} style={{ background: tierColorVar(tier) }} aria-hidden="true">
      <span style={{ display: "inline-flex", color: "#0a0e14" }}
        dangerouslySetInnerHTML={{ __html: barsSvg(def.bars, { size: 11, fill: "#0a0e14", dim: "rgba(10,14,20,.6)" }) }} />
    </span>
  );
}

// ── Query states ─────────────────────────────────────────────────────────
export function Loading({ lines = 3, label = "טוען…" }: { lines?: number; label?: string }) {
  return (
    <div className={s.state} role="status" aria-live="polite" aria-label={label}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className={s.skel} style={{ height: 14, width: `${90 - i * 14}%` }} />
      ))}
    </div>
  );
}

export function ErrorState({ error, onRetry, what }: { error: unknown; onRetry?: () => void; what?: string }) {
  const status = (error as any)?.response?.status;
  if (status === 403) {
    return (
      <div className={s.state} role="alert">
        <span>🔒 {what ? `${what}: ` : ""}אין הרשאה לנתונים אלה בתפקיד שלך.</span>
      </div>
    );
  }
  return (
    <div className={s.cardB}>
      <div className={s.errbox} role="alert">
        <span>⚠ {what ? `${what}: ` : ""}{apiErrorText(error, "שגיאה בטעינת הנתונים")}</span>
        {onRetry && <button className={s.btn} onClick={onRetry}>נסה שוב</button>}
      </div>
    </div>
  );
}

// ── Modal dialog ─────────────────────────────────────────────────────────
export function Dialog({ title, onClose, children, footer, wide, labelledBy }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean; labelledBy?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = labelledBy ?? "dlg-title";
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    el?.querySelector<HTMLElement>("button, [href], input, select, textarea")?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); }
      if (e.key === "Tab" && el) {
        const f = Array.from(el.querySelectorAll<HTMLElement>(
          "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea, [tabindex]:not([tabindex='-1'])"));
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [onClose]);
  return (
    <div className="field-dlg-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} className={`field-dlg ${wide ? "field-dlg-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="field-dlg-h">
          <h2 id={titleId}>{title}</h2>
          <button className={s.btn} onClick={onClose} aria-label="סגור">✕</button>
        </div>
        <div className="field-dlg-b">{children}</div>
        {footer && <div className="field-dlg-f">{footer}</div>}
      </div>
    </div>
  );
}
