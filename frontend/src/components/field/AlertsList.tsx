/**
 * Alerts (spec 7, 8.3 "התראות") — ADMIN/OPERATOR. Severity as icon + text (never colour alone),
 * type, device / area, opened, duration, ×occurrences, status; acknowledge / close.
 * A manually closed alert whose condition still holds re-opens after the 10-minute cooldown.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiErrorText, fieldApi, type FieldAlert } from "../../services/api";
import { ALERT_STATUS_HE, SEVERITY_HE } from "../../lib/fieldStatus";
import { durationText, fmtTime, relTime } from "../../lib/fieldFormat";
import { POLL } from "../../lib/fieldQueries";
import { ErrorState, Loading } from "./primitives";
import s from "./field.module.css";
import a from "./AlertsList.module.css";

const SEV_ORDER = { critical: 0, warning: 1, info: 2 } as const;

export default function AlertsList({ preview, onOpenDevice, onShowAll, now }: {
  preview?: number; onOpenDevice?: (id: string) => void; onShowAll?: () => void; now: number;
}) {
  const qc = useQueryClient();
  const [status, setStatus] = useState(preview ? "open" : "open,acked");
  const [type, setType] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["field", "alerts", "list", status],
    queryFn: () => fieldApi.alerts({ status, limit: 500 }),
    refetchInterval: POLL.alerts, refetchIntervalInBackground: false,
  });
  const act = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: "ack" | "resolve" }) =>
      kind === "ack" ? fieldApi.ackAlert(id) : fieldApi.resolveAlert(id),
    onSuccess: () => { setErr(null); qc.invalidateQueries({ queryKey: ["field", "alerts"] }); qc.invalidateQueries({ queryKey: ["field", "devices"] }); },
    onError: (e) => setErr(apiErrorText(e, "הפעולה נכשלה")),
  });

  const all = q.data ?? [];
  const types = Array.from(new Map(all.map((x) => [x.type, x.type_label_he])).entries());
  let rows = all.filter((x) => !type || x.type === type);
  rows = [...rows].sort((x, y) => (preview ? 0 : SEV_ORDER[x.severity] - SEV_ORDER[y.severity])
    || Date.parse(y.opened_at ?? "") - Date.parse(x.opened_at ?? ""));
  if (preview) rows = rows.slice(0, preview);
  const nOpen = all.filter((x) => x.status === "open").length;
  const nAcked = all.filter((x) => x.status === "acked").length;

  return (
    <section className={s.card} aria-label={preview ? "התראות אחרונות" : "התראות"} data-testid={preview ? "alerts-preview" : "alerts-list"}>
      <div className={s.cardH}>
        <h2>{preview ? "התראות אחרונות" : "התראות"}</h2>
        <span className={s.sub}>{nOpen} פתוחות{nAcked ? ` · ${nAcked} אושרו` : ""}{preview ? "" : " · ממוין לפי חומרה"}</span>
        <div className={s.tools}>
          {!preview && <>
            <select className={s.select} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="סינון לפי סטטוס">
              <option value="open,acked">סטטוס: פתוחות + אושרו</option>
              <option value="open">סטטוס: פתוחות</option>
              <option value="resolved">סטטוס: נסגרו</option>
              <option value="open,acked,resolved">סטטוס: הכל</option>
            </select>
            <select className={s.select} value={type} onChange={(e) => setType(e.target.value)} aria-label="סינון לפי סוג">
              <option value="">סוג: הכל</option>
              {types.map(([t, l]) => <option key={t} value={t}>{l}</option>)}
            </select>
          </>}
          {preview && onShowAll && <button className={s.btn} onClick={onShowAll}>לכל ההתראות ←</button>}
        </div>
      </div>
      {err && <div className={s.cardB}><p className={s.errbox} role="alert">{err}</p></div>}
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} what="התראות" /> : (
        <ul className={a.alerts}>
          {rows.length === 0 && <li className={s.state}>אין התראות {status === "resolved" ? "סגורות" : "פתוחות"} 🎉</li>}
          {rows.map((x) => <AlertRow key={x.id} x={x} now={now} busy={act.isPending}
            onAck={() => act.mutate({ id: x.id, kind: "ack" })} onResolve={() => act.mutate({ id: x.id, kind: "resolve" })}
            onOpenDevice={onOpenDevice} />)}
        </ul>
      )}
      {!preview && (
        <p className={s.note} style={{ margin: "10px 14px" }}>
          "אשר" = נראתה ע״י מפעיל (נסגרת אוטומטית כשהתנאי חולף). "סגור" סוגר ידנית; אם התנאי עדיין מתקיים ההתראה תיפתח שוב אחרי 10 דק׳.
          התראות "מידע" (ללא קשר, תצורה ממתינה, פקודה פגה) אינן נספרות בתג שבסרגל העליון. במכשירים אישיים (PERSONAL) השהיה אינה מפעילה התראה.
        </p>
      )}
    </section>
  );
}

function AlertRow({ x, now, busy, onAck, onResolve, onOpenDevice }: {
  x: FieldAlert; now: number; busy: boolean; onAck: () => void; onResolve: () => void; onOpenDevice?: (id: string) => void;
}) {
  const sev = SEVERITY_HE[x.severity] ?? SEVERITY_HE.info;
  const tone = x.status === "resolved" ? "ok" : sev.tone;
  const dur = x.opened_at ? ((x.resolved_at ? Date.parse(x.resolved_at) : now) - Date.parse(x.opened_at)) / 1000 : 0;
  return (
    <li className={`${a.alert} ${a[`a-${tone}`]}`} data-alert-id={x.id} data-alert-status={x.status}>
      <span className={a.sev}><span aria-hidden="true">{x.status === "resolved" ? "✓" : sev.icon}</span> {x.status === "resolved" ? "נסגרה" : sev.label}</span>
      <div className={a.body}>
        <div className={a.title}>
          {x.type_label_he}
          {x.occurrences > 1 && <span className={a.occ}> ×{x.occurrences}</span>}
          {x.device_id && <> · {onOpenDevice
            ? <button className={s.rowBtn} onClick={() => onOpenDevice(x.device_id!)}><b>{x.device_label ?? x.device_id.slice(0, 8)}</b></button>
            : <b>{x.device_label}</b>}</>}
        </div>
        <div className={a.det}>{x.message_he}</div>
      </div>
      <div className={a.meta}>
        <span>נפתחה {fmtTime(x.opened_at)}</span>
        <span>משך {durationText(dur)}</span>
        <span className={a.st}>{ALERT_STATUS_HE[x.status]}{x.acked_by ? ` · ${x.acked_by}` : ""}</span>
        <span className={s.srOnly}>{relTime(x.opened_at, now)}</span>
      </div>
      <div className={a.act}>
        {x.status === "open" && <button className={s.btn} onClick={onAck} disabled={busy}>אשר</button>}
        {x.status !== "resolved" && <button className={s.btnGhost} onClick={onResolve} disabled={busy}>סגור</button>}
      </div>
    </li>
  );
}
