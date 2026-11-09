/**
 * Role checks for the UI (mirror the backend's require_role("ADMIN", "OPERATOR")).
 * VIEWER is read-only: it never sees create / edit / delete / run controls.
 */
export const canWrite = (role?: string | null) => role === "ADMIN" || role === "OPERATOR";

/** Note shown in place of run buttons for read-only users. */
export const READ_ONLY_NOTE = "👁 משתמש צופה — צפייה בלבד, ללא הרשאה להריץ חישובים או לערוך";
