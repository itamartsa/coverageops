/**
 * Remote commands for one device (spec 8.3 "פקודות", contract v2.1 rule 2):
 *  - "דגימה עכשיו" / "העלאה עכשיו" (ADMIN/OPERATOR), delivered with the next heartbeat;
 *  - status list of the last commands, polled every 10 s while one is still open;
 *  - paused devices never execute commands (skipped_paused). PERSONAL paused → buttons disabled
 *    with the reason; ORG paused → allowed, with the warning that it will be skipped unless the
 *    device resumes, and that there is no delivery bound while paused;
 *  - a second click while one is pending → the server's 409 is shown, no duplicate is created.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiErrorText, fieldApi, type FieldDeviceDetail } from "../../services/api";
import { COMMAND_TYPE_HE, commandLook } from "../../lib/fieldStatus";
import { fmtTime, relTime } from "../../lib/fieldFormat";
import { POLL } from "../../lib/fieldQueries";
import s from "./field.module.css";
import d from "./DeviceDrawer.module.css";

const OPEN = ["queued", "delivered"];

export default function DeviceCommands({ device, now }: { device: FieldDeviceDetail; now: number }) {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<{ kind: "ok" | "err" | "warn"; text: string } | null>(null);
  const cmdsQ = useQuery({
    queryKey: ["field", "commands", device.id],
    queryFn: () => fieldApi.deviceCommands(device.id, 10),
    initialData: device.commands,
    refetchInterval: (q) => ((q.state.data ?? []).some((c) => OPEN.includes(c.status)) ? POLL.commands : false),
    refetchIntervalInBackground: false,
  });
  const issue = useMutation({
    mutationFn: (type: "sample_now" | "upload_now") => fieldApi.issueCommand(device.id, type),
    onSuccess: (r) => {
      setMsg(r.warning_he ? { kind: "warn", text: r.warning_he } : { kind: "ok", text: `${COMMAND_TYPE_HE[r.type]} — נשלחה, ממתינה ל-heartbeat הבא` });
      qc.invalidateQueries({ queryKey: ["field", "commands", device.id] });
      qc.invalidateQueries({ queryKey: ["field", "devices"] });
    },
    onError: (e: any) => {
      const code = e?.response?.data?.detail_code;
      setMsg({ kind: "err", text: code === "command_pending" ? "כבר קיימת פקודה פתוחה מסוג זה — לא נוצרה פקודה נוספת" : apiErrorText(e, "שליחת הפקודה נכשלה") });
    },
  });
  const cancel = useMutation({
    mutationFn: (id: string) => fieldApi.cancelCommand(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["field", "commands", device.id] }),
    onError: (e) => setMsg({ kind: "err", text: apiErrorText(e, "ביטול נכשל") }),
  });

  const paused = device.status === "paused" || device.status === "no_contact";
  const inactive = device.admin_status !== "active";
  const personalPaused = paused && device.ownership === "PERSONAL";
  const disabled = inactive || personalPaused || issue.isPending;
  const cmds = (cmdsQ.data ?? []).slice(0, 5);
  const openTypes = new Set(cmds.filter((c) => OPEN.includes(c.status)).map((c) => c.type));

  return (
    <div className={d.sec} data-testid="device-commands">
      <h3>פקודות למכשיר</h3>
      {inactive ? (
        <p className={s.note}>המכשיר {device.status_label_he} — אין פקודות.</p>
      ) : (
        <>
          <div className={d.act}>
            {(["sample_now", "upload_now"] as const).map((t) => (
              <button key={t} className={t === "sample_now" ? s.btnPrimary : s.btn} disabled={disabled}
                onClick={() => { setMsg(null); issue.mutate(t); }}
                title={openTypes.has(t) ? "כבר יש פקודה פתוחה מסוג זה" : undefined}>
                {t === "sample_now" ? "⟳ " : "⇪ "}{COMMAND_TYPE_HE[t]}
              </button>
            ))}
          </div>
          {personalPaused && (
            <p className={s.note} style={{ marginTop: 6 }}>מכשיר אישי מושהה — פקודות לא מבוצעות, ולכן הכפתורים מושבתים.</p>
          )}
          {paused && !personalPaused && (
            <p className={s.warnbox} style={{ marginTop: 6 }}>
              <b>המכשיר מושהה:</b> פקודות אינן מבוצעות במצב מושהה (יסומנו "דולגה"), ואין זמן מסירה מובטח עד שיחזור לפעילות.
            </p>
          )}
          {!paused && <p className={s.note} style={{ marginTop: 6 }}>הפקודה נמסרת עם ה-heartbeat הבא של המכשיר — עד ~5 דק׳ במצב פעיל.</p>}
        </>
      )}
      {msg && (
        <p role="status" className={msg.kind === "err" ? s.errbox : msg.kind === "warn" ? s.warnbox : s.infobox} style={{ marginTop: 6 }}>
          {msg.text}
        </p>
      )}
      <ul className={d.cmds} aria-label="פקודות אחרונות">
        {cmds.length === 0 && <li className={s.muted}>לא נשלחו פקודות למכשיר.</li>}
        {cmds.map((c) => {
          const look = commandLook(c.status, paused);
          return (
            <li key={c.id} data-command-status={c.status}>
              <span className={`${d.cs} ${d[`cs-${look.tone}`]}`}>{look.icon} {look.label}</span>
              <b>{COMMAND_TYPE_HE[c.type] ?? c.type}</b>
              <span className={s.muted}>
                {fmtTime(c.issued_at)} · {String(c.issued_by ?? "—")} · {relTime(c.issued_at, now)}
                {c.status === "queued" && (
                  <> · <button className={s.lnk} onClick={() => cancel.mutate(c.id)} disabled={cancel.isPending}>בטל</button></>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      <p className={s.note}>כל פקודה נרשמת ביומן הפעילות עם שם המנפיק (FIELD_COMMAND_ISSUED).</p>
    </div>
  );
}
