/**
 * ADMIN device actions: revoke (token invalidated immediately) and remove with the data rule of
 * contract v2.1 rule 5 / spec 9.6 — ORG device data is ANONYMISED, never deleted;
 * PERSONAL device data is deleted (or anonymised, by choice). Purge runs in the next
 * maintenance pass (`completes_by`).
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiErrorText, fieldApi } from "../../services/api";
import { fmtDateTime } from "../../lib/fieldFormat";
import { Dialog } from "./primitives";
import s from "./field.module.css";

export const ORG_ANON_RULE_HE =
  "מכשיר ארגוני (ORG): הנתונים מאונונמים ואינם נמחקים — הדגימות נשארות עד תום תקופת השמירה (90 יום) אך מנותקות מהמכשיר: מיקום מעוגל למרכז תא 100 מ׳ וזמן לשעה.";
export const PERSONAL_DELETE_RULE_HE =
  "מכשיר אישי (PERSONAL): ברירת המחדל היא מחיקת כל הדגימות הגולמיות, ה-heartbeats, הפקודות וההתראות של המכשיר. אפשר לבחור אנונימיזציה במקום.";

export default function DeviceAdminActions({ id, label, ownership, adminStatus, onDone }: {
  id: string; label: string; ownership: "ORG" | "PERSONAL"; adminStatus: string; onDone?: () => void;
}) {
  const qc = useQueryClient();
  const [dlg, setDlg] = useState<null | "revoke" | "remove">(null);
  const [mode, setMode] = useState<"delete" | "anonymize">(ownership === "ORG" ? "anonymize" : "delete");
  const [result, setResult] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const done = () => { qc.invalidateQueries({ queryKey: ["field"] }); };

  const revoke = useMutation({
    mutationFn: () => fieldApi.revoke(id),
    onSuccess: () => { setDlg(null); done(); setResult("המכשיר בוטל — הטוקן שלו נפסל מיד."); },
    onError: (e) => setErr(apiErrorText(e, "הביטול נכשל")),
  });
  const remove = useMutation({
    mutationFn: () => fieldApi.remove(id, ownership === "ORG" ? "anonymize" : mode),
    onSuccess: (r) => {
      setDlg(null); done();
      setResult(`${r.data_action === "anonymize" ? "האנונימיזציה" : "המחיקה"} תושלם עד ${fmtDateTime(r.completes_by)} (בריצת התחזוקה הבאה).`);
      onDone?.();
    },
    onError: (e) => setErr(apiErrorText(e, "ההסרה נכשלה")),
  });

  return (
    <>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {adminStatus === "active" && <button className={s.btnDanger} onClick={() => { setErr(null); setDlg("revoke"); }}>בטל מכשיר</button>}
        <button className={s.btnDanger} onClick={() => { setErr(null); setDlg("remove"); }}>
          {ownership === "ORG" ? "הסר ואנונם נתונים" : "הסר ומחק נתונים"}
        </button>
      </div>
      {result && <p className={s.infobox} role="status" style={{ marginTop: 6 }}>{result}</p>}

      {dlg === "revoke" && (
        <Dialog title={`ביטול מכשיר — ${label}`} onClose={() => setDlg(null)} footer={<>
          <button className={s.btnDanger} onClick={() => revoke.mutate()} disabled={revoke.isPending}>אשר ביטול</button>
          <button className={s.btn} onClick={() => setDlg(null)}>חזרה</button>
        </>}>
          <p>הטוקן של המכשיר ייפסל מיד והוא יפסיק לשלוח נתונים. פקודות פתוחות יבוטלו. הנתונים שכבר נאספו נשמרים.</p>
          {err && <p className={s.errbox} role="alert" style={{ marginTop: 8 }}>{err}</p>}
        </Dialog>
      )}
      {dlg === "remove" && (
        <Dialog title={`הסרת מכשיר — ${label}`} onClose={() => setDlg(null)} footer={<>
          <button className={s.btnDanger} onClick={() => remove.mutate()} disabled={remove.isPending}>
            {ownership === "ORG" || mode === "anonymize" ? "הסר ואנונם" : "הסר ומחק"}
          </button>
          <button className={s.btn} onClick={() => setDlg(null)}>חזרה</button>
        </>}>
          {ownership === "ORG" ? (
            <p className={s.warnbox}><b>כלל ORG:</b> {ORG_ANON_RULE_HE}</p>
          ) : (
            <>
              <p className={s.note} style={{ marginBottom: 8 }}>{PERSONAL_DELETE_RULE_HE}</p>
              <fieldset style={{ border: 0, display: "flex", flexDirection: "column", gap: 6 }}>
                <legend className={s.srOnly}>טיפול בנתונים</legend>
                <label><input type="radio" name="purge" checked={mode === "delete"} onChange={() => setMode("delete")} /> מחיקת הנתונים (ברירת מחדל)</label>
                <label><input type="radio" name="purge" checked={mode === "anonymize"} onChange={() => setMode("anonymize")} /> אנונימיזציה</label>
              </fieldset>
            </>
          )}
          <p className={s.note} style={{ marginTop: 8 }}>הטוקן נפסל מיד; הטיפול בנתונים מתבצע בריצת התחזוקה הבאה (עד ~5 דק׳). הפעולה נרשמת ביומן הביקורת.</p>
          {err && <p className={s.errbox} role="alert" style={{ marginTop: 8 }}>{err}</p>}
        </Dialog>
      )}
    </>
  );
}
