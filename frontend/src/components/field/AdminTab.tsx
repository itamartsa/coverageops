/**
 * "ניהול מכשירים" tab (spec 8.3 "ניהול"): ADMIN — groups & configuration, enrollment codes
 * (create → code + QR once, revoke), devices (move group / rename, revoke, remove with the ORG
 * anonymise rule), field audit log. OPERATOR — groups read-only.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiErrorText, fieldApi, type FieldDeviceRow, type FieldGroup } from "../../services/api";
import { fmtDateTime, fmtTime } from "../../lib/fieldFormat";
import { useGroups } from "../../lib/fieldQueries";
import GroupConfigForm, { scheduleText } from "./GroupConfigForm";
import EnrollDialog from "./EnrollDialog";
import DeviceAdminActions, { ORG_ANON_RULE_HE, PERSONAL_DELETE_RULE_HE } from "./DeviceAdminActions";
import { Dialog, ErrorState, Loading, Num, Ownership, StatusText } from "./primitives";
import s from "./field.module.css";
import t from "./FieldTabs.module.css";

const LOC_HE: Record<string, string> = { precise: "מדויק", coarse: "משוער", off: "כבוי" };

export default function AdminTab({ isAdmin, devices, onShowRequirements, now, enrollOpen, setEnrollOpen }: {
  isAdmin: boolean; devices: FieldDeviceRow[]; onShowRequirements: () => void; now: number;
  enrollOpen: boolean; setEnrollOpen: (v: boolean) => void;
}) {
  const qc = useQueryClient();
  const groupsQ = useGroups(true);
  const codesQ = useQuery({ queryKey: ["field", "enrollment-codes"], queryFn: () => fieldApi.enrollmentCodes(false), enabled: isAdmin });
  const auditQ = useQuery({ queryKey: ["field", "audit"], queryFn: () => fieldApi.audit({ limit: 30 }), enabled: isAdmin, refetchInterval: 60_000, refetchIntervalInBackground: false });
  const [editGroup, setEditGroup] = useState<FieldGroup | null | "new">(null);
  const [editDev, setEditDev] = useState<FieldDeviceRow | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const revokeCode = useMutation({
    mutationFn: (id: number) => fieldApi.revokeEnrollmentCode(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["field", "enrollment-codes"] }),
    onError: (e) => setErr(apiErrorText(e, "ביטול הקוד נכשל")),
  });
  const delGroup = useMutation({
    mutationFn: (id: number) => fieldApi.deleteGroup(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["field"] }),
    onError: (e) => setErr(apiErrorText(e, "מחיקת הקבוצה נכשלה")),
  });

  const groups = groupsQ.data ?? [];

  return (
    <div className={t.stack} data-testid="admin-tab">
      {err && <p className={s.errbox} role="alert">{err} <button className={s.btn} onClick={() => setErr(null)}>סגור</button></p>}
      <div className={t.stack}>
        <section className={s.card} aria-label="קבוצות ותצורה">
          <div className={s.cardH}>
            <h2>קבוצות ותצורה</h2>
            {!isAdmin && <span className={s.sub}>צפייה בלבד — עריכה למנהל</span>}
            {isAdmin && <div className={s.tools}><button className={s.btnPrimary} onClick={() => setEditGroup("new")}>+ קבוצה</button></div>}
          </div>
          {groupsQ.isLoading ? <Loading /> : groupsQ.error ? <ErrorState error={groupsQ.error} onRetry={() => groupsQ.refetch()} /> : (
            <div className={s.tw}>
              <table className={s.t}>
                <thead><tr><th scope="col">קבוצה</th><th scope="col" className={s.numCell}>מכשירים</th>
                  <th scope="col" className={s.numCell}>דגימה</th><th scope="col" className={s.numCell}>מרחק מינ׳</th>
                  <th scope="col" className={s.numCell}>העלאה</th><th scope="col" className={s.numCell}>בדיקה פעילה</th>
                  <th scope="col">מיקום</th><th scope="col">משמרות</th>{isAdmin && <th scope="col"><span className={s.srOnly}>פעולות</span></th>}</tr></thead>
                <tbody>
                  {groups.map((g) => {
                    const c = g.effective_config;
                    return (
                      <tr key={g.id}>
                        <th scope="row">{g.name}{g.is_default && <span className={s.chip} style={{ marginInlineStart: 6 }}>ברירת מחדל</span>}</th>
                        <td className={s.numCell}>{g.device_count}</td>
                        <td className={s.numCell}>{c.sampling_interval_s} ש׳</td>
                        <td className={s.numCell}>{c.min_distance_m} מ׳</td>
                        <td className={s.numCell}>{c.upload_interval_s} ש׳</td>
                        <td className={s.numCell}>{c.active_test_interval_s ? `${Math.round(c.active_test_interval_s / 60)} דק׳` : "כבוי"}</td>
                        <td>{LOC_HE[c.location_mode] ?? c.location_mode}</td>
                        <td style={{ whiteSpace: "normal", minWidth: 120 }}>{scheduleText(c.schedule)}</td>
                        {isAdmin && <td>
                          <button className={s.btn} onClick={() => setEditGroup(g)}>עריכה</button>{" "}
                          {!g.is_default && <button className={s.btnGhost} onClick={() => { setErr(null); delGroup.mutate(g.id); }}
                            title="אפשר למחוק רק קבוצה ללא מכשירים">מחק</button>}
                        </td>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className={s.note} style={{ margin: "10px 14px" }}>{groups[0]?.personal_note_he ?? ""}</p>
        </section>

        {isAdmin && (
          <section className={s.card} aria-label="קודי צירוף" data-testid="enrollment-codes">
            <div className={s.cardH}>
              <h2>קודי צירוף</h2>
              <span className={s.sub}>פעילים בלבד</span>
              <div className={s.tools}>
                <button className={s.btn} onClick={onShowRequirements} aria-label="דרישות מערכת ליחידות הקצה">ⓘ דרישות</button>
                <button className={s.btnPrimary} onClick={() => setEnrollOpen(true)}>+ קוד צירוף</button>
              </div>
            </div>
            {codesQ.isLoading ? <Loading /> : codesQ.error ? <ErrorState error={codesQ.error} onRetry={() => codesQ.refetch()} /> : (
              (codesQ.data?.length ?? 0) === 0 ? <div className={s.state}>אין קודים פעילים. צרו קוד כדי לצרף מכשיר.</div> : (
                <div className={s.tw}>
                  <table className={s.t}>
                    <thead><tr><th scope="col">קוד</th><th scope="col">תווית</th><th scope="col">קבוצה</th><th scope="col">בעלות</th>
                      <th scope="col" className={s.numCell}>שימושים</th><th scope="col">תוקף</th><th scope="col"><span className={s.srOnly}>פעולות</span></th></tr></thead>
                    <tbody>
                      {codesQ.data!.map((c) => (
                        <tr key={c.id}>
                          <td><Num>{`••••-••••-…${c.code_hint}`}</Num></td>
                          <td>{c.label ?? "—"}</td><td>{c.group ?? "ברירת מחדל"}</td><td><Ownership value={c.ownership} /></td>
                          <td className={s.numCell}>{c.use_count}/{c.max_uses}</td>
                          <td>{fmtDateTime(c.expires_at)}</td>
                          <td><button className={s.btnGhost} onClick={() => revokeCode.mutate(c.id)} disabled={revokeCode.isPending}>בטל</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            )}
            <p className={s.note} style={{ margin: "10px 14px" }}>הקוד עצמו מוצג פעם אחת ביצירה; בשרת נשמרים רק 4 התווים האחרונים ו-hash.</p>
          </section>
        )}
      </div>

      {isAdmin && (
        <section className={s.card} aria-label="ניהול מכשירים" data-testid="admin-devices">
          <div className={s.cardH}>
            <h2>מכשירים רשומים</h2>
            <span className={s.sub}>{devices.length} מכשירים · העברת קבוצה, שינוי שם, ביטול והסרה</span>
          </div>
          <div className={s.cardB}>
            <p className={s.warnbox} style={{ marginBottom: 6 }}><b>כלל ORG:</b> {ORG_ANON_RULE_HE}</p>
            <p className={s.note}>{PERSONAL_DELETE_RULE_HE}</p>
          </div>
          <div className={s.tw}>
            <table className={s.t}>
              <thead><tr><th scope="col">מכשיר</th><th scope="col">בעלות</th><th scope="col">קבוצה</th><th scope="col">סטטוס</th>
                <th scope="col">גרסה</th><th scope="col"><span className={s.srOnly}>פעולות</span></th></tr></thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.id}>
                    <th scope="row">{d.label ?? d.id.slice(0, 8)}</th>
                    <td><Ownership value={d.ownership} /></td><td>{d.group ?? "—"}</td>
                    <td><StatusText row={d} short /></td><td><Num>{d.app_version ?? "—"}</Num></td>
                    <td>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        <button className={s.btn} onClick={() => setEditDev(d)}>עריכה</button>
                        {d.status !== "withdrawn" && <DeviceAdminActions id={d.id} label={d.label ?? d.id.slice(0, 8)} ownership={d.ownership}
                          adminStatus={d.status === "revoked" ? "revoked" : "active"} />}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!devices.length && <div className={s.state}>אין מכשירים רשומים.</div>}
          </div>
        </section>
      )}

      {isAdmin && (
        <section className={s.card} aria-label="יומן ביקורת שטח">
          <div className={s.cardH}><h2>יומן ביקורת שטח</h2><span className={s.sub}>30 הרשומות האחרונות (FIELD_*)</span></div>
          {auditQ.isLoading ? <Loading /> : auditQ.error ? <ErrorState error={auditQ.error} /> : (
            <ul style={{ listStyle: "none", padding: "10px 14px", fontSize: 11.5, display: "flex", flexDirection: "column", gap: 4 }}>
              {(auditQ.data ?? []).length === 0 && <li className={s.muted}>אין רשומות.</li>}
              {(auditQ.data ?? []).map((e) => (
                <li key={e.id} className={s.note}>
                  <Num>{fmtTime(e.created_at)}</Num> · {e.username ?? "מכשיר"} · <code dir="ltr">{e.action}</code>
                  {e.detail?.device_id ? <> · מכשיר <Num>{String(e.detail.device_id).slice(0, 8)}</Num></> : null}
                  {e.detail?.type ? <> · {String(e.detail.type)}</> : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {editGroup && <GroupConfigForm group={editGroup === "new" ? null : editGroup} onClose={() => setEditGroup(null)} />}
      {enrollOpen && <EnrollDialog groups={groups} onClose={() => setEnrollOpen(false)} onShowRequirements={onShowRequirements} />}
      {editDev && <DeviceEditDialog dev={editDev} groups={groups} onClose={() => setEditDev(null)} />}
    </div>
  );
}

function DeviceEditDialog({ dev, groups, onClose }: { dev: FieldDeviceRow; groups: FieldGroup[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [label, setLabel] = useState(dev.label ?? "");
  const [groupId, setGroupId] = useState(dev.group_id != null ? String(dev.group_id) : "");
  const [err, setErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => fieldApi.updateDevice(dev.id, { label: label.trim() || null, group_id: groupId ? Number(groupId) : null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["field"] }); onClose(); },
    onError: (e) => setErr(apiErrorText(e, "השמירה נכשלה")),
  });
  return (
    <Dialog title={`עריכת מכשיר — ${dev.label ?? dev.id.slice(0, 8)}`} onClose={onClose} labelledBy="dev-edit-title" footer={<>
      <button className={s.btnPrimary} onClick={() => save.mutate()} disabled={save.isPending}>שמור</button>
      <button className={s.btnGhost} onClick={onClose}>ביטול</button>
    </>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>שם תצוגה
          <input className={s.input} value={label} maxLength={64} onChange={(e) => setLabel(e.target.value)} /></label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>קבוצה
          <select className={s.select} value={groupId} onChange={(e) => setGroupId(e.target.value)}>
            {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select></label>
        <p className={s.note}>העברת קבוצה מעלה את גרסת התצורה של המכשיר; היא תוחל ב-heartbeat הבא.</p>
        {err && <p className={s.errbox} role="alert">{err}</p>}
      </div>
    </Dialog>
  );
}
