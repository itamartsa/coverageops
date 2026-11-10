/**
 * Role checks for the UI (mirror the backend's require_role("ADMIN", "OPERATOR")).
 * VIEWER is read-only: it never sees create / edit / delete / run controls.
 */
export const canWrite = (role?: string | null) => role === "ADMIN" || role === "OPERATOR";

/** Note shown in place of run buttons for read-only users. */
export const READ_ONLY_NOTE = "👁 משתמש צופה — צפייה בלבד, ללא הרשאה להריץ חישובים או לערוך";

/** Field reception: device-level data (live positions, device list, alerts, commands) — ADMIN/OPERATOR. */
export const canSeeDevices = canWrite;

export const isAdmin = (role?: string | null) => role === "ADMIN";

/** One-line reason shown to VIEWER instead of device-level field UI (spec 8.4 / 9.4). */
export const FIELD_AGGREGATE_ONLY_NOTE =
  "👁 תצוגה מצרפית בלבד — פרטי מכשירים, מיקומים והתראות זמינים למנהל ולמפעיל בלבד (פרטיות)";
