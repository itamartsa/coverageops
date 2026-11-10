/**
 * Enrollment: create a code (ADMIN) → show the code ONCE with the server-generated QR SVG
 * (segno, POST /enrollment-codes only — the plain code is never stored, so it can't be shown
 * again). The QR is rendered as an <img> data URI (no inline SVG injection).
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiErrorText, fieldApi, type EnrollmentCodeCreated, type FieldGroup } from "../../services/api";
import { fmtDateTime } from "../../lib/fieldFormat";
import { Dialog, Ownership } from "./primitives";
import s from "./field.module.css";
import g from "./AdminTab.module.css";

export default function EnrollDialog({ groups, onClose, onShowRequirements }: {
  groups: FieldGroup[]; onClose: () => void; onShowRequirements: () => void;
}) {
  const qc = useQueryClient();
  const [label, setLabel] = useState("");
  const [groupId, setGroupId] = useState<string>("");
  const [ownership, setOwnership] = useState<"ORG" | "PERSONAL" | "">("");
  const [maxUses, setMaxUses] = useState("1");
  const [ttl, setTtl] = useState("24");
  const [err, setErr] = useState<string | null>(null);
  const [created, setCreated] = useState<EnrollmentCodeCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const mu = Number(maxUses), th = Number(ttl);
  const errors: Record<string, string> = {};
  if (!ownership) errors.ownership = "יש לבחור בעלות";
  if (!Number.isInteger(mu) || mu < 1 || mu > 200) errors.maxUses = "1–200";
  if (!Number.isInteger(th) || th < 1 || th > 720) errors.ttl = "1–720 שעות";
  const valid = !Object.keys(errors).length;

  const create = useMutation({
    mutationFn: () => fieldApi.createEnrollmentCode({
      label: label.trim() || null, group_id: groupId ? Number(groupId) : null,
      ownership: ownership as "ORG" | "PERSONAL", max_uses: mu, ttl_hours: th,
    }),
    onSuccess: (r) => { setCreated(r); qc.invalidateQueries({ queryKey: ["field", "enrollment-codes"] }); },
    onError: (e) => setErr(apiErrorText(e, "יצירת הקוד נכשלה")),
  });

  async function copy() {
    if (!created) return;
    try { await navigator.clipboard.writeText(created.code); setCopied(true); } catch { setCopied(false); }
  }

  if (created) {
    const qrSrc = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(created.qr_svg)}`;
    return (
      <Dialog title="צירוף מכשיר חדש" onClose={onClose} wide labelledBy="enr-title" footer={<>
        <button className={s.btnPrimary} onClick={copy}>{copied ? "✓ הועתק" : "העתק קוד"}</button>
        <button className={s.btn} onClick={() => window.print()}>הדפס</button>
        <button className={s.btn} onClick={onShowRequirements}>ⓘ דרישות מערכת למכשיר</button>
        <button className={s.btnGhost} onClick={onClose}>סגור</button>
      </>}>
        <div className={g.enr} data-testid="enroll-result">
          <figure className={g.qrBox}>
            <img src={qrSrc} alt={`קוד QR לצירוף מכשיר — מכיל את כתובת השרת והקוד ${created.code}`} className={g.qr} data-testid="enroll-qr" />
            <figcaption className={s.muted}>QR · מכיל את כתובת השרת ואת הקוד</figcaption>
          </figure>
          <div style={{ minWidth: 0 }}>
            <div className={s.muted} style={{ fontSize: 11 }}>קוד צירוף (להקלדה ידנית)</div>
            <div className={g.code} data-testid="enroll-code" dir="ltr">{created.code}</div>
            <dl className={s.kv} style={{ margin: "12px 0" }}>
              <dt>קבוצה</dt><dd>{created.group ?? "ברירת מחדל"}</dd>
              <dt>בעלות</dt><dd><Ownership value={created.ownership} /> בלבד</dd>
              <dt>שימושים</dt><dd><bdi dir="ltr">{created.use_count} / {created.max_uses}</bdi>{created.max_uses === 1 ? " (חד-פעמי)" : ""}</dd>
              <dt>תוקף</dt><dd>עד {fmtDateTime(created.expires_at)}</dd>
              {created.label && <><dt>תווית</dt><dd>{created.label}</dd></>}
              <dt>כתובת שרת</dt><dd><bdi dir="ltr" style={{ fontSize: 11 }}>{decodeURIComponent(created.qr_payload.match(/server=([^&]+)/)?.[1] ?? "")}</bdi></dd>
            </dl>
            <p className={s.warnbox}><b>הקוד מוצג פעם אחת בלבד.</b> בשרת נשמר רק hash. מי שמחזיק בקוד או ב-QR יכול לצרף מכשיר עד שהקוד ינוצל או יפוג — אל תשלחו אותו בערוץ פתוח.</p>
            <p className={s.note} style={{ marginTop: 8 }}>האפליקציה דורשת Android 11 (API 30) ומעלה —&nbsp;
              <button className={s.lnk} onClick={onShowRequirements}>כל דרישות המערכת ליחידות הקצה</button>.</p>
          </div>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title="קוד צירוף חדש" onClose={onClose} labelledBy="enr-new-title" footer={<>
      <button className={s.btnPrimary} disabled={!valid || create.isPending} onClick={() => { setErr(null); create.mutate(); }}>צור קוד</button>
      <button className={s.btn} onClick={onShowRequirements}>ⓘ דרישות מערכת</button>
      <button className={s.btnGhost} onClick={onClose}>ביטול</button>
    </>}>
      <form className={g.form1} onSubmit={(e) => { e.preventDefault(); if (valid) create.mutate(); }} data-testid="enroll-form">
        <fieldset className={g.field} style={{ border: 0 }}>
          <legend>בעלות המכשיר (חובה)</legend>
          <label><input type="radio" name="own" checked={ownership === "ORG"} onChange={() => setOwnership("ORG")} /> ORG — מכשיר ארגוני (ניטור רציף)</label>
          <label><input type="radio" name="own" checked={ownership === "PERSONAL"} onChange={() => setOwnership("PERSONAL")} /> PERSONAL — מכשיר פרטי (הסכמה, השהיה, משמרות)</label>
          {errors.ownership && <em className={g.err}>{errors.ownership}</em>}
          {ownership === "PERSONAL" && <p className={s.warnbox}>פריסה למכשירים פרטיים חסומה עד חוות דעת משפטית (חוזה v2.1 #9).</p>}
        </fieldset>
        <label className={g.field}><span>קבוצה</span>
          <select className={s.select} value={groupId} onChange={(e) => setGroupId(e.target.value)}>
            <option value="">ברירת מחדל</option>
            {groups.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select></label>
        <label className={g.field}><span>תווית (רשות)</span>
          <input className={s.input} value={label} onChange={(e) => setLabel(e.target.value)} maxLength={64} /></label>
        <label className={g.field}><span>מספר שימושים <span className={s.muted}>(1–200)</span></span>
          <input className={s.input} type="number" dir="ltr" min={1} max={200} value={maxUses} onChange={(e) => setMaxUses(e.target.value)} aria-invalid={!!errors.maxUses} />
          {errors.maxUses && <em className={g.err}>{errors.maxUses}</em>}</label>
        <label className={g.field}><span>תוקף בשעות <span className={s.muted}>(1–720)</span></span>
          <input className={s.input} type="number" dir="ltr" min={1} max={720} value={ttl} onChange={(e) => setTtl(e.target.value)} aria-invalid={!!errors.ttl} />
          {errors.ttl && <em className={g.err}>{errors.ttl}</em>}</label>
        {err && <p className={s.errbox} role="alert">{err}</p>}
      </form>
    </Dialog>
  );
}
