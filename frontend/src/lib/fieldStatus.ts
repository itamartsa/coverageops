/**
 * Field reception — presentation of the server's computed device status (spec 3.5, v2/v2.1),
 * command statuses, alert severities and network types. Labels follow the approved mockup.
 * The status itself is always computed by the server (`status`, `status_label_he`); this module
 * only adds the icon / tone / sentence around it.
 */
import { relTime, fmtTime } from "./fieldFormat";

export type DeviceStatus =
  | "active" | "degraded" | "paused" | "no_contact" | "offline" | "never_seen"
  | "withdrawn" | "revoked" | "deleted";

/** tone → CSS class in the field components (ok / deg / pause / nc / off / dead) */
export interface StatusLook { label: string; icon: string; tone: "ok" | "deg" | "pause" | "nc" | "off" | "dead" }

const LOOKS: Record<string, StatusLook> = {
  active:     { label: "פעיל",          icon: "●", tone: "ok" },
  degraded:   { label: "ניטור מוגבל",   icon: "◐", tone: "deg" },
  paused:     { label: "מושהה",         icon: "❚❚", tone: "pause" },
  no_contact: { label: "ללא קשר",       icon: "ⓘ", tone: "nc" },
  offline:    { label: "לא מקוון",      icon: "○", tone: "off" },
  never_seen: { label: "טרם התחבר",     icon: "◌", tone: "pause" },
  withdrawn:  { label: "ההסכמה בוטלה",  icon: "⊘", tone: "dead" },
  revoked:    { label: "בוטל",          icon: "⊘", tone: "dead" },
  deleted:    { label: "נמחק",          icon: "⊘", tone: "dead" },
};

export function statusLook(status: string): StatusLook {
  return LOOKS[status] ?? { label: status, icon: "?", tone: "pause" };
}

/**
 * The sentence shown in tables / the drawer, e.g. "מושהה ע״י המשתמש · נראה לאחרונה לפני 8 דק׳",
 * "ללא קשר · מושהה מאז 03:10", "לא מקוון · נראה לאחרונה לפני 47 דק׳".
 */
export function statusSentence(d: {
  status: string; monitoring_state?: string | null; status_since?: string | null;
  last_seen_at?: string | null; degraded_reasons?: { label_he: string }[] | string[] | null;
}, now = Date.now()): string {
  const look = statusLook(d.status);
  switch (d.status) {
    case "paused": {
      const who = d.monitoring_state === "paused_schedule" ? "מחוץ למשמרת" : "מושהה ע״י המשתמש";
      return `${who} · נראה לאחרונה ${relTime(d.last_seen_at, now)}`;
    }
    case "no_contact":
      return `ללא קשר · מושהה מאז ${fmtTime(d.status_since)} (${relTime(d.status_since, now).replace("לפני ", "")})`;
    case "offline":
      return `לא מקוון · נראה לאחרונה ${relTime(d.last_seen_at ?? d.status_since, now)}`;
    case "degraded": {
      const r = d.degraded_reasons?.[0];
      const first = typeof r === "string" ? degradedLabel(r) : r?.label_he;
      return first ? `ניטור מוגבל · ${first}` : "ניטור מוגבל";
    }
    default:
      return look.label;
  }
}

/** Mirror of field_status.DEGRADED_REASON_LABELS_HE for the list endpoint (which sends codes). */
export const DEGRADED_REASON_LABELS_HE: Record<string, string> = {
  location_permission_missing: "אין הרשאת מיקום",
  location_approximate_only: "מיקום משוער בלבד — אין נתוני תא",
  location_services_off: "שירותי המיקום כבויים",
  phone_state_missing: "אין הרשאת מצב טלפון",
  notifications_denied: "התראות חסומות",
  background_location_missing: "אין מיקום ברקע",
  battery_optimization_not_exempt: "אין פטור מהגבלת סוללה",
  no_cell_identity: "אין זהות תא",
  cell_info_frozen: "נתוני תא קפואים",
  no_location_fix: "אין קליטת מיקום",
  power_save_location: "חיסכון חשמל מגביל מיקום",
  low_battery_mode: "סוללה חלשה — קצב מופחת",
  queue_evicting: "אחסון מלא — דגימות נמחקות",
  repeated_gaps: "המערכת עוצרת את האפליקציה",
  upload_blocked: "העלאה חסומה",
};
export const degradedLabel = (code: string) => DEGRADED_REASON_LABELS_HE[code] ?? `סיבה לא מוכרת: ${code}`;

/** "5G NSA · עוגן LTE", "5G SA", "LTE", "3G", "ללא שירות". */
export function networkLabel(nt: string | null | undefined): string {
  switch (nt) {
    case "NR_NSA": return "5G NSA · עוגן LTE";
    case "NR_SA":  return "5G SA";
    case "LTE":    return "LTE";
    case "UMTS":   return "3G";
    case "GSM":    return "2G";
    case "NONE":   return "ללא שירות";
    default:       return "—";
  }
}

/** Why a sample is unclassified — "(3G)" / "(מצב טיסה)" / "(אין RSRP)". */
export function unclassifiedReason(nt: string | null | undefined, serviceState?: string | null): string {
  if (serviceState === "power_off") return "מצב טיסה";
  if (nt === "UMTS") return "3G";
  if (nt === "GSM") return "2G";
  return "אין RSRP";
}

export const COMMAND_TYPE_HE: Record<string, string> = { sample_now: "דגימה עכשיו", upload_now: "העלאה עכשיו" };

export interface CommandLook { label: string; icon: string; tone: "wait" | "ok" | "skip" | "exp" }
export function commandLook(status: string, paused: boolean): CommandLook {
  switch (status) {
    case "queued":
      return { label: paused ? "נשלחה · ממתינה למכשיר (מושהה — ללא זמן מובטח)" : "נשלחה · ממתינה למכשיר (עד ~5 דק׳)", icon: "⏳", tone: "wait" };
    case "delivered":      return { label: "נמסרה · ממתינה לאישור", icon: "⏳", tone: "wait" };
    case "done":           return { label: "בוצעה", icon: "✓", tone: "ok" };
    case "skipped_paused": return { label: "דולגה — המכשיר מושהה", icon: "↷", tone: "skip" };
    case "failed":         return { label: "נכשלה במכשיר", icon: "✕", tone: "exp" };
    case "expired":        return { label: "פג תוקף", icon: "⌛", tone: "exp" };
    case "cancelled":      return { label: "בוטלה", icon: "⊘", tone: "exp" };
    default:               return { label: status, icon: "·", tone: "exp" };
  }
}

export const SEVERITY_HE: Record<string, { label: string; icon: string; tone: "crit" | "warn" | "info" }> = {
  critical: { label: "קריטי", icon: "⛔", tone: "crit" },
  warning:  { label: "אזהרה", icon: "⚠", tone: "warn" },
  info:     { label: "מידע",  icon: "ⓘ", tone: "info" },
};

export const ALERT_STATUS_HE: Record<string, string> = { open: "פתוחה", acked: "אושרה", resolved: "נסגרה" };

export const LOCATION_MODE_HE: Record<string, string> = { precise: "מדויק", coarse: "משוער", off: "כבוי" };

/** Permission chips for the drawer: [label, ok?, value text]. */
export function permissionChips(p: Record<string, unknown> | null | undefined): { label: string; ok: boolean; text?: string }[] {
  if (!p) return [];
  const out: { label: string; ok: boolean; text?: string }[] = [];
  const loc = p.location;
  if (loc !== undefined) {
    const t = loc === "precise" ? "מדויק" : loc === "approximate" ? "משוער" : loc === true ? "יש" : "אין";
    out.push({ label: "מיקום", ok: loc === "precise" || loc === true, text: t });
  }
  const names: Record<string, string> = {
    background_location: "מיקום ברקע", phone_state: "מצב טלפון", notifications: "התראות",
    battery_optimization_exempt: "פטור מחיסכון סוללה",
  };
  for (const [k, label] of Object.entries(names)) {
    if (p[k] !== undefined && p[k] !== null) out.push({ label, ok: !!p[k] });
  }
  return out;
}
