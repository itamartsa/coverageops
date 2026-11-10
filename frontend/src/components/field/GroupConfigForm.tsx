/**
 * Group configuration form (spec 3.4 #28, 3.6; contract "תצורה מרחוק" + v2). Client-side
 * validation mirrors the server ranges (field_config.validate_config); the server stays the
 * authority and its 422 message is shown as is. Only fields that differ from the contract
 * defaults (or were already set on the group) are sent — the rest keep inheriting the defaults.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiErrorText, fieldApi, type FieldDeviceConfig, type FieldGroup } from "../../services/api";
import { Dialog } from "./primitives";
import s from "./field.module.css";
import g from "./AdminTab.module.css";

/** Contract defaults — mirror of field_config.DEFAULT_CONFIG. */
export const DEFAULT_CONFIG: FieldDeviceConfig = {
  sampling_interval_s: 30, min_distance_m: 25, stationary_interval_s: 300, upload_interval_s: 60,
  upload_wifi_only: false, active_test_interval_s: 0, collect_neighbors: true, location_mode: "precise",
  schedule: null, low_battery_threshold_pct: 15, allow_user_pause: true,
};

type IntKey = "sampling_interval_s" | "min_distance_m" | "stationary_interval_s" | "upload_interval_s"
  | "low_battery_threshold_pct" | "active_test_interval_s";
export const INT_FIELDS: { k: IntKey; label: string; unit: string; min: number; max: number; zeroOk?: boolean }[] = [
  { k: "sampling_interval_s", label: "מרווח דגימה בתנועה", unit: "ש׳", min: 5, max: 3600 },
  { k: "min_distance_m", label: "מרחק מינימלי בין דגימות", unit: "מ׳", min: 0, max: 1000 },
  { k: "stationary_interval_s", label: "מרווח דגימה בעמידה", unit: "ש׳", min: 30, max: 3600 },
  { k: "upload_interval_s", label: "מרווח העלאה", unit: "ש׳", min: 15, max: 3600 },
  { k: "active_test_interval_s", label: "בדיקה אקטיבית (0 = כבוי)", unit: "ש׳", min: 300, max: 86400, zeroOk: true },
  { k: "low_battery_threshold_pct", label: "סף סוללה חלשה", unit: "%", min: 5, max: 50 },
];
const DAYS = ["א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳"];
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

type Win = { days: number[]; start: string; end: string };

export function scheduleText(sch: Win[] | null | undefined): string {
  if (!sch?.length) return "רציף (ORG) / ידני (PERSONAL)";
  return sch.map((w) => `${w.days.map((d) => DAYS[d]).join("")} ${w.start}–${w.end}`).join(" · ");
}

export default function GroupConfigForm({ group, onClose }: { group: FieldGroup | null; onClose: () => void }) {
  const qc = useQueryClient();
  const eff = group?.effective_config ?? DEFAULT_CONFIG;
  const [name, setName] = useState(group?.name ?? "");
  const [desc, setDesc] = useState(group?.description ?? "");
  const [ints, setInts] = useState<Record<IntKey, string>>(() =>
    Object.fromEntries(INT_FIELDS.map((f) => [f.k, String(eff[f.k])])) as Record<IntKey, string>);
  const [bools, setBools] = useState({ upload_wifi_only: eff.upload_wifi_only, collect_neighbors: eff.collect_neighbors, allow_user_pause: eff.allow_user_pause });
  const [locMode, setLocMode] = useState(eff.location_mode);
  const [sched, setSched] = useState<Win[]>(eff.schedule ?? []);
  const [serverErr, setServerErr] = useState<string | null>(null);

  const errors: Record<string, string> = {};
  if (!name.trim()) errors.name = "שם חובה";
  for (const f of INT_FIELDS) {
    const v = Number(ints[f.k]);
    const ok = ints[f.k].trim() !== "" && Number.isInteger(v) && ((f.zeroOk && v === 0) || (v >= f.min && v <= f.max));
    if (!ok) errors[f.k] = f.zeroOk ? `0 או ${f.min}–${f.max}` : `מספר שלם ${f.min}–${f.max}`;
  }
  if (sched.length > 14) errors.schedule = "לכל היותר 14 חלונות";
  sched.forEach((w, i) => {
    if (!w.days.length) errors[`s${i}`] = "בחרו לפחות יום אחד";
    else if (!HHMM.test(w.start) || !HHMM.test(w.end)) errors[`s${i}`] = "שעה בפורמט HH:MM";
    else if (w.start === w.end) errors[`s${i}`] = "התחלה וסיום זהים — חלון לא חד-משמעי";
  });
  const valid = Object.keys(errors).length === 0;

  function buildConfig(): Partial<FieldDeviceConfig> {
    const cur: Record<string, unknown> = {
      ...Object.fromEntries(INT_FIELDS.map((f) => [f.k, Number(ints[f.k])])),
      ...bools, location_mode: locMode, schedule: sched.length ? sched : null,
    };
    const prev = group?.config ?? {};
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(cur)) {
      const def = (DEFAULT_CONFIG as unknown as Record<string, unknown>)[k];
      if (k in prev || JSON.stringify(v) !== JSON.stringify(def)) out[k] = v;
    }
    return out as Partial<FieldDeviceConfig>;
  }

  const save = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), description: desc.trim() || null, config: buildConfig() };
      return group ? fieldApi.updateGroup(group.id, body) : fieldApi.createGroup(body);
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["field"] }); onClose(); },
    onError: (e) => setServerErr(apiErrorText(e, "השמירה נכשלה")),
  });

  return (
    <Dialog title={group ? `תצורת קבוצה — ${group.name}` : "קבוצה חדשה"} onClose={onClose} wide labelledBy="grp-title"
      footer={<>
        <button className={s.btnPrimary} disabled={!valid || save.isPending} onClick={() => { setServerErr(null); save.mutate(); }}>שמור</button>
        <button className={s.btn} onClick={onClose}>ביטול</button>
        {group && <span className={s.note}>שינוי תצורה מעלה את גרסת התצורה לכל מכשירי הקבוצה ({group.device_count}).</span>}
      </>}>
      <form className={g.form} onSubmit={(e) => { e.preventDefault(); if (valid) save.mutate(); }} data-testid="group-form">
        <label className={g.field}><span>שם הקבוצה</span>
          <input className={s.input} value={name} onChange={(e) => setName(e.target.value)} maxLength={64} aria-invalid={!!errors.name} />
          {errors.name && <em className={g.err}>{errors.name}</em>}</label>
        <label className={g.field}><span>תיאור</span>
          <input className={s.input} value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={256} /></label>
        {INT_FIELDS.map((f) => (
          <label key={f.k} className={g.field}>
            <span>{f.label} <span className={s.muted}>({f.zeroOk ? `0 או ${f.min}–${f.max}` : `${f.min}–${f.max}`} {f.unit})</span></span>
            <span className={g.inUnit}>
              <input className={s.input} type="number" inputMode="numeric" value={ints[f.k]} dir="ltr"
                min={f.zeroOk ? 0 : f.min} max={f.max} aria-invalid={!!errors[f.k]}
                onChange={(e) => setInts({ ...ints, [f.k]: e.target.value })} />
              <span>{f.unit}</span>
            </span>
            {errors[f.k] && <em className={g.err}>{errors[f.k]}</em>}
          </label>
        ))}
        <label className={g.field}><span>מצב מיקום</span>
          <select className={s.select} value={locMode} onChange={(e) => setLocMode(e.target.value as typeof locMode)}>
            <option value="precise">מדויק (precise)</option><option value="coarse">משוער ~1 ק״מ (coarse)</option><option value="off">כבוי (off)</option>
          </select>
          <em className={s.muted} style={{ fontStyle: "normal", fontSize: 11 }}>במכשיר אישי זו תקרה בלבד — המשתמש יכול רק להחמיר.</em></label>
        <div className={g.field}>
          <span>אפשרויות</span>
          <label><input type="checkbox" checked={bools.upload_wifi_only} onChange={(e) => setBools({ ...bools, upload_wifi_only: e.target.checked })} /> העלאה ב-Wi-Fi בלבד</label>
          <label><input type="checkbox" checked={bools.collect_neighbors} onChange={(e) => setBools({ ...bools, collect_neighbors: e.target.checked })} /> איסוף תאים שכנים</label>
          <label><input type="checkbox" checked={bools.allow_user_pause} onChange={(e) => setBools({ ...bools, allow_user_pause: e.target.checked })} /> לאפשר למשתמש להשהות (ORG)</label>
          <em className={s.muted} style={{ fontStyle: "normal", fontSize: 11 }}>🔒 במכשיר אישי (PERSONAL) השהיה מותרת תמיד — השרת מתעלם מהערך כאן.</em>
        </div>

        <fieldset className={g.sched}>
          <legend>חלונות משמרת (שעון ישראל; סיום ≤ התחלה = חוצה חצות; ריק = ORG רציף / PERSONAL ידני)</legend>
          {sched.map((w, i) => (
            <div key={i} className={g.win}>
              <span className={g.days} role="group" aria-label={`ימים בחלון ${i + 1}`}>
                {DAYS.map((d, di) => (
                  <label key={di} className={s.chip}>
                    <input type="checkbox" checked={w.days.includes(di)} onChange={(e) => {
                      const days = e.target.checked ? [...w.days, di].sort() : w.days.filter((x) => x !== di);
                      setSched(sched.map((x, j) => (j === i ? { ...x, days } : x)));
                    }} />{d}
                  </label>
                ))}
              </span>
              <input className={s.input} value={w.start} dir="ltr" aria-label="התחלה" style={{ width: 70 }}
                onChange={(e) => setSched(sched.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)))} />
              <span>–</span>
              <input className={s.input} value={w.end} dir="ltr" aria-label="סיום" style={{ width: 70 }}
                onChange={(e) => setSched(sched.map((x, j) => (j === i ? { ...x, end: e.target.value } : x)))} />
              <button type="button" className={s.btnGhost} onClick={() => setSched(sched.filter((_, j) => j !== i))} aria-label={`הסר חלון ${i + 1}`}>✕</button>
              {errors[`s${i}`] && <em className={g.err}>{errors[`s${i}`]}</em>}
            </div>
          ))}
          <button type="button" className={s.btn} disabled={sched.length >= 14}
            onClick={() => setSched([...sched, { days: [0, 1, 2, 3, 4], start: "07:00", end: "19:00" }])}>+ חלון</button>
          {errors.schedule && <em className={g.err}>{errors.schedule}</em>}
        </fieldset>
        {serverErr && <p className={s.errbox} role="alert">{serverErr}</p>}
      </form>
    </Dialog>
  );
}
