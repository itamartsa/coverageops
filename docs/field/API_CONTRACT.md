# חוזה משותף — ניטור קליטה ממכשירי שטח (CoverageOps Field Probe)

מסמך זה מחייב את שני הצוותים (אפליקציית אנדרואיד + דאשבורד). אין לסטות ממנו; אם נדרש שינוי — לציין זאת במפורש בסעיף "בקשות שינוי לחוזה" בסוף האפיון שלכם, ולא לשנות בשקט.

## הקשר
- CoverageOps: FastAPI + SQLAlchemy 2 (PostgreSQL/PostGIS בפרודקשן, SQLite בפיתוח/בדיקות), React 18 + Vite + TypeScript + react-leaflet + zustand + react-query, CSS Modules, RTL עברית, ערכת צבעים כהה (ראו frontend/src/index.css).
- הרשאות קיימות: ADMIN / OPERATOR / VIEWER, JWT דרך `/api/auth/login`, `require_role(...)`.
- מיגרציות: `create_all` + `run_startup_migrations` (עמודות תוספתיות). טבלאות חדשות נוצרות שלמות.
- קיימת טבלת `cell_sites` (מאגר ממשלתי, ללא Cell ID) וטבלת `coverage_results` (חיזוי RSSI מחושב). אלה בסיס להשוואת "נמדד מול חזוי".
- קוד מקור לקריאה בלבד: /mnt/user-data/uploads/CoverageOps/coverageops/ (docs/TECHNICAL.md הוא נקודת הפתיחה).

## החלטות מוצר (אושרו ע"י המשתמש)
- אנדרואיד: Kotlin נייטיב. הפצה: APK ישיר / MDM (לא Google Play בשלב ראשון).
- שני סוגי מכשירים: ORG (בבעלות הארגון — ניטור רציף) ו-PERSONAL (מכשיר פרטי — הסכמה מפורשת, אפשרות עצירה, איסוף מינימלי, ניטור רק במשמרת/כשהמשתמש מפעיל).
- הדאשבורד הוא עמוד בתוך CoverageOps (route חדש `/field`), לא מערכת נפרדת.
- מטרות: (1) בקרה חיה על מכשירים בשטח, (2) ניתוח היסטורי, (3) חיזוי, (4) כיול מודל הכיסוי מול מדידות אמת.
- הסבב הנוכחי: אפיון + עיצוב בלבד. אין לכתוב קוד מוצר.

## זהות ואימות מכשיר
- רישום: ADMIN יוצר "קוד צירוף" (enrollment code, חד-פעמי/מוגבל בזמן, מוצג גם כ-QR) בדאשבורד.
- `POST /api/field/devices/enroll` {enrollment_code, device_uuid, model, manufacturer, os_version, app_version, ownership: ORG|PERSONAL, label?} → {device_id, device_token, config}
- `device_token`: טוקן ארוך-טווח ייעודי למכשיר (נשמר בשרת כ-hash, ניתן לביטול), נשלח ב-`Authorization: Bearer`. אינו JWT של משתמש ואינו נותן גישה לשום endpoint אחר.
- אין לאסוף IMEI / IMSI / מספר טלפון. מזהה המכשיר הוא UUID שהאפליקציה מייצרת.

## קליטת מדידות
`POST /api/field/measurements/batch` (device_token), גוף gzip JSON:
```
{ "device_id": "...", "sent_at": ISO8601 UTC, "config_version": int,
  "samples": [ Sample, ... ]   // עד 500 בבקשה
}
```
Sample:
| שדה | סוג | הערות |
|---|---|---|
| sample_id | UUID | נוצר במכשיר; השרת אידמפוטנטי לפי (device_id, sample_id) |
| ts | ISO8601 UTC | זמן המדידה |
| lat, lon | float WGS84 | nullable (אין GPS / מצב פרטיות) |
| loc_accuracy_m | float | |
| altitude_m, speed_mps, bearing_deg | float? | |
| loc_provider | gps\|fused\|network\|none | |
| network_type | NR_SA\|NR_NSA\|LTE\|UMTS\|GSM\|NONE | |
| mcc, mnc | string | |
| operator_name | string | |
| sim_slot | int | תמיכה ב-dual SIM: דגימה לכל SIM פעיל |
| is_roaming | bool | |
| cell_id | long? | ECI (LTE) / NCI (NR) / CID |
| pci, tac, earfcn_arfcn, band | int? | |
| rsrp_dbm, rsrq_db, sinr_db, rssi_dbm | float? | LTE: rsrp/rsrq/rssnr; NR: ss-rsrp/ss-rsrq/ss-sinr |
| cqi, timing_advance | int? | |
| signal_level | int 0-4 | רמת המערכת |
| data_state | connected\|disconnected\|suspended | |
| service_state | in_service\|emergency_only\|out_of_service\|power_off | |
| neighbors | [{pci, earfcn_arfcn, rsrp_dbm, rsrq_db, network_type}] | עד 8 |
| battery_pct | int | |
| is_charging, screen_on | bool | |
| trigger | periodic\|cell_change\|service_change\|movement\|manual\|active_test | |
| active_test | {latency_ms, jitter_ms, loss_pct, dl_kbps?, ul_kbps?}? | רק בדגימות active_test |

תשובה: `{accepted: n, duplicates: n, rejected: [{sample_id, reason}], config_version: int}`. אם `config_version` בשרת גדול — המכשיר מושך `GET /api/field/devices/me/config`.

## Heartbeat
`POST /api/field/devices/heartbeat` (device_token) {ts, battery_pct, is_charging, app_version, queue_size, permissions: {location, background_location, phone_state, notifications, battery_optimization_exempt}, monitoring_state: active|paused_by_user|paused_schedule|degraded}. נשלח כל 5 דק' כשיש רשת, גם כשאין דגימות חדשות. מכשיר נחשב OFFLINE אחרי 3 פספוסים (15 דק').

## תצורה מרחוק (config)
```
{ config_version, sampling_interval_s (ברירת מחדל 30), min_distance_m (25),
  stationary_interval_s (300), upload_interval_s (60), upload_wifi_only (false),
  active_test_interval_s (0 = כבוי), collect_neighbors (true),
  location_mode: precise|coarse|off, schedule: [{days, start, end}]? ,
  low_battery_threshold_pct (15) }
```
נקבעת ברמת קבוצת מכשירים (device group) עם דריסה לכל מכשיר.

## סיווג איכות (משותף לאפליקציה ולדאשבורד — אותן 5 רמות ואותם צבעים של CoverageOps)
| רמה | LTE RSRP | NR SS-RSRP | צבע (token) |
|---|---|---|---|
| מצוין | ≥ -85 | ≥ -85 | --cov-excellent #00ff88 |
| טוב | -85..-95 | -85..-95 | --cov-good #7dff6b |
| בינוני | -95..-105 | -95..-105 | --cov-medium #ffe600 |
| חלש | -105..-115 | -105..-115 | --cov-weak #ff8c00 |
| שולי/אין | < -115 או out_of_service | < -115 | --cov-marginal #ff3b5c |
אין להסתמך על צבע בלבד: כל רמה מלווה בתווית טקסט/אייקון.

## פרטיות (מחייב)
- PERSONAL: מסך הסכמה מפורט לפני כל איסוף; מתג עצירה גלוי תמיד; התראה קבועה (foreground service) כשהניטור פעיל; ניטור רק בחלון משמרת או בהפעלה ידנית; אפשרות location_mode=coarse.
- ORG: הודעת יידוע במסך ראשון + התראה קבועה; עצירה ע"י משתמש ניתנת לחסימה ע"י מדיניות אך תמיד מדווחת.
- שמירת נתונים: דגימות גולמיות 90 יום (ניתן להגדרה), אגרגציות ללא הגבלה. מחיקת מכשיר מוחקת/מאנונימת את הדגימות הגולמיות שלו.
- גישה למיקום מכשיר חי: ADMIN + OPERATOR. VIEWER רואה אגרגציות ומפת חום בלבד, ללא זיהוי מכשיר/אדם.
- כל צפייה במסלול מכשיר בודד נרשמת ב-activity_log.

---
# גרסה 2 — הכרעות האדריכל על בקשות השינוי (גוברות על כל מה שלמעלה במקרה של סתירה)

מקרא: A-CRn = בקשת צוות האנדרואיד, D-CRn = בקשת צוות הדאשבורד.

## סיווג איכות (A-CR18, D-CR1/2/3/11/12)
- גבולות חצי-פתוחים לפי RSRP (LTE) / SS-RSRP (NR): מצוין `x ≥ -85`; טוב `-95 ≤ x < -85`; בינוני `-105 ≤ x < -95`; חלש `-115 ≤ x < -105`; שולי/אין `x < -115`.
- `out_of_service` ו-`emergency_only` → שולי/אין בכל הטכנולוגיות. `power_off` → "לא מסווג". `in_service` בלי RSRP (UMTS/GSM או null) → "לא מסווג" (אפור, מחוץ ל-5 הרמות, לא נכלל במכנה של אחוזי הרמות).
- קובץ וקטורי בדיקה משותף `field_tier_vectors.json` (ערך → רמה, כולל ערכי גבול ומקרי service_state) — מקור אמת יחיד; שני הצוותים מריצים אותו בבדיקות. הבעלים: צוות הדאשבורד (בריפו של CoverageOps), האנדרואיד מעתיק.
- כל מקרא מציין את המדד והספים ("RSRP dBm"). במפה הראשית מקרא החיזוי (RSSI) ומקרא המדידות (RSRP) מופרדים.
- סמני מפה, תאי גריד ותגיות רמה חייבים גליף פסים 0–4 בנוסף לצבע ולטקסט.

## NR_NSA (A-CR1, D-CR3)
- ב-`NR_NSA` השדות הראשיים (cell_id, pci, tac, earfcn_arfcn, band, rsrp/rsrq/sinr) הם של עוגן ה-LTE, והרמה נקבעת לפיו. אין שדה `rsrp_source` — נגזר מ-`network_type`.
- שדה אופציונלי חדש: `nr_leg: {pci, arfcn, band, ss_rsrp_dbm, ss_rsrq_db, ss_sinr_db}?` (null כשהמכשיר לא מדווח).
- ב-`NR_SA` השדות הראשיים הם SS-RSRP/SS-RSRQ/SS-SINR. הדאשבורד לא מערבב LTE RSRP ו-SS-RSRP באותו KPI בלי פילוח לפי טכנולוגיה.

## שדות Sample — תיקונים
- `neighbors[].network_type` → מוחלף ב-`rat: LTE|NR|UMTS|GSM` (A-CR2).
- `loc_accuracy_m: float?` — חובה null כש-lat/lon null (A-CR3).
- `altitude_m` = מעל אליפסואיד WGS84. חדש: `altitude_msl_m: float?` (כשהמכשיר יודע לחשב). המרה בצד שרת — שלב מאוחר (A-CR4).
- חדש: `cell_info_age_ms: int?`, `loc_age_ms: int?`. האפליקציה שולחת ערכים כפי שהם עם הגיל; **השרת** הוא שמסנן (דגימה עם `cell_info_age_ms > 10000` לא נכנסת ל-KPI/אגרגציות אך נשמרת גולמית). האפליקציה לא מאפסת שדות אות (A-CR5).
- 2G/3G: UMTS → `tac`=LAC, `pci`=PSC; GSM → `tac`=LAC, `pci`=BSIC; rsrp/rsrq/sinr=null, `rssi_dbm` מלא (A-CR7).
- `timing_advance` = אינדקס LTE בלבד. חדש: `timing_advance_us: int?` ל-NR (A-CR8).
- Dual-SIM: `sample_id` ייחודי לכל SIM; שני SIM באותו רגע חולקים `ts` (D-CR14).

## Heartbeat (A-CR6/9/10, D-CR4/5)
- קצב: כל 5 דק' במצב `active|degraded`. במצב `paused_*`: best-effort, לפחות כל 15 דק' כשהמערכת מאפשרת, **ללא מיקום**.
- סטטוס בשרת: כלל OFFLINE (15 דק' ללא heartbeat) חל רק על מכשירים שהמצב האחרון שלהם `active|degraded`. מכשיר מושהה מוצג "מושהה · נראה לאחרונה …"; אחרי 6 שעות בלי heartbeat → "ללא קשר" (התראת מידע, לא קריטית).
- שדות נוספים בבקשה: `degraded_reasons: [string]` (רשימה סגורה שצוות האנדרואיד מגדיר והדאשבורד מציג), `permissions.location: precise|approximate|none` (במקום bool), `applied_config_version`, `evicted_samples_total`, `last_sample_ts`, `location_services_enabled`, `acked_commands: [id]`.
- תשובה: `{config_version, server_time, commands: [{id, type, issued_at}]}`.
- פקודות MVP: `sample_now`, `upload_now`. שלב מאוחר: `active_test_now`, `send_diagnostics`. במכשיר PERSONAL מושהה/מחוץ למשמרת פקודות **אינן מבוצעות** (מוחזר ack עם `skipped_paused`) — רק תצורה מתעדכנת. השהיה צפויה לביצוע פקודה: עד 5 דק'. אין FCM ב-MVP.

## תצורה (A-CR11/15/17, D-CR8/10/15)
- חדש: `allow_user_pause: bool` — ברירת מחדל ORG: true (שאלה פתוחה לבעל המוצר); PERSONAL: תמיד true, השרת מתעלם מערך אחר.
- `schedule`: `[{days: [0..6] (0=ראשון), start: "HH:MM", end: "HH:MM"}]` בשעון Asia/Jerusalem; `end ≤ start` = חוצה חצות; null/ריק = PERSONAL ידני בלבד, ORG 24/7.
- `location_mode` ב-PERSONAL: בחירת המשתמש יכולה רק להחמיר (coarse/off) וגוברת על השרת; השרת לא יכול לכפות precise. המצב בפועל מדווח ב-heartbeat (`effective_location_mode`).
- הגדרת coarse: lat/lon מעוגלים ל-0.01°, `loc_accuracy_m ≥ 1000`, altitude/speed/bearing/timing_advance = null. `cell_id` נשמר (פריט לבדיקה משפטית). ב-off: lat/lon null, `cell_id` נשמר.
- טווחי תצורה: לפי הצעת צוות הדאשבורד (סעיף 3.4 #28 באפיון הדאשבורד) — השרת מאמת, האפליקציה עושה clamp ולא קורסת.

## HTTP (A-CR12, D-CR6/7)
- batch: `Content-Encoding: gzip`, `Content-Type: application/json`; השרת פורס ידנית עם תקרת גודל.
- קודים: 401 = טוקן לא תקף/בוטל בלבד (האפליקציה דורשת שני 401 בהפרש 60 ש' לפני עצירה ומחיקה); 403 `device_mismatch`; 413; 422 שגיאת מעטפה; 429/503 + `Retry-After`.
- enroll: 400 `code_invalid`, 410 `code_expired`, 409 `code_used` / `ownership_mismatch` — בגוף `{detail_code}`.
- `rejected[].reason` רשימה סגורה: `invalid_sample_id`, `invalid_ts`, `ts_in_future`, `ts_too_old`, `invalid_enum:<field>`, `out_of_range:<field>`. כל הסיבות סופיות — האפליקציה לא מנסה שוב דגימה שנדחתה, רק סופרת.

## רישום (A-CR14, D-CR13)
- QR: `coverageops-enroll:v1?server=<https-url>&code=<CODE>`.
- `ownership` נקבע בקוד הצירוף ע"י ADMIN ומוחזר לאפליקציה; אי-התאמה → 409.
- PERSONAL: `consent_version`, `consented_at` חובה בגוף ה-enroll.
- `device_uuid` חדש בכל רישום מחדש (ברירת מחדל; שאלה פתוחה).

## פרטיות — תוספות (A-CR16/19, D-CR9)
- חדש ב-MVP: `POST /api/field/devices/me/withdraw` (device_token) — מבטל טוקן ומפעיל מחיקה/אנונימיזציה. חובה ל-PERSONAL.
- באנדרואיד קריאת נתוני תא דורשת הרשאת מיקום מדויק גם ב-coarse/off; הדילול נעשה באפליקציה. חייב להופיע בנוסח ההסכמה.
- שמירה: גולמי 90 יום; אגרגציה שעתית 400 יום; אגרגציה יומית ללא הגבלה.
- VIEWER: תא מפה מוצג רק אם תרמו לו ≥ 3 מכשירים שונים, ברזולוציית יום (אכיפה בשרת).

## נדחה לשלב מאוחר (לא MVP)
- endpoints ל-throughput ו-`active_test_daily_mb` (A-CR13); החלפת טוקן (D-CR16); certificate pinning; FCM.

---
# גרסה 2.1 — הכרעות סופיות של האדריכל (גוברות על כל מה שלמעלה)

הכרעות אלה סוגרות את הנקודות ששני הצוותים העלו בסבב התיקון. היכן שגוף המסמך סותר — סעיף זה קובע, והגוף יעודכן בתחילת שלב הפיתוח.

1. **`acked_commands`** — הצורה המחייבת: `[{id, status}]`, `status ∈ done | skipped_paused | failed | expired`. הצורה `[id]` מבוטלת (השרת לא נדרש לתמוך בה).
2. **פקודות במכשיר מושהה** — לא מבוצעות גם ב-ORG וגם ב-PERSONAL (`skipped_paused`). מגבלת "עד 5 דק'" חלה רק במצב `active|degraded`; למכשיר מושהה אין חסם עליון, והדאשבורד מציג זאת ליד כפתורי הפקודה. `sample_now` מייצר `trigger=manual`.
3. **סינון טריות** — חל רק על שדות האות והתא (rsrp/rsrq/sinr/rssi, cell_id, pci, neighbors, nr_leg). `service_state`, `data_state` וזמן ללא שירות נספרים תמיד. `cell_info_age_ms = null` = גיל לא ידוע → הדגימה נחשבת טרייה אך מסומנת `age_unknown` ונספרת בנפרד בדוח איכות הנתונים.
4. **`out_of_range`** — ערך רדיו מחוץ לטווח **מאופס ל-null בשרת** והדגימה מתקבלת (נספר ב-`normalized` בתשובה). דחיית דגימה שלמה נשארת רק ל-`invalid_sample_id`, `invalid_ts`, `ts_in_future`, `ts_too_old`, `invalid_enum:<field>`, ול-`out_of_range:lat|lon`. טבלת הטווחים היא חלק מ-`field_tier_vectors.json` (בבעלות ריפו CoverageOps) והאפליקציה מעתיקה אותה.
5. **Withdraw** — זמין רק ב-PERSONAL (מוחק/מאנונים לפי בחירת בעל המוצר, ברירת מחדל: מחיקה). ב-ORG הסרת מכשיר נעשית ע"י ADMIN בלבד והנתונים מאונונמים, לא נמחקים.
6. **Enroll אידמפוטנטי** — אותה שלישייה (code, device_uuid) בתוך 15 דק' מחזירה את אותה תוצאה עם טוקן חדש (הקודם מבוטל). מאושר לשני הצוותים.
7. **`422 consent_required`** — ל-enroll של PERSONAL בלי `consent_version`/`consented_at`.
8. **Timing advance** — מאופס גם ב-`location_mode=off`, לא רק ב-coarse.
9. **`cell_id` ב-coarse/off** — נשאר בחוזה, אך **פריסה למכשירים פרטיים (PERSONAL) חסומה עד חוות דעת משפטית** בנושא זה ובנושא נוסח ההסכמה. פיתוח ופריסה ל-ORG אינם תלויים בכך.
10. **`altitude_msl_m`, `timing_advance_us`** — null ב-Android 11–13 (זמינים רק מ-API 34). הדאשבורד לא מניח שהם קיימים.
11. **Heartbeat במצב מושהה** — best-effort בלבד; מכשיר מושהה תקין עלול להגיע ל"ללא קשר" (6 שעות) בלילה. לכן "ללא קשר" נשאר התראת מידע ואינו נספר במדד זמינות הצי.
12. **QR** — מותר פרמטר רשות `&ownership=ORG|PERSONAL` כרמז לחיסכון בצעד; השרת ממשיך לאכוף לפי הקוד.

## ממצאי אימות של האדריכל מול הריפו (נבדק בפועל)
- בסיס הנתונים בפיתוח ובפרודקשן: `postgis/postgis:16-3.4` — חלוקה לפי טווחים (partitioning) נתמכת; PostGIS מותקן אך העיצוב אינו תלוי בו.
- `render.yaml` מגדיר `plan: free` גם לשירות וגם ל-DB — ראו שער Go/No-Go לאירוח באפיון הדאשבורד. **מודול זה לא ייפרס על התוכנית החינמית.**
- `pytest` מופעל דרך ה-Makefile אך אינו מופיע ב-`backend/requirements.txt` — יש להוסיף קובץ תלויות פיתוח כחלק מאבן הדרך הראשונה.
- `MapView.tsx` מנהל שכבות Leaflet אימפרטיבית דרך refs (`L.LayerGroup`, `L.CircleMarker`, `L.ImageOverlay`) — שכבת המדידות במפה הראשית תיבנה באותו דפוס.
- תלות חדשה שאושרה: `segno` (QR בצד שרת). גרפים: SVG בכתיבה ידנית, ללא ספרייה.
