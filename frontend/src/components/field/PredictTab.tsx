/**
 * "חיזוי" tab — the prediction endpoints (M5: /predict/cells, /predict/route, /predict/validation,
 * /device-health) are NOT built on the server yet. Honest empty state: what will appear, and that
 * it needs accumulated data. No numbers are shown.
 */
import type { FieldSummary } from "../../services/api";
import { fmtInt } from "../../lib/fieldFormat";
import s from "./field.module.css";
import t from "./FieldTabs.module.css";

export default function PredictTab({ summary }: { summary?: FieldSummary }) {
  return (
    <div className={t.stack} data-testid="predict-tab">
      <div className={s.card}>
        <div className={s.cardB}>
          <div className={s.insuff}>
            <span className={s.insuffIc} aria-hidden="true">⏳</span>
            <div>
              <b style={{ fontSize: 14 }}>בפיתוח — שלב הבא</b>
              <p style={{ marginTop: 6 }}>
                חיזוי הקליטה עדיין לא זמין: ממשקי החיזוי בשרת טרם נבנו, ואין כאן מודל או ערכים משוערים.
                החיזוי יתבסס על מדידות שנצברו לאורך זמן ולכן יופיע רק אחרי שתצטבר היסטוריה מספקת.
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className={t.g2}>
        <section className={s.card} aria-label="מה יופיע כאן">
          <div className={s.cardH}><h2>מה יופיע בלשונית זו</h2></div>
          <div className={s.cardB}>
            <ul style={{ paddingInlineStart: 18, display: "flex", flexDirection: "column", gap: 6, fontSize: 12.5 }}>
              <li><b>קליטה צפויה לפי תא</b> — מפת תאים עם ערך צפוי (חציון וטווח 80%) ורמת ביטחון; תאים בלי מספיק נתונים יסומנו "אין מספיק נתונים" ולא יקבלו ערך.</li>
              <li><b>חיזוי מסלול</b> — בחירת מסלול ופילוחו לקטעים של 100 מ׳ עם הרמה הצפויה וההסתברות לרמה ≥ בינוני בכל קטע.</li>
              <li><b>כרטיס תיקוף</b> — בדיקת דיוק מול נתונים שלא שימשו לבנייה (MAE, כיסוי טווח 80%, מול בסיס השוואה פשוט).</li>
              <li><b>בריאות מכשירים</b> (מנהל/מפעיל) — קצב פריקת סוללה וסיכון לנפילה, מבוסס heartbeat.</li>
            </ul>
            <p className={s.note} style={{ marginTop: 10 }}>שלב ראשון (P0) יהיה סטטיסטיקה היסטורית לכל תא — לא מודל ML.</p>
          </div>
        </section>
        <section className={s.card} aria-label="דרישות נתונים">
          <div className={s.cardH}><h2>מה נדרש כדי שיופיע</h2></div>
          <div className={s.cardB}>
            <ul style={{ paddingInlineStart: 18, display: "flex", flexDirection: "column", gap: 6, fontSize: 12.5 }}>
              <li>לכל תא: לפחות 10 דגימות שנאספו ב-2 ימים שונים לפחות (אחרת "אין מספיק נתונים").</li>
              <li>היסטוריה של כמה שבועות כדי להבחין בין שעות ביום וימים בשבוע.</li>
              <li>מימוש ממשקי החיזוי בשרת (אבן דרך M5).</li>
            </ul>
            {summary && (
              <p className={s.note} style={{ marginTop: 10 }}>
                כרגע בחלון הנבחר: <bdi dir="ltr">{fmtInt(summary.kpis.samples)}</bdi> דגימות. זהו מידע על כמות הנתונים בלבד — לא חיזוי.
              </p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
