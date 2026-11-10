# CoverageOps Field Probe — אפיון אפליקציית אנדרואיד

> סטטוס: גרסה 2 — מעודכן לחוזה v2 ("גרסה 2 — הכרעות האדריכל") · סבב: אפיון + עיצוב בלבד (אין קוד מוצר)
> מסמך מחייב קודם: `SHARED_CONTRACT.md` כולל סעיף v2, שגובר על כל מה שלפניו. סטטוס בקשות השינוי — סעיף 13.
> מוקאפ מסכים: `android/mockup.html`.

**מוסכמות במסמך**
- "חוזה" = `SHARED_CONTRACT.md`. שמות שדות של החוזה מופיעים כ-`code`.
- **[לאימות]** = טענה טכנית שאני מעריך שהיא נכונה אך לא אימתתי מול תיעוד עדכני או מכשיר. כל פריט כזה נבדק ב-M0 (ספייק).
- **[הנחה]** = פרשנות שלי לחוזה במקום שבו הוא לא מגדיר. כל הנחה פתוחה מופיעה גם בסעיף 12.3.
- מספרי סוללה, נפחי אחסון וזמנים הם **הערכות** אלא אם נכתב אחרת.

---

## 1. מטרות, היקף ומחוץ להיקף

### 1.1 מטרות
האפליקציה היא "חיישן" — היא לא כלי ניתוח. התפקיד שלה: לייצר דגימות קליטה אמינות, מתויגות ומעוגנות בזמן ובמיקום, ולהעביר אותן בבטחה לשרת, תוך שקיפות מלאה למשתמש במכשיר.
המטרות נגזרות מארבע מטרות המוצר בחוזה:

| מטרת מוצר (חוזה) | מה האפליקציה חייבת לספק |
|---|---|
| (1) בקרה חיה | heartbeat סדיר, `monitoring_state` אמין, העלאה בהשהיה קצרה (`upload_interval_s`) |
| (2) ניתוח היסטורי | דגימות שלמות ומאומתות, אפס אובדן שקט (כל השמטה נספרת ומדווחת), אידמפוטנטיות |
| (3) חיזוי | כיסוי מרחבי טוב (טריגר תנועה), מזהי תא ותדר לכל דגימה |
| (4) כיול מודל הכיסוי | RSRP אמיתי + מיקום מדויק + `cell_id/pci/earfcn/band` להשוואה מול `coverage_results` ו-`cell_sites` |

### 1.2 היקף MVP (M0–M5, ראו סעיף 12)
- מנוע מדידה LTE + NR (SA, ו-NSA כולל `nr_leg`), dual SIM, שכנים (`rat`), מיקום, מדדי טריות (`cell_info_age_ms`, `loc_age_ms`).
- טריגרים: periodic, cell_change, service_change, movement, manual.
- foreground service מסוג `location`, הפעלה מחדש אחרי אתחול, הגנות מפני OEM battery killers.
- אחסון offline-first (Room), תור, batching עד 500, gzip, retry/backoff, אידמפוטנטיות.
- רישום (QR בפורמט `coverageops-enroll:v1` / קוד ידני), טוקן מוצפן ב-Keystore, ביטול טוקן, תצורה מרחוק.
- heartbeat מורחב (חוזה v2) כולל פקודות MVP: `sample_now`, `upload_now` (נמשכות בתשובת ה-heartbeat, בלי FCM).
- מצבי ORG ו-PERSONAL: מסך הסכמה, השהיה (`allow_user_pause`), חלון משמרת, `location_mode` coarse/off, **ביטול הסכמה דרך `POST /api/field/devices/me/withdraw`**.
- מסכים: רישום, הסכמה/יידוע, הרשאות, סטטוס חי, היסטוריה, אבחון וסנכרון, הגדרות/פרטיות, מכשיר מבוטל.
- `active_test` בסיסי: latency/jitter/loss בלבד (ללא throughput) — M5.

### 1.3 שלבים מאוחרים (לא ב-MVP)
- בדיקות throughput (`dl_kbps`, `ul_kbps`) ו-`active_test_daily_mb` — נדחו בחוזה v2.
- פקודות `active_test_now`, `send_diagnostics` (חוזה v2: שלב מאוחר) ודחיפה ב-FCM (נדחה). ב-MVP פקודות מגיעות רק בתשובת heartbeat, כלומר השהיה של עד 5 דק' במצב פעיל.
- Certificate pinning והחלפת טוקן — נדחו בחוזה v2.
- Managed configuration (Android Enterprise / MDM) לרישום אפס-מגע.
- מפת רקע בהיסטוריה (שרת אריחים פנימי של הארגון).
- הצפנת DB מלאה (SQLCipher) — החלטת ארכיטקט, ראו 9.3.
- הפצה ב-Google Play (מחייב התאמה למדיניות Play על background location ועל battery optimization exemption).

### 1.4 מחוץ להיקף במפורש
- כל ניתוח, חיזוי או השוואה מול מודל — בשרת/דאשבורד בלבד. באפליקציה רק סיווג רמת איכות (5 הרמות) לתצוגה.
- איסוף IMEI / IMSI / ICCID / מספר טלפון / MSISDN, אנשי קשר, SMS, יומן שיחות, רשימת אפליקציות, SSID/BSSID של Wi-Fi, Advertising ID, תוכן תעבורה.
- מדידות Wi-Fi, Bluetooth, מדידות קול (VoLTE MOS), שיחות בדיקה.
- גישה לנתוני מודם ברמת root / diag / AT commands / engineering mode.
- iOS.
- SDK צד שלישי לאנליטיקה או crash reporting (אין שליחת נתונים לשום יעד מלבד שרת CoverageOps).

---

## 2. החלטות פלטפורמה וארכיטקטורה

### 2.1 minSdk / targetSdk

| פרמטר | ערך | נימוק |
|---|---|---|
| `minSdk` | **30 (Android 11)** | `TelephonyDisplayInfo` (API 30) הוא הדרך הציבורית היחידה לזהות **NR_NSA** — ערך חובה ב-`network_type` בחוזה. גם `CellIdentity*.getBands()` (API 30), `CellInfo.getTimestampMillis()` (API 30, נדרש לזיהוי נתונים ישנים), `ServiceState.getNetworkRegistrationInfoList()` (API 30) ו-`ApplicationExitInfo` (API 30, לאבחון הריגות). ב-API 29 היינו מדווחים NSA כ-LTE בלי להבחין — הפרת חוזה שקטה. |
| `targetSdk` | **36 (Android 16)** | הגרסה היציבה העדכנית שאני בטוח בקיומה. ההפצה אינה דרך Play, אבל target עדכני נדרש כדי שהתנהגות FGS/הרשאות תהיה צפויה ונבדקת. מעבר ל-API 37 ייבחן כשייצא יציב **[לאימות]**. |
| `compileSdk` | = targetSdk | |

**סיכון:** אם יש בצי מכשירי ORG עם Android 10 — הם לא נתמכים. שאלה פתוחה לבעל המוצר (12.3 #1). חלופה: minSdk 29 עם `network_type` מוגבל (ללא NR_NSA) ומסומן כ-degraded — מוסיף מסלול קוד שני ל-telephony ולכן לא מומלץ.

### 2.2 ארכיטקטורה — מודולים (Gradle)

```
:app                    — Application, DI graph, Navigation, Manifest, build flavors (dev/staging/prod)
:core:model             — Sample, Heartbeat, Config, enums, QualityTier classifier (Kotlin טהור, ללא Android)
:core:designsystem      — טוקנים (צבעים/טיפוגרפיה מ-CoverageOps), רכיבי Compose משותפים, TierBadge
:core:database          — Room: entities, DAOs, migrations
:core:datastore         — DataStore: config נוכחי + last-known-good, מצב רישום, הגדרות משתמש
:core:security          — Keystore wrapper (AES-GCM), TokenStore
:core:network           — FieldApiClient (OkHttp), gzip, auth interceptor, error mapping, backoff
:probe:telephony        — TelephonySource (abstraction) + מימוש אנדרואיד; CellInfoValidator; NetworkTypeResolver
:probe:location         — LocationSource (FLP + LocationManager fallback), Coarsener, MockDetector
:probe:engine           — SamplingScheduler, MotionStateMachine, SampleAssembler, ActiveTestRunner
:probe:service          — ProbeService (FGS), BootReceiver, Watchdog worker, NotificationController
:sync                   — UploadWorker, HeartbeatSender (+ CommandExecutor), ConfigSync, QueueEvictor, WithdrawWorker
:feature:enroll         — QR + קוד ידני
:feature:consent        — הסכמה (PERSONAL) / יידוע (ORG)
:feature:permissions    — רשימת הרשאות + מדריכי OEM
:feature:status         — מסך חי
:feature:history        — ציר זמן + שרטוט מסלול
:feature:diagnostics    — אבחון וסנכרון
:feature:settings       — הגדרות, פרטיות, אודות
```

**שכבות** (בכל feature): UI (Compose) → ViewModel (StateFlow של UiState) → UseCases (domain) → Repositories (`:core:*`, `:probe:*`, `:sync`).
**עיקרון מפתח:** `:probe:telephony` ו-`:probe:location` חושפים ממשקים (`TelephonySource`, `LocationSource`) כך שכל לוגיקת המנוע נבדקת ביחידה עם snapshots מוקלטים ממכשירים אמיתיים (replay fixtures, סעיף 11.1).

**תהליך יחיד** (ללא `android:process` נפרד) — פשטות, ו-Room עם instance יחיד.

### 2.3 ספריות
גרסאות ייקבעו ב-M0 לפי הגרסה היציבה העדכנית בזמן ההקמה; אני לא מציין מספרים כדי לא לנחש.

| תחום | ספרייה | הערה |
|---|---|---|
| שפה | Kotlin, kotlinx.coroutines, Flow | |
| סריאליזציה | kotlinx.serialization (JSON) | שליטה מלאה בשמות שדות ו-null-handling מול החוזה |
| UI | Jetpack Compose, Material 3, Navigation Compose | RTL מובנה |
| מחזור חיים | AndroidX Lifecycle (ViewModel, `lifecycle-service`) | |
| DI | Hilt | כולל Hilt-Work |
| DB | Room (KSP) | |
| הגדרות | DataStore | |
| רקע | WorkManager | העלאה כשאין FGS, watchdog, תזכורת משמרת |
| רשת | OkHttp (+ MockWebServer בבדיקות) | בלי Retrofit — ה-API קטן ונרצה שליטה ב-gzip/Retry-After |
| מיקום | Google Play services location (`FusedLocationProviderClient`) | fallback ל-`LocationManager` במכשירים בלי GMS |
| QR | CameraX + ML Kit Barcode Scanning, **מודל bundled** | עובד גם בלי GMS. הרשאת CAMERA רק במסך הסריקה |
| הצפנה | Android Keystore + `javax.crypto` (AES/GCM) | **לא** `androidx.security:security-crypto` (EncryptedSharedPreferences), שלפי ידיעתי הוצא משימוש ב-2025 **[לאימות]** |
| בדיקות | JUnit, MockK, Turbine, Robolectric, Compose UI Test, UiAutomator, WorkManager testing | |

ללא SDK של Firebase/Analytics/Crashlytics ב-MVP. קריסות נרשמות מקומית ומוצגות במסך אבחון (שליחה לשרת — שלב מאוחר, דורש endpoint).

---

## 3. מנוע המדידה

### 3.1 ממשקי API ושימושם

| API | שימוש | זמינות | הרשאה |
|---|---|---|---|
| `SubscriptionManager.getActiveSubscriptionInfoList()` | רשימת SIM פעילים, `getSimSlotIndex()`, `getSubscriptionId()` | 22+ | `READ_PHONE_STATE` |
| `TelephonyManager.createForSubscriptionId(subId)` | TelephonyManager לכל SIM | 24+ | — |
| `TelephonyManager.requestCellInfoUpdate(executor, callback)` | רענון יזום של מדידות התא (לכל SIM דרך ה-TM שלו) | 29+ | `ACCESS_FINE_LOCATION` |
| `TelephonyManager.getAllCellInfo()` | ערך מטמון; משמש רק כ-fallback | 17+ | `ACCESS_FINE_LOCATION` |
| `TelephonyManager.registerTelephonyCallback(executor, cb)` | מאזינים: `CellInfoListener`, `ServiceStateListener`, `DisplayInfoListener`, `SignalStrengthsListener`, `DataConnectionStateListener` | 31+ | משתנה לפי מאזין |
| `PhoneStateListener` (deprecated) | אותם אירועים ב-API 30 בלבד | 30 | |
| `TelephonyDisplayInfo.getOverrideNetworkType()` | הבחנה LTE / NR_NSA | 30+ | `READ_PHONE_STATE` בחלק מהגרסאות **[לאימות]** |
| `TelephonyManager.getDataNetworkType()` | טכנולוגיית הנתונים | 24+ | `READ_PHONE_STATE` (נאכף מ-API 30) |
| `ServiceState` + `getNetworkRegistrationInfoList()` | מצב שירות, נדידה, RAT של רישום קול/נתונים | 30+ | `READ_PHONE_STATE` + מיקום לזהות תא |
| `TelephonyManager.getDataState()` | מצב חיבור נתונים | 1+ | — |
| `CellInfoLte/Nr/Wcdma/Gsm` + `CellIdentity*` + `CellSignalStrength*` | ערכים | ראו טבלה 3.2 | |
| `CellInfo.getCellConnectionStatus()` | זיהוי תא משרת ראשי (PRIMARY_SERVING) | 28+ | |
| `CellInfo.getTimestampMillis()` | גיל המדידה (בסיס `elapsedRealtime`) | 30+ | |
| `FusedLocationProviderClient.requestLocationUpdates` | מיקום | GMS | `ACCESS_FINE_LOCATION` |
| `LocationManager` (`FUSED_PROVIDER` מ-31, `GPS_PROVIDER`) | fallback בלי GMS | | |
| `Location.isMock()` | זיהוי מיקום מזויף | 31+ (`isFromMockProvider` ב-30) | |
| `BatteryManager`, `PowerManager.isInteractive()`, `PowerManager.isPowerSaveMode()`, `getLocationPowerSaveMode()` | סוללה, מסך, חיסכון | 21+/28+ | — |
| `Sensor.TYPE_SIGNIFICANT_MOTION` | התעוררות ממצב נייח | 18+ | — (חיישן לא קיים בכל מכשיר) |
| `ActivityManager.getHistoricalProcessExitReasons()` | סיבת הריגה קודמת | 30+ | — |

**הערה על dual SIM [לאימות]:** לפי קריאתי ב-AOSP, `getAllCellInfo()` מחזיר רשימה מאוחדת של כל המודמים, ואילו `requestCellInfoUpdate` על TM של SIM מסוים מחזיר את ה-phone של אותו SIM. לכן השיוך ל-`sim_slot` נעשה דרך `requestCellInfoUpdate` פר SIM, ולא דרך סינון הרשימה המאוחדת. יש לאמת במכשירי Qualcomm ו-MediaTek ב-M0.

**שלושה תנאים מקדימים לנתוני תא** (בלעדיהם `getAllCellInfo` ריק או חסר זהות תא):
1. `ACCESS_FINE_LOCATION` מאושר (מיקום **מדויק**; Approximate לא מספיק).
2. שירותי המיקום של המערכת דלוקים (`LocationManager.isLocationEnabled()`) **[לאימות לכל גרסה]**.
3. האפליקציה בחזית **או** FGS מסוג `location` רץ שהופעל מהחזית, **או** `ACCESS_BACKGROUND_LOCATION`.

המשמעות לפרטיות: גם ב-`location_mode=coarse|off` האפליקציה **חייבת** הרשאת מיקום מדויק מהמערכת, כי זה תנאי של אנדרואיד לקריאת זהות תא. הדילול מתבצע אצלנו לפני שמירה. זה חייב להיות כתוב במסך ההסכמה (סעיף 8.3), כי דיאלוג המערכת יבקש "מיקום מדויק".

### 3.2 טבלת מיפוי שדה-שדה (חוזה v2)

קיצורים: `L` = `CellIdentityLte`/`CellSignalStrengthLte`, `N` = `CellIdentityNr`/`CellSignalStrengthNr` (נגיעה ב-`CellSignalStrength` דורשת cast מ-`CellInfoNr.getCellSignalStrength()`), `W` = WCDMA, `G` = GSM. `U` = `CellInfo.UNAVAILABLE` (= `Integer.MAX_VALUE`), `UL` = `CellInfo.UNAVAILABLE_LONG` (= `Long.MAX_VALUE`).

| שדה בחוזה | מקור | API | אימות / הערות |
|---|---|---|---|
| `sample_id` | `UUID.randomUUID()` בעת הרכבת הדגימה, **ייחודי לכל SIM** | — | נקבע פעם אחת ולעולם לא נוצר מחדש (גם בניסיון שליחה חוזר) |
| `ts` | שעון קיר (`System.currentTimeMillis()`) **ברגע הטריגר** — משותף לכל ה-SIM של אותו טריגר (חוזה v2) | — | ISO8601 UTC עם `Z` ומילישניות. גיל המדידה של כל SIM נפרד ב-`cell_info_age_ms`. ראו 6.7 לקפיצות שעון |
| `cell_info_age_ms` | `elapsedRealtime_at_trigger − CellInfo.getTimestampMillis()` של התא המשרת | 30+ | נשלח כמות שהוא, **בלי לאפס שדות אות**; השרת מסנן (> 10000 → מחוץ ל-KPI). null אם ה-timestamp לא תקין (≤ 0 או בעתיד) או כשהדגימה נבנתה מ-fallback של `SignalStrength` בלבד |
| `lat`, `lon` | `Location.getLatitude/Longitude` — ה-fix האחרון **מתוך סשן הניטור הנוכחי** | — | null אם: אין fix בסשן הנוכחי, `location_mode=off`, `isMock()`. **לא** משתמשים ב-fix שנאסף לפני תחילת הסשן (ב-PERSONAL זה עלול לחשוף מיקום מחוץ למשמרת). ב-coarse: מעוגל (8.4) |
| `loc_age_ms` | `elapsedRealtime_at_trigger − Location.getElapsedRealtimeMillis()` | 33+ (`getElapsedRealtimeNanos` לפני כן) | null כש-lat/lon null. נשלח כמות שהוא; השרת מסנן |
| `loc_accuracy_m` | `Location.getAccuracy()` (`hasAccuracy`) | — | `float?` — חובה null כש-lat/lon null. ב-coarse: `max(accuracy, 1000)` |
| `altitude_m` | `Location.getAltitude()` (`hasAltitude`) | — | **מעל אליפסואיד WGS84** (חוזה v2). ההפרש מול גובה מעל פני הים בישראל הוא בסדר גודל של עשרות מטרים **[לאימות]**. null ב-coarse/off |
| `altitude_msl_m` | `Location.getMslAltitudeMeters()` אם `hasMslAltitude()`; אחרת `AltitudeConverter.addMslAltitudeToLocation()` | 34+ | ב-API 30–33: null. האם `AltitudeConverter` עובד offline ובאיזו עלות **[לאימות]**. null ב-coarse/off |
| `speed_mps` | `Location.getSpeed()` (`hasSpeed`) | — | null ב-coarse/off |
| `bearing_deg` | `Location.getBearing()` (`hasBearing`) | — | null ב-coarse/off או כש-speed < 1 m/s |
| `loc_provider` | `Location.getProvider()`: `"fused"`→`fused`, `"gps"`→`gps`, `"network"`→`network`; אין מיקום→`none` | — | |
| `network_type` | `NetworkTypeResolver` (3.3) | 30+ | |
| `mcc`, `mnc` | `L/N.getMccString()/getMncString()` של התא המשרת; fallback `TelephonyManager.getNetworkOperator()` | 28+ | מחרוזות; שמירת אפס מוביל (`"01"`). ישראל: `425` |
| `operator_name` | `TelephonyManager.getNetworkOperatorName()`; fallback `ServiceState.getOperatorAlphaLong()` | — | ערך גולמי, משתנה בין מכשירים. הדאשבורד ממפתח לפי `mcc+mnc` |
| `sim_slot` | `SubscriptionInfo.getSimSlotIndex()` | 22+ | 0-based. eSIM מקבל slot פיזי/לוגי לפי היצרן **[לאימות]** |
| `is_roaming` | `ServiceState.getRoaming()`; fallback `TelephonyManager.isNetworkRoaming()` | — | |
| `cell_id` | LTE: `L.getCi()` (ECI, 0..2²⁸−1); NR_SA: `N.getNci()` (0..2³⁶−1); UMTS: `W.getCid()`; GSM: `G.getCid()` | — | ב-`NR_NSA`: של עוגן ה-LTE. `U`/`UL`→null. **0 חשוד** → null + מונה **[לאימות]**. נשמר גם ב-coarse/off (חוזה v2; פריט משפטי) |
| `pci` | LTE: `L.getPci()` (0..503); NR: `N.getPci()` (0..1007); UMTS: `W.getPsc()` (0..511); GSM: `G.getBsic()` (0..63) | —/29+/—/28+ | מיפוי 2G/3G לפי חוזה v2. ייתכן שרשתות 2G/3G בישראל כבר כבויות, ואז המסלול כמעט לא ירוץ **[לאימות]** |
| `tac` | LTE: `L.getTac()`; NR: `N.getTac()` (24 bit); UMTS/GSM: `getLac()` | — | מיפוי LAC→`tac` לפי חוזה v2 |
| `earfcn_arfcn` | `L.getEarfcn()`; `N.getNrarfcn()`; `W.getUarfcn()`; `G.getArfcn()` | 24+/29+ | |
| `band` | `L/N.getBands()[0]`; fallback חישוב מטבלת EARFCN/NR-ARFCN מקומית | 30+ | מערך ריק נפוץ במכשירים מסוימים **[לאימות]** |
| `rsrp_dbm` | LTE ו-NR_NSA: `L.getRsrp()` (−140..−43); NR_SA: `N.getSsRsrp()` (−140..−44) | 26+/29+ | מחוץ לטווח המתועד → null (ערך לא אפשרי פיזית, לא "ישן") |
| `rsrq_db` | `L.getRsrq()` (−34..3); `N.getSsRsrq()` (−43..20) | | |
| `sinr_db` | `L.getRssnr()` (−20..30); `N.getSsSinr()` (−23..40) | 26+/29+ | חשד ליחידות 0.1 dB במכשירים מסוימים **[לאימות]**. **לא מתקנים בניחוש**: מחוץ לטווח → null + מונה; תיקון רק דרך טבלת quirks אחרי אישור בשטח |
| `rssi_dbm` | LTE: `L.getRssi()` (−113..−51); NR: null; UMTS: `W.getDbm()`; GSM: `G.getRssi()` | 29+/—/—/30+ | |
| `cqi` | LTE: `L.getCqi()` (0..15); NR: null ב-MVP | | בדרך כלל רק ב-RRC connected |
| `timing_advance` | LTE (כולל עוגן NSA): `L.getTimingAdvance()` (0..1282) | | **אינדקס LTE בלבד** (חוזה v2). null ב-coarse |
| `timing_advance_us` | NR_SA: `N.getTimingAdvanceMicros()` | 34+ | ב-API 30–33: null. זמינות בפועל לא ידועה **[לאימות]**. null ב-coarse |
| `nr_leg` | רק ב-`NR_NSA` — ראו 3.3 | 29+/30+ | null כשהמכשיר לא מדווח |
| `signal_level` | `CellSignalStrength.getLevel()` של התא המשרת; fallback `SignalStrength.getLevel()` | | 0–4 |
| `data_state` | `TelephonyManager(sub).getDataState()` | | `CONNECTED`→`connected`; `SUSPENDED`→`suspended`; `DISCONNECTED`/`CONNECTING`/`DISCONNECTING`→`disconnected` **[הנחה]**. SIM שאינו SIM הנתונים תמיד `disconnected` |
| `service_state` | `ServiceState.getState()` | | |
| `neighbors` | כל `CellInfo` עם `getCellConnectionStatus()==NONE` (או `!isRegistered`), ממוין לפי RSRP יורד, עד 8 | | שדה `rat: LTE\|NR\|UMTS\|GSM` (חוזה v2) לפי סוג ה-`CellInfo`. רק אם `collect_neighbors=true`. ב-NSA תא ה-NR שמזוהה כ-`nr_leg` לא נכלל בשכנים |
| `battery_pct` | `BatteryManager.getIntProperty(BATTERY_PROPERTY_CAPACITY)` | 21+ | |
| `is_charging` | `BatteryManager.isCharging()` | 23+ | |
| `screen_on` | `PowerManager.isInteractive()` | 20+ | |
| `trigger` | מנוע הדגימה (סעיף 4) | | |
| `active_test` | `ActiveTestRunner` (4.6) | | רק ב-`trigger=active_test` |

### 3.3 קביעת `network_type` ו-`nr_leg`

| קלט | תוצאה |
|---|---|
| `service_state ∈ {out_of_service, power_off}` | `NONE` |
| `getDataNetworkType()==NETWORK_TYPE_NR` | `NR_SA` |
| `NETWORK_TYPE_LTE` ו-`overrideNetworkType ∈ {OVERRIDE_NETWORK_TYPE_NR_NSA, OVERRIDE_NETWORK_TYPE_NR_ADVANCED}` | `NR_NSA` |
| `NETWORK_TYPE_LTE` (כולל override `LTE_CA`, `LTE_ADVANCED_PRO`) | `LTE` |
| `UMTS`, `HSDPA`, `HSUPA`, `HSPA`, `HSPAP`, `TD_SCDMA` | `UMTS` |
| `GSM`, `GPRS`, `EDGE` | `GSM` |
| `NETWORK_TYPE_IWLAN` או `UNKNOWN` (למשל SIM שאינו SIM הנתונים) | RAT של רישום WWAN מ-`NetworkRegistrationInfo` (domain PS, אחרת CS); אחרת סוג ה-`CellInfo` המשרת; אחרת `NONE` |

**NR_NSA (חוזה v2):** השדות הראשיים (`cell_id`, `pci`, `tac`, `earfcn_arfcn`, `band`, `rsrp/rsrq/sinr`, `timing_advance`) הם של עוגן ה-LTE, והרמה נקבעת לפיו. רגל ה-NR נשלחת ב-`nr_leg: {pci, arfcn, band, ss_rsrp_dbm, ss_rsrq_db, ss_sinr_db}`, לפי סדר המקורות:
1. `CellInfoNr` באותו SIM עם `getCellConnectionStatus()==CONNECTION_SECONDARY_SERVING` — זהות + אות.
2. אחרת `CellInfoNr` לא רשום עם האות החזק ביותר באותו SIM — זהות + אות. **[לאימות]**: ייתכן שזה תא NR שכן ולא הרגל בפועל; M0 יבדוק מול engineering mode.
3. אחרת `CellSignalStrengthNr` מתוך `SignalStrength.getCellSignalStrengths()` — אות בלבד; `pci/arfcn/band` = null.
4. אחרת `nr_leg` = null.

לפי הבנתי רוב ה-5G בישראל כיום הוא NSA **[לאימות]**, ולכן איכות `nr_leg` משמעותית. מקור 2 ומקור 3 הם היוריסטיקות. אם M0 יראה שמקור 2 מטעה ביצרן מסוים, טבלת ה-quirks תכבה אותו לאותו דגם. התנהגות NSA לפי יצרן לא ידועה עדיין **[לאימות]**.

### 3.4 אימות וסינון

**כללים כלליים**
- כל ערך int שווה ל-`U` או long שווה ל-`UL` → null.
- ערך מחוץ לטווח המתועד (טבלה 3.2) → null + מונה `out_of_range[field][model]`. זה סינון של ערך בלתי אפשרי, לא של ערך ישן — ולכן לא סותר את חוזה v2.
- תא משרת: `getCellConnectionStatus()==CONNECTION_PRIMARY_SERVING`; אם אין מידע (`CONNECTION_UNKNOWN`) → ה-`isRegistered()` הראשון שה-PLMN שלו תואם ל-SIM. אם יש כמה רשומים (CA, או דיווח כפול של שני SIM) → זה שה-`mcc/mnc` שלו תואם ל-`SubscriptionInfo` ושה-RSRP שלו הגבוה.
- ערכים זהים לחלוטין (RSRP+RSRQ+SINR+timestamp) בדגימות רצופות במשך יותר מ-3 דקות כשהמכשיר בתנועה → חשד ל-cache תקוע. הדגימה נשמרת ונשלחת, נרשם אירוע `CELL_INFO_FROZEN`, ונוספת סיבת degraded `cell_info_frozen`.

**נתונים ישנים (stale) — חוזה v2**
- `requestCellInfoUpdate` נקרא בכל דגימה עם timeout של 5 ש'. אם ה-callback לא חזר או חזר עם `onError` → `getAllCellInfo()` (מטמון).
- **האפליקציה לא מאפסת שדות אות בגלל גיל.** הערכים נשלחים כמות שהם עם `cell_info_age_ms` ו-`loc_age_ms`, והשרת מסנן (`cell_info_age_ms > 10000` לא נכנס ל-KPI).
- בממשק המשתמש (מסך חי, התראה) ערך עם `cell_info_age_ms > 10000` מוצג עם סימון "ישן · לפני X ש'" — אותו סף של השרת, כדי שהמכשיר והדאשבורד יציגו אותו דבר.
- **מגבלת קצב של הפלטפורמה:** אנדרואיד מגביל את תדירות הרענון בפועל של `requestCellInfoUpdate`, והמרווחים משתנים בין גרסאות, יצרנים ומסך דלוק/כבוי **[לאימות — יימדד ב-M0]**. לכן `sampling_interval_s` מתחת ל-10 ש' כנראה יחזיר ערכים כפולים; מתחת ל-10 ש' מוצגת אזהרה באבחון.

**מיקום**
- רק fixes מתוך סשן הניטור הנוכחי (ראו `lat/lon` ב-3.2). הגיל נשלח ב-`loc_age_ms`; אין סף ניתוק בצד המכשיר.
- `isMock()` → lat/lon null + אירוע `MOCK_LOCATION`.
- דיוק > 500 מ' במצב precise → נשלח (עם הדיוק), השרת יסנן.

**fallback כשאין CellInfo כלל** (תופעה מוכרת במכשירי MediaTek/Unisoc מסוימים, בעיקר ל-SIM שני **[לאימות]**): דגימה עם `SignalStrength.getCellSignalStrengths()` מה-listener (אות בלי זהות), `network_type`/`service_state`/`mcc`/`mnc` מ-ServiceState ו-TelephonyManager. `cell_id/pci/tac/earfcn` = null, `cell_info_age_ms` = null. אם זה המצב יותר מ-5 דק' → `degraded` עם סיבה `no_cell_identity`.

### 3.5 תופעות OEM ידועות/חשודות — לבדיקה במטריצה (11.3)
אני לא מציג כאן טענות לפי יצרן כעובדה. אלה תופעות שיש לחפש במכוון ב-M0 ולתעד בקובץ `oem_quirks` (מוגדר בקוד, לפי `MANUFACTURER+MODEL+SDK`):
1. `getAllCellInfo` ריק/null ל-SIM שני.
2. 0 במקום `UNAVAILABLE` עבור ci/pci/earfcn.
3. RSSNR ביחידות 0.1 dB או קבוע.
4. `getBands()` ריק תמיד.
5. NR NSA: `CellInfoNr` חסר, או קיים עם זהות UNAVAILABLE ואות תקין.
6. `getTimestampMillis()` לא מתעדכן (cache תקוע).
7. TA תמיד UNAVAILABLE.
8. שני תאים `isRegistered=true` לאותו SIM.
9. Huawei בלי GMS: FLP לא זמין → fallback ל-LocationManager.

---

## 4. אסטרטגיית דגימה

### 4.1 טריגרים

| `trigger` | מקור | כללים |
|---|---|---|
| `periodic` | טיימר המנוע | בתנועה: כל `sampling_interval_s` (ברירת מחדל 30). נייח: כל `stationary_interval_s` (300). הטיימר מתאפס אחרי כל דגימה מכל סוג |
| `movement` | callback מיקום | המרחק מהדגימה האחרונה ≥ `min_distance_m` (25) **ו**-עברו ≥ 5 ש' מהדגימה האחרונה (תקרת קצב; ברכב 90 קמ"ש = דגימה כל ~1 ש' בלעדיה) |
| `cell_change` | `CellInfoListener` / שינוי `cell_id` או `pci` משרת | debounce של 2 ש' (המתנה ליציבות), תקרה 6 בדקה לכל SIM (ping-pong) |
| `service_change` | `ServiceStateListener`, `DisplayInfoListener`, `DataConnectionStateListener` | שינוי ב-`service_state`, `network_type`, `is_roaming` או `data_state`. debounce 2 ש', תקרה 6 בדקה |
| `manual` | כפתור "דגום עכשיו" במסך הראשי/בהתראה | תקרה: אחת ל-5 ש' |
| `active_test` | מתזמן לפי `active_test_interval_s` (0 = כבוי) | 4.6 |

**דגימה = לכל SIM פעיל** (חוזה). כל טריגר מייצר N דגימות (אחת לכל SIM) עם אותו מיקום ואותו `trigger`, כל אחת עם `sample_id` משלה, ועם **`ts` משותף** (רגע הטריגר, חוזה v2). גיל המדידה של כל SIM מדווח בנפרד ב-`cell_info_age_ms`. טריגר `cell_change`/`service_change` של SIM אחד מייצר דגימה **רק ל-SIM שהשתנה** (חוסך דגימות כפולות; ה-SIM השני ממשיך במחזור שלו) **[הנחה]**.

### 4.2 מכונת מצבי תנועה (קצב אדפטיבי)

```
MOVING ──(תזוזה < min_distance_m במשך 3 דק' ו-speed < 0.5 m/s)──▶ STATIONARY
STATIONARY ──(Significant Motion / תזוזה ≥ 2×min_distance_m / cell_change)──▶ MOVING
```
| מצב | בקשת מיקום (FLP) | מרווח periodic |
|---|---|---|
| MOVING | `Priority.PRIORITY_HIGH_ACCURACY`, interval = `sampling_interval_s`, `minUpdateDistance` = 0 | `sampling_interval_s` |
| STATIONARY | `Priority.PRIORITY_BALANCED_POWER_ACCURACY`, interval = `stationary_interval_s` | `stationary_interval_s` |
| LOW_BATTERY (`battery_pct ≤ low_battery_threshold_pct` ולא בטעינה) | BALANCED גם בתנועה, interval = `max(sampling_interval_s×2, 60)` | כנ"ל; שכנים כבויים; `active_test` כבוי; העלאה כל `max(upload_interval_s, 300)` |
| `location_mode=coarse` | `PRIORITY_BALANCED_POWER_ACCURACY` תמיד | ללא שינוי |
| `location_mode=off` | אין בקשת מיקום | טריגר `movement` כבוי; מצב נייח נקבע רק לפי Significant Motion ו-cell_change |

**מנגנון ההתעוררות:** בתוך FGS ה-CPU עדיין יכול להירדם, ו-`delay()` של coroutine לא מעיר אותו. לכן "השעון" של המנוע הוא **callback המיקום של FLP**, שמעיר את התהליך. בכל callback: wake lock חלקי עם timeout 10 ש' → `requestCellInfoUpdate` → הרכבה → שמירה → שחרור. ב-STATIONARY עם מסך כבוי ו-Doze עמוק, אני מצפה שבקשות המיקום של אפליקציה עם FGS מסוג location ימשיכו להגיע, אבל בתדירות שעלולה להתארך **[לאימות — בדיקת Doze כפויה ב-11.5]**. גיבוי: `AlarmManager.setAndAllowWhileIdle` (לא exact; ב-Doze לא יותר מפעם ב-~9 דק') — אין שימוש ב-exact alarms ואין בקשת `SCHEDULE_EXACT_ALARM`.

### 4.3 heartbeat, פקודות ושילוב עם הדגימה (חוזה v2)
- **`active|degraded`:** heartbeat כל 5 דק', מתוזמן על אותן התעוררויות של ה-FGS (ב-STATIONARY המרווח 300 ש' מתלכד). נשלח מיד גם בכל שינוי `monitoring_state`, הרשאה או `effective_location_mode`.
- **`paused_*`:** אין FGS. WorkManager periodic בכל 15 דק' (המינימום של הפלטפורמה), עם constraint של רשת. **ללא מיקום וללא נתוני תא.** ב-Doze ובדליי App Standby העבודה נדחית לחלונות תחזוקה, לפעמים לשעות; "לפחות כל 15 דק'" הוא best-effort בלבד (ראו 13.2).
- **גוף הבקשה** (מעבר לשדות החוזה המקורי): `degraded_reasons` (רשימה סגורה, 5.6), `permissions.location: precise|approximate|none`, `applied_config_version`, `evicted_samples_total`, `last_sample_ts`, `location_services_enabled`, `effective_location_mode`, `acked_commands`.
- **תשובה:** `{config_version, server_time, commands: [{id, type, issued_at}]}`. `config_version` גבוה מהמקומי → משיכת config. `server_time` → חישוב הטיית שעון (6.7).
- **ביצוע פקודות:**

| פקודה | `active` / `degraded` | ORG `paused_*` | PERSONAL `paused_*` |
|---|---|---|---|
| `sample_now` | דגימה מיידית לכל SIM, `trigger=manual` **[הנחה — אין ערך trigger ייעודי]** | ack `skipped_paused` **[הנחה — החוזה לא קובע ל-ORG]** | ack `skipped_paused`, לא מבוצע (חוזה) |
| `upload_now` | העלאה מיידית של התור | העלאה מ-WorkManager (expedited) — מותר, לא אוסף דבר | ack `skipped_paused`, לא מבוצע (חוזה) |
| סוג לא מוכר | ack `unsupported` **[הנחה]** | כנ"ל | כנ"ל |

- מזהי פקודות שבוצעו נשמרים מקומית (24 שעות) ונשלחים ב-`acked_commands` של ה-heartbeat הבא; פקודה שמזהה שלה כבר טופל לא מבוצעת שוב (תשובה חוזרת אחרי ניתוק).
- **פורמט ack:** החוזה מגדיר `acked_commands: [id]` אבל גם "ack עם `skipped_paused`" — רשימת מזהים לא יכולה לשאת סטטוס. עד הבהרה האפליקציה תשלח `[{id, status: "done"|"skipped_paused"|"unsupported"}]`. נדרשת הכרעה (12.3, 13.2).
- `sample_now` ב-ORG מושהה **לא** מבוצע כי ביצוע מחייב הפעלת FGS מסוג location מהרקע. זה אפשרי רק עם מיקום ברקע + פטור סוללה, ובעיקר שובר את ציפיית המשתמש שהשהה. אם בעל המוצר רוצה שזה יבוצע — שאלה 12.3.
- **השהיית ביצוע:** "עד 5 דק'" מתקיים רק ב-`active|degraded`. במצב מושהה הפקודה מגיעה רק ב-heartbeat הבא (≥ 15 דק', best-effort).

### 4.4 העלאה
- מתוזמנת כל `upload_interval_s` כשיש פריטים בתור, **בתוך** אותה התעוררות (piggyback) כדי לא להעיר את הרדיו בנפרד.
- ב-STATIONARY: העלאה מתלכדת עם ה-heartbeat.
- מיד כשהרשת חוזרת (`ConnectivityManager.NetworkCallback`), עם jitter של 0–10 ש'.
- `upload_wifi_only=true`: העלאת דגימות רק ברשת `NET_CAPABILITY_NOT_METERED`. ה-heartbeat (קטן) נשלח בכל רשת **[הנחה]**.

### 4.5 תקציב סוללה

**שיטת הערכה:** פירוק לרכיבים, עם הנחות זרם מסדר גודל אופייני, על מכשיר ייחוס עם 4,500 mAh (1% ≈ 45 mAh). **אלה לא מדידות.** המספרים נועדו לזהות את הרכיב הדומיננטי ולקבוע יעד. המדידה האמיתית מתבצעת ב-M0 וב-11.5.

| רכיב | הנחה | תוספת (mA ממוצע) | %/שעה |
|---|---|---|---|
| GNSS בתנועה (interval 30 ש' משאיר את ה-GNSS חם) | 20–40 mA רציף | 20–40 | 0.45–0.9 |
| התעוררות CPU לדגימה (~0.5–1 ש' × 100–200 mA, כל 30 ש') | | 2–7 | 0.05–0.15 |
| רענון מדידות מודם | **לא ידוע** — המודם מודד ממילא ב-idle; העלות השולית צריכה להימדד | ? | ? |
| העלאה כל 60 ש' (זנב RRC של 5–10 ש' ב-100–250 mA) | | 8–40 | 0.2–0.9 |
| heartbeat (מתלכד עם העלאה) | | ~0 | ~0 |
| **סה"כ בתנועה** | | **30–90** | **≈0.7–2.0** |
| **סה"כ נייח** (BALANCED, 300 ש', ללא GNSS) | | 3–12 | **≈0.1–0.3** |

**מסקנה מההערכה:** אחרי ה-GNSS, ההעלאה כל 60 ש' היא כנראה הרכיב היקר — לא המדידה עצמה. המלצה לבעל המוצר: `upload_interval_s=300` לקבוצות שבהן הסוללה רגישה (PERSONAL), ו-60 ש' רק לקבוצות שבהן "בקרה חיה" קריטית (שאלה 12.3 #4).

**יעדי תקציב** (תוספת מעל baseline, מסך כבוי, יימדדו):
- ORG בתנועה: ≤ 2.5%/שעה. ORG נייח: ≤ 0.5%/שעה.
- PERSONAL — משמרת של 9 שעות (מעורב): ≤ 12% סה"כ.
- אם המדידה ב-M0 חורגת ביותר מ-50% מהיעד → חזרה לארכיטקט לפני M1.

### 4.6 `active_test` (M5)
- latency: 10 חיבורי TCP ל-host השרת בפורט 443 (handshake בלבד, בלי TLS ובלי בקשת HTTP), במרווח 200 ms, timeout של 2 ש' לניסיון. `latency_ms` = חציון, `jitter_ms` = ממוצע הפרשים מוחלטים בין ניסיונות עוקבים, `loss_pct` = כישלונות/10×100. לא דורש endpoint חדש.
- מגבלה: המדידה היא RTT לשרת CoverageOps, כולל המרחק אליו. אם השרת מתארח בחו"ל — הערכים כוללים את הקפיצה לחו"ל.
- רץ רק על SIM הנתונים (ברירת המחדל של המערכת), ומצורף לדגימה של אותו SIM.
- `dl_kbps`/`ul_kbps` = null תמיד ב-MVP (throughput נדחה בחוזה v2). הפקודה `active_test_now` — שלב מאוחר.

---

## 5. הישרדות ברקע

### 5.1 Foreground service

```xml
<uses-feature android:name="android.hardware.telephony" android:required="true"/>
<uses-feature android:name="android.hardware.camera.any" android:required="false"/>
<uses-feature android:name="android.hardware.location.gps" android:required="false"/>

<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>
<uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION"/>
<uses-permission android:name="android.permission.ACCESS_BACKGROUND_LOCATION"/>
<uses-permission android:name="android.permission.READ_PHONE_STATE"/>
<uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>
<uses-permission android:name="android.permission.FOREGROUND_SERVICE"/>
<uses-permission android:name="android.permission.FOREGROUND_SERVICE_LOCATION"/>
<uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED"/>
<uses-permission android:name="android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS"/>
<uses-permission android:name="android.permission.WAKE_LOCK"/>
<uses-permission android:name="android.permission.INTERNET"/>
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/>
<uses-permission android:name="android.permission.CAMERA"/>

<application
    android:allowBackup="false"
    android:dataExtractionRules="@xml/data_extraction_rules"
    android:networkSecurityConfig="@xml/network_security_config" ...>

  <service android:name=".probe.service.ProbeService"
           android:foregroundServiceType="location"
           android:exported="false"/>

  <receiver android:name=".probe.service.BootReceiver" android:exported="true">
    <intent-filter>
      <action android:name="android.intent.action.BOOT_COMPLETED"/>
      <action android:name="android.intent.action.MY_PACKAGE_REPLACED"/>
    </intent-filter>
  </receiver>
</application>
```
- סוג FGS יחיד: **`location`**. ההעלאה רצה בתוך אותו שירות (תעבורת רשת אינה דורשת סוג נוסף). לא משתמשים ב-`dataSync` (מוגבל בזמן מ-Android 15 **[לאימות]**) ולא ב-`specialUse`.
- `startForeground(id, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)`.
- `BootReceiver` מסונן לפעולות מוגנות בלבד (protected broadcasts) ולא מבצע דבר על intent אחר.
- **אין** `LOCKED_BOOT_COMPLETED`: ה-DB והטוקן נמצאים ב-credential-encrypted storage, זמינים רק אחרי פתיחת המכשיר.

### 5.2 הרשאות — סדר ו-UX
כל בקשה מקבלת קודם מסך הסבר בעברית ("למה צריך") ורק אז דיאלוג המערכת. מסך "רשימת הרשאות" (S4) מציג את כולן עם סטטוס ופעולה, ונפתח שוב אוטומטית כשהרשאה נשללת.

| # | הרשאה | ORG | PERSONAL | מתי | אם נדחה |
|---|---|---|---|---|---|
| 1 | מיקום מדויק (`FINE`+`COARSE` יחד, Android 12+) | חובה | חובה | אחרי הסכמה/יידוע | אי אפשר לנטר. אם נבחר "משוער" → הסבר + בקשה חוזרת (שדרוג); עד אז `degraded`, בלי זהות תא |
| 2 | `READ_PHONE_STATE` | חובה | חובה | מיד אחרי 1 | בלי dual SIM ובלי `network_type` אמין → `degraded` |
| 3 | `POST_NOTIFICATIONS` (13+) | חובה | **חוסם** | אחרי 2 | ORG: ממשיך, `degraded`. PERSONAL: לא מתחיל ניטור — החוזה מחייב התראה קבועה גלויה |
| 4 | מיקום ברקע ("לאפשר כל הזמן") — דף ההגדרות, בקשה נפרדת (חובה מ-Android 11) | חובה | אופציונלי | אחרי 3 | ORG: `degraded` (אין התאוששות עצמית אחרי הריגה/אתחול). PERSONAL: אין הפעלה אוטומטית במשמרת — רק "הקש להפעלה" (8.2) |
| 5 | פטור מאופטימיזציית סוללה (`ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`) | חובה | אופציונלי | אחרי 4 | ORG: `degraded` |
| 6 | הגדרות יצרן (autostart וכו') | מדריך | מדריך | רק ב-Xiaomi/Huawei/Oppo/Vivo/Samsung | לא ניתן לזהות בוודאות — "סימנתי שביצעתי" + זיהוי פערים בפועל (5.5) |
| — | CAMERA | — | — | רק בלחיצה על "סריקת QR" | הזנת קוד ידנית |

בנוסף: שירותי מיקום של המערכת דלוקים (`SettingsClient.checkLocationSettings` עם resolution; בלי GMS — `ACTION_LOCATION_SOURCE_SETTINGS`).

### 5.3 הפעלה מחדש
| אירוע | התנהגות |
|---|---|
| אתחול (`BOOT_COMPLETED`) | אם המכשיר רשום ו-ORG (או PERSONAL בתוך משמרת עם הפעלה אוטומטית) → `startForegroundService`. מגבלות Android 15 על FGS מ-BOOT_COMPLETED חלות לפי ידיעתי על סוגים אחרים, לא על `location` **[לאימות]**. אבל FGS שהופעל מהרקע **לא** מקבל גישת "while in use" למיקום, ולכן נדרשת `ACCESS_BACKGROUND_LOCATION`. בלעדיה: התראה "הקש להפעלת הניטור" |
| עדכון אפליקציה (`MY_PACKAGE_REPLACED`) | כנ"ל |
| הריגת תהליך | `START_STICKY` + `Watchdog` (WorkManager periodic 15 דק') שבודק אם הניטור אמור לרוץ ומפעיל FGS. הפעלת FGS מהרקע מ-Android 12 מותרת רק עם פטור (למשל פטור מאופטימיזציית סוללה) **[לאימות]** — ולכן ב-ORG הפטור חובה |
| עצירה ע"י המשתמש ב-FGS Task Manager (13+) / Force stop | לא ניתן למנוע (פרט ל-MDM, למטה). בהפעלה הבאה: `ApplicationExitInfo` → אירוע `USER_STOPPED` → heartbeat מיידי |

**MDM (ORG):** עם Device Owner ניתן להשתמש ב-`DevicePolicyManager.setUserControlDisabledPackages` (30+) כדי למנוע force stop ו-clear data, ולהעניק הרשאות מראש. תלוי ב-MDM של הארגון — שאלה 12.3 #3.

### 5.4 Doze ו-App Standby
- FGS מעלה את התהליך למצב שבו לפי ידיעתי מגבלות הרשת של Doze לא חלות עליו **[לאימות]**.
- WorkManager (watchdog, העלאה כשאין FGS, heartbeat במצב paused) **כן** נדחה ב-Doze ובדליי Standby. לכן כל מה שבזמן-אמת נעשה בתוך ה-FGS.
- `PowerManager.getLocationPowerSaveMode()`: במצב חיסכון ייתכן כיבוי GPS כשהמסך כבוי (`LOCATION_MODE_GPS_DISABLED_WHEN_SCREEN_OFF` ועוד). מזוהה → `degraded` עם סיבה `power_save_location`.
- ב-Android 16 יש החמרות במכסות JobScheduler גם לאפליקציות עם FGS **[לאימות]** — רלוונטי ל-watchdog בלבד.

### 5.5 OEM battery killers

| יצרן | מנגנון (מוכר בקהילה, משתנה בין גרסאות) | מיטיגציה |
|---|---|---|
| Xiaomi (MIUI / HyperOS) | Autostart כבוי כברירת מחדל; "Battery saver" לאפליקציה; נעילת אפליקציה ב-Recents | מדריך: Autostart = מופעל, חיסכון = "ללא הגבלות", נעילה ב-Recents. deep link להגדרה אם קיים (עטוף ב-try/catch) |
| Samsung (One UI) | "Sleeping apps" / "Deep sleeping apps", "Put unused apps to sleep" | מדריך: הוספה ל-"Never sleeping apps". ב-Knox/MDM — מדיניות מתאימה |
| Huawei / Honor (EMUI) | "App launch" אוטומטי (PowerGenie); במכשירים חדשים בלי GMS | מדריך: "נהל ידנית" + שלושת המתגים דלוקים. fallback ל-LocationManager |
| Oppo / OnePlus / Realme (ColorOS) | Auto launch, "Allow background activity", הקפאת רקע | מדריך: Auto launch + פעילות ברקע |
| Vivo | High background power consumption | מדריך |

ה-deep links של יצרנים שבירים ומשתנים בין גרסאות. לכן הם רק "קיצור דרך"; המדריך הכתוב (עם צילומי מסך שיופקו ב-M3) הוא העיקר.
**זיהוי בפועל (לא על סמך הצהרה):** פער בין דגימות > 3× המרווח הצפוי בזמן שהניטור אמור לפעול → אירוע `GAP` עם `ApplicationExitInfo.getReason()` (למשל `REASON_USER_REQUESTED`, `REASON_LOW_MEMORY`, `REASON_OTHER`). 2 פערים ב-24 שעות → באנר "נראה שהמכשיר עוצר את האפליקציה" + פתיחת מדריך היצרן.

### 5.6 `monitoring_state` ו-`degraded_reasons`

| ערך | מתי |
|---|---|
| `active` | הניטור אמור לפעול, ה-FGS רץ ואין אף סיבת degraded |
| `paused_by_user` | המשתמש עצר. PERSONAL תמיד מותר; ORG לפי `allow_user_pause` (ברירת מחדל true). כש-false המתג מנוטרל עם הסבר |
| `paused_schedule` | מחוץ לחלון `schedule` ואין הפעלה ידנית פעילה |
| `degraded` | הניטור אמור לפעול ו-FGS רץ (או אמור לרוץ), **אבל** קיימת לפחות סיבה אחת מהרשימה למטה |

**`degraded_reasons` — רשימה סגורה (חוזה v2: צוות האנדרואיד מגדיר, הדאשבורד מציג).** קוד חדש = עדכון חוזה.

| קוד | תווית לתצוגה בדאשבורד | תנאי | חל על |
|---|---|---|---|
| `location_permission_missing` | אין הרשאת מיקום | `FINE` ו-`COARSE` לא מאושרים | שניהם |
| `location_approximate_only` | מיקום משוער בלבד — אין נתוני תא | רק `COARSE` מאושר | שניהם |
| `location_services_off` | שירותי המיקום כבויים | `isLocationEnabled()==false` | שניהם |
| `phone_state_missing` | אין הרשאת מצב טלפון | `READ_PHONE_STATE` לא מאושר | שניהם |
| `notifications_denied` | התראות חסומות | `POST_NOTIFICATIONS` לא מאושר (ב-PERSONAL הניטור לא מתחיל כלל) | ORG |
| `background_location_missing` | אין מיקום ברקע | `ACCESS_BACKGROUND_LOCATION` לא מאושר | ORG |
| `battery_optimization_not_exempt` | אין פטור מהגבלת סוללה | `isIgnoringBatteryOptimizations()==false` | ORG |
| `no_cell_identity` | אין זהות תא | אין CellInfo משרת > 5 דק' כשיש שירות | שניהם |
| `cell_info_frozen` | נתוני תא קפואים | 3.4 | שניהם |
| `no_location_fix` | אין קליטת מיקום | אין fix > 10 דק' כש-`effective_location_mode != off` | שניהם |
| `power_save_location` | חיסכון חשמל מגביל מיקום | `getLocationPowerSaveMode() != LOCATION_MODE_NO_CHANGE` כשמצב חיסכון פעיל | שניהם |
| `low_battery_mode` | סוללה חלשה — קצב מופחת | `battery_pct ≤ low_battery_threshold_pct` ולא בטעינה | שניהם |
| `queue_evicting` | אחסון מלא — דגימות נמחקות | פינוי פעיל (6.5) | שניהם |
| `repeated_gaps` | המערכת עוצרת את האפליקציה | ≥ 2 פערים ב-24 שעות (5.5) | שניהם |
| `upload_blocked` | העלאה חסומה | 422 על מעטפה או 403 `device_mismatch` (6.3) | שניהם |

מצב טיסה **אינו** degraded — הדגימות עצמן משקפות אותו (`service_state=power_off`, שהוא "לא מסווג").

---

## 6. אחסון offline-first וסנכרון

### 6.1 סכמת DB מקומית (Room)

**`samples`**
| עמודה | סוג | הערה |
|---|---|---|
| `sample_id` | TEXT PK | UUID |
| `ts_ms` | INTEGER | UTC epoch ms (מה שנשלח כ-`ts`) |
| `elapsed_ms` | INTEGER | `SystemClock.elapsedRealtime()` במדידה |
| `boot_count` | INTEGER | `Settings.Global.BOOT_COUNT` |
| `sim_slot` | INTEGER | |
| `trigger` | TEXT | |
| `network_type` | TEXT | לתצוגה |
| `tier` | TEXT? | לתצוגה (מחושב מקומית) |
| `rsrp`, `rsrq`, `sinr` | REAL? | לתצוגה |
| `lat`, `lon` | REAL? | לתצוגה (כבר אחרי דילול) |
| `payload_json` | TEXT | Sample קנוני לפי החוזה — **זה מה שנשלח**, בדיוק כפי שנשמר |
| `upload_state` | INTEGER | 0 pending · 1 inflight · 2 accepted · 3 duplicate · 4 rejected |
| `batch_id` | TEXT? | |
| `attempts` | INTEGER | |
| `reject_reason` | TEXT? | |
| `priority` | INTEGER | 0 = periodic, 1 = movement, 2 = cell/service change, manual, active_test (לפינוי) |

אינדקסים: `(upload_state, ts_ms)`, `(ts_ms)`.

**`upload_batches`**: `batch_id`, `created_ms`, `sample_count`, `bytes_raw`, `bytes_gz`, `attempt`, `last_http_status`, `last_error`, `next_attempt_ms`.
**`events`** (יומן אבחון): `id`, `ts_ms`, `type` (`GAP`, `PROCESS_EXIT`, `PERMISSION_CHANGED`, `CONFIG_APPLIED`, `CONFIG_REJECTED_FIELD`, `UPLOAD_FAILED`, `CLOCK_JUMP`, `MOCK_LOCATION`, `CELL_INFO_FROZEN`, `EVICTION`, `STATE_CHANGED`, `COMMAND` (id, סוג, תוצאה), `UPLOAD_BLOCKED`, `WITHDRAW`, `REVOKED`), `detail_json`. שמירה 7 ימים / 5,000 שורות.
**`counters`**: מונים מצטברים (`generated`, `accepted`, `duplicates`, `rejected`, `evicted`, `out_of_range[field]`).
**DataStore**: config נוכחי + last-known-good, מצב רישום (`device_id`, `server_url`, `ownership`, `device_uuid`), הטוקן המוצפן (blob + IV), גרסת הסכמה, העדפות משתמש (location_mode מקומי מחמיר, הפעלה אוטומטית במשמרת).

### 6.2 תור ו-batching
1. `SELECT … WHERE upload_state=0 ORDER BY ts_ms LIMIT 500`.
2. עדכון ל-`inflight` עם `batch_id` חדש (טרנזקציה).
3. בניית מעטפה: `{device_id, sent_at, config_version (הנוכחי), samples:[payload_json…]}` — ה-JSON מחובר מהמחרוזות השמורות, בלי סריאליזציה מחדש.
4. gzip (`GZIPOutputStream`), `Content-Encoding: gzip`, `Content-Type: application/json` (חוזה v2).
5. בתגובה 2xx: כל `rejected[].sample_id` → `rejected` + `reject_reason`. **כל הסיבות סופיות** (`invalid_sample_id`, `invalid_ts`, `ts_in_future`, `ts_too_old`, `invalid_enum:<field>`, `out_of_range:<field>`): לא נשלח שוב, רק נספר במונה לפי סיבה ומוצג באבחון. כל השאר → `accepted` (החוזה מחזיר רק ספירות, לכן לא מבחינים מקומית בין accepted ל-duplicate; ספירת duplicates נשמרת במונים).
6. אם `config_version` בתגובה > המקומי → משיכת config (7.4).
7. באתחול התהליך: כל `inflight` חוזר ל-`pending` (שליחה חוזרת בטוחה — השרת אידמפוטנטי).

`ts_in_future` חוזר ונשנה הוא סימן להטיית שעון במכשיר — מוצג באבחון לצד ההטיה שמחושבת מ-`server_time`.

גודל משוער **[הערכה]**: Sample עם 4–8 שכנים ≈ 700–1,000 bytes JSON. מנה של 500 ≈ 350–500 KB גולמי, ≈ 50–90 KB אחרי gzip. יימדד על נתונים אמיתיים ב-M1.

### 6.3 Retry / backoff וטיפול בשגיאות (חוזה v2)

| תגובה | פעולה |
|---|---|
| 2xx | כמתואר ב-6.2; איפוס backoff |
| 401 | זרימת ביטול (7.3): שני 401 בהפרש 60 ש' → עצירה ומחיקה |
| 403 `device_mismatch` | ה-`device_id` במעטפה לא תואם לטוקן — באג/שחיתות מצב מקומי, לא ביטול. **לא מוחקים.** עצירת העלאות, `degraded/upload_blocked`, אירוע אבחון; heartbeat ממשיך (אין בו device_id). ניסיון חוזר אחרי שעה, ושוב בהפעלת האפליקציה |
| 413 | חציית גודל המנה (500→250→…→25), נזכר לסשן |
| 422 (שגיאת **מעטפה**) | ראו למטה |
| 429 / 503 | כיבוד `Retry-After` (שניות או תאריך HTTP); אחרת backoff |
| 5xx, timeout, שגיאת רשת | backoff מעריכי עם full jitter: בסיס 15 ש', ×2, תקרה 15 דק'. איפוס בשינוי רשת |

**422 — הוחלט לבטל את החצייה (bisect).** בחוזה v2 שגיאות ברמת דגימה חוזרות ב-`rejected[]` בתוך תשובת 2xx, ו-422 שמור לשגיאת מעטפה (device_id, sent_at, config_version, מבנה, יותר מ-500). חציית המנה לא מבודדת שום דבר במקרה כזה, כי כל תת-מנה תיכשל באותה דרך. הטיפול:
1. בנייה מחדש של המעטפה מאפס (sent_at חדש, ספירה) ושליחה אחת נוספת.
2. נכשל שוב → העלאות נעצרות, הדגימות **נשארות pending** (הן כנראה תקינות), `degraded/upload_blocked`, אירוע עם גוף התשובה (עד 1 KB). ניסיון חוזר כל שעה ובהפעלת האפליקציה. הפתרון הצפוי הוא עדכון אפליקציה או תיקון שרת.

Timeouts: connect 15 ש', read 30 ש', call 60 ש'. לכל היותר בקשת העלאה אחת במקביל.

### 6.4 אידמפוטנטיות
`sample_id` נוצר פעם אחת ונשמר ב-`payload_json`. שליחה חוזרת (timeout אחרי שהשרת כבר קיבל, קריסה ב-inflight) מגיעה עם אותם מזהים → השרת סופר `duplicates`. אין שום מסלול שמייצר מזהה חדש לדגימה קיימת.

### 6.5 תקרת אחסון ומדיניות פינוי
- דגימות `accepted/duplicate/rejected` נשמרות לתצוגת היסטוריה: ORG 7 ימים, PERSONAL 24 שעות, ואז נמחקות (job יומי).
- **תקרת pending:** 150,000 דגימות **או** 120 MB DB, המוקדם מביניהם. בהערכה של ~3,000 דגימות ליום ל-SIM (30 ש', מחצית מהזמן בתנועה) ושני SIM → כ-25 ימי עבודה בלי רשת. אם נשאר פחות מ-500 MB פנויים במכשיר — התקרה יורדת ל-20 MB.
- **פינוי מעל התקרה, לפי סדר:**
  1. דילול: מחיקת כל דגימת `periodic` שנייה ביום הישן ביותר.
  2. מחיקת `periodic` הישנות ביותר.
  3. מחיקת `movement` הישנות ביותר.
  4. רק אז `priority=2` הישנות ביותר.
- כל פינוי: מונה `evicted` + אירוע + `degraded/queue_evicting`. אין השמטה שקטה. המונה המצטבר מדווח ב-heartbeat כ-`evicted_samples_total` (חוזה v2).

### 6.6 קצב העלאה ונתונים סלולריים
העלאה ממצב offline ארוך מתבצעת במנות רצופות (בלי לחכות `upload_interval_s` בין מנות) עד לריקון, עם הפסקה של 2 ש' בין מנות. ברשת metered (סלולרי) עד 20 מנות ברצף (≈1–2 MB gz) ואז חזרה לקצב הרגיל — הגנה על חבילת גלישה פרטית ב-PERSONAL.

### 6.7 הטיית שעון
- `ts` = שעון קיר ברגע הטריגר (משותף לכל ה-SIM); `sent_at` = שעון קיר בזמן השליחה. השרת יכול להעריך הטיה כ-`received_at − sent_at`.
- **הטיה מול השרת:** `server_time` בתשובת heartbeat. `skew ≈ server_time − (t_send + t_recv)/2`. מוצג באבחון; מעל 2 דק' → אירוע. האפליקציה **לא** משנה `ts` לפי `server_time` (השרת מחליט).
- **קפיצת שעון במכשיר** (`ACTION_TIME_CHANGED`, או סטייה > 5 ש' בין `now_wall − now_elapsed` לערך שנשמר): לדגימות pending מאותו `boot_count` מחושב מחדש `ts = now_wall − (now_elapsed − elapsed_ms)` ומתעדכן ב-`payload_json` — סביר שהשעון אחרי התיקון (סנכרון רשת) נכון יותר. אירוע `CLOCK_JUMP`. דגימות מאתחול קודם לא מתוקנות. דגימות של אותו טריגר מתוקנות יחד, כך שהן ממשיכות לחלוק `ts`.

---

## 7. רישום, טוקן ותצורה מרחוק

### 7.1 זרימת רישום (חוזה v2)
1. מסך פתיחה: "סריקת QR" (ראשי) / "הזנת קוד ידנית", ובחירת **סוג מכשיר** (ORG/PERSONAL).
2. **פורמט QR:** `coverageops-enroll:v1?server=<https-url>&code=<CODE>`. פורמט לא מוכר או גרסה אחרת → "קוד לא נתמך — עדכנו את האפליקציה".
3. **אימות שרת:** ב-build של production יש רשימת hosts מותרים (build config). host אחר או לא-https → נדחה עם הודעה. מונע QR זדוני שמפנה לשרת זר.
4. **סוג המכשיר:** ה-QR לא נושא `ownership`, אבל האפליקציה צריכה לדעת אותו **לפני** הרישום — כדי להציג הסכמה (PERSONAL) או יידוע (ORG) לפני כל איסוף, וכדי לשלוח `ownership` + `consent_version` בגוף. לכן המשתמש בוחר סוג (ברירת מחדל: ORG). השרת דוחה אי-התאמה ב-409 `ownership_mismatch`.
5. **לפני** קריאת ה-enroll: ORG → מסך יידוע; PERSONAL → מסך הסכמה מלא. שום מדידה לא נאספת לפני השלב הזה.
6. `POST /api/field/devices/enroll` עם `{enrollment_code, device_uuid, model: Build.MODEL, manufacturer: Build.MANUFACTURER, os_version: "16 (API 36)", app_version: "1.0.0 (100)", ownership, label?, consent_version?, consented_at?}`. ב-PERSONAL `consent_version` + `consented_at` חובה (חוזה v2).
   - `device_uuid`: UUID v4 חדש בכל רישום (ברירת המחדל בחוזה v2; שאלה פתוחה).
   - `label`: אופציונלי. ב-PERSONAL מוצג רמז "אל תכתבו שם פרטי".
7. תגובה `{device_id, device_token, config, ownership}` → ה-`ownership` שמוחזר הוא מקור האמת ונשמר → שמירה מוצפנת של הטוקן → מסך הרשאות → התחלת ניטור (ORG) או מסך ראשי במצב משמרת/מושהה (PERSONAL).
8. **שגיאות** (`detail_code`):

| קוד | הודעה למשתמש |
|---|---|
| 400 `code_invalid` | "הקוד שגוי. בדקו את הקוד בדאשבורד ונסו שוב" |
| 410 `code_expired` | "הקוד פג תוקף. בקשו קוד חדש ממנהל המערכת" |
| 409 `code_used` | "הקוד כבר נוצל. בקשו קוד חדש" |
| 409 `ownership_mismatch` | "סוג המכשיר שבחרתם לא תואם לקוד." + כפתור "החלפה ל-[הסוג השני]". מעבר ל-PERSONAL מחייב מעבר במסך ההסכמה; מעבר ל-ORG מציג את מסך היידוע |
| אין רשת / 5xx | "אין חיבור לשרת. בדקו רשת ונסו שוב" (הקוד נשמר בשדה) |

### 7.2 אחסון `device_token`
- מפתח AES-256-GCM ב-Android Keystore (`setUserAuthenticationRequired(false)` — השירות חייב לרוץ ללא משתמש; StrongBox אם זמין, עם fallback ל-TEE).
- הטוקן נשמר מוצפן (ciphertext + IV) ב-DataStore. המפתח לא יוצא מה-Keystore.
- גיבוי מושבת (`allowBackup=false`, `dataExtractionRules` מוציא הכל). מפתחות Keystore גם לא עוברים בגיבוי.
- ה-interceptor מוסיף `Authorization: Bearer` **רק** לבקשות ל-host הרשום. הטוקן לא נכתב ללוג ולא מוצג במסך (רק 4 תווים אחרונים באבחון).

### 7.3 ביטול טוקן (401)
1. 401 מכל endpoint → heartbeat אימות אחרי 60 ש'.
2. 401 שני → מצב **REVOKED**: עצירת FGS, ביטול כל ה-Work, מחיקת הטוקן והמפתח, **מחיקת כל הדגימות המקומיות** (אין דרך להעלות אותן, והמכשיר כבר לא מורשה), שמירת אירוע בלבד.
3. מסך S9 "המכשיר הוסר מהמערכת" + "רישום מחדש".
חוזה v2 מתחייב ש-401 = טוקן לא תקף/בוטל בלבד. 403 `device_mismatch` **אינו** ביטול (6.3).

### 7.4 תצורה מרחוק
- **מקורות:** `config` בתגובת enroll; `GET /api/field/devices/me/config` כש-`config_version` בתגובת batch או heartbeat גבוה מהמקומי; בהפעלת האפליקציה; כל 6 שעות כגיבוי. גם במצב מושהה התצורה מתעדכנת (חוזה v2).
- **טווחים** (חוזה v2 → הצעת הדאשבורד, אפיון הדאשבורד 3.4 #28). השרת מאמת; האפליקציה עושה clamp לטווח, לא קורסת, ורושמת `CONFIG_REJECTED_FIELD` כשנדרש clamp:

| שדה | טווח |
|---|---|
| `sampling_interval_s` | 5–3600 |
| `min_distance_m` | 0–1000 (0 = טריגר movement כבוי) |
| `stationary_interval_s` | 30–3600; בנוסף האפליקציה מחילה `max(stationary_interval_s, sampling_interval_s)` |
| `upload_interval_s` | 15–3600 |
| `active_test_interval_s` | 0 או 300–86400 (ערך 1–299 → 300) |
| `low_battery_threshold_pct` | 5–50 |
| `allow_user_pause` | bool; ב-PERSONAL מתעלמים (תמיד true) |
| `location_mode` | `precise\|coarse\|off`; ערך לא מוכר → `coarse` (הכיוון המחמיר) |
| `schedule` | עד 14 חלונות; `days ⊆ 0..6` (0=ראשון); `start/end "HH:MM"` בשעון `Asia/Jerusalem`; `end ≤ start` = חוצה חצות; null/ריק = PERSONAL ידני בלבד, ORG 24/7. רשומה לא תקינה נזרקת, השאר נשמרות |

- **החלה אטומית:** החלפת אובייקט ה-config, רישום מחדש של בקשת המיקום והטיימרים, שמירת `config_version`, ודיווח `applied_config_version` ב-heartbeat. last-known-good נשמר לשחזור.
- **`effective_location_mode`:** ORG = ערך השרת. PERSONAL = המחמיר מבין בחירת המשתמש וערך השרת (`off` > `coarse` > `precise`). השרת לא יכול לכפות precise על PERSONAL (חוזה v2). מדווח ב-heartbeat.

---

## 8. ORG מול PERSONAL

### 8.1 טבלת הבדלים

| היבט | ORG | PERSONAL |
|---|---|---|
| מסך ראשון | יידוע (S2): מה נאסף, למה, מי רואה. כפתור "הבנתי" | הסכמה מלאה (S3) עם סעיפים, גלילה עד הסוף, תיבת סימון + "אני מסכים/ה". `consent_version` + `consented_at` נשלחים ב-enroll |
| מתי מנטרים | רציף 24/7. אם הוגדר `schedule` — לפיו | רק בחלון `schedule` **או** בהפעלה ידנית. אין schedule → ידני בלבד (חוזה v2) |
| התחלה בחלון משמרת | אוטומטית | ברירת מחדל: **התראה "המשמרת התחילה — הקש להפעלת הניטור"**. הפעלה אוטומטית רק אם המשתמש בחר בה **וגם** אישר מיקום ברקע ופטור סוללה (בלי אלה אנדרואיד לא מאפשר הפעלה מהרקע) |
| סוף משמרת | — | עצירה אוטומטית (תמיד אפשרית) + התראה "הניטור הופסק — סוף משמרת" |
| הפעלה ידנית | — | עד לעצירה ע"י המשתמש, מקסימום 12 שעות ואז עצירה אוטומטית עם התראה **[הנחה, שאלה 12.3]** |
| מתג עצירה | לפי `allow_user_pause` (ברירת מחדל true). false → המתג מוצג נעול עם הסבר. כל עצירה מדווחת ב-heartbeat מיידי | תמיד גלוי ותמיד פעיל: במסך הראשי ובהתראה |
| התראה קבועה | כן | כן, חובה. בלי `POST_NOTIFICATIONS` הניטור לא מתחיל |
| מיקום ברקע | חובה | אופציונלי |
| `location_mode` | לפי השרת | המשתמש יכול רק להחמיר (coarse/off), ובחירתו גוברת על השרת |
| שמירה מקומית של היסטוריה | 7 ימים | 24 שעות |
| SIM | כל SIM פעיל (חוזה) | כל SIM פעיל (חוזה). שאלה 12.3: האם לאפשר בחירת SIM |
| heartbeat כשמושהה | best-effort ≥ 15 דק', ללא מיקום | כנ"ל: מצב, סוללה, הרשאות, גודל תור — ללא מיקום ונתוני תא. **לבדיקה משפטית** |
| פקודות מרחוק כשמושהה | `upload_now` מבוצע; `sample_now` → `skipped_paused` **[הנחה]** | לא מבוצעות — ack `skipped_paused` (חוזה v2) |
| ביטול הסכמה | — (הסרה ע"י ADMIN בלבד) | "ביטול הסכמה והסרת המכשיר" בהגדרות → `POST /api/field/devices/me/withdraw` (8.6) |

### 8.2 השהיה
- PERSONAL: לחיצה על "השהה" → עצירה מיידית של FGS ושל המיקום (תוך פחות משנייה), דגימה אחרונה לא נוצרת, heartbeat `paused_by_user`, התראה מוסרת. "חידוש" → הפעלה מיידית (האפליקציה בחזית, ולכן מותר).
- ORG עם `allow_user_pause=true`: אותה התנהגות, ו-heartbeat מיידי. עם `false`: המתג נעול ("ההשהיה חסומה במדיניות הארגון"); כפתור ההשהיה לא מופיע בהתראה.
- השהיה עם זמן: "השהה לשעה / עד סוף המשמרת / עד שאחדש" (PERSONAL, ו-ORG כשמותר).

### 8.3 טיוטת נוסח הסכמה (PERSONAL) — לבדיקה משפטית
> **הסכמה לניטור קליטה סלולרית במכשיר הפרטי שלך**
>
> האפליקציה CoverageOps Field Probe מודדת את איכות הקליטה הסלולרית במכשיר ושולחת את המדידות למערכת CoverageOps של [שם הארגון]. המטרה היא למפות כיסוי סלולרי ולשפר את מודל הכיסוי. המטרה **אינה** מעקב אחריך.
>
> **מה נאסף, רק בזמן שהניטור פעיל:**
> • עוצמת ואיכות האות (RSRP, RSRQ, SINR), סוג הרשת, המפעיל ומזהה התא שאליו המכשיר מחובר.
> • מיקום המכשיר ברגע המדידה — [מדויק / מעוגל לכ-1 ק"מ / ללא מיקום], לפי ההגדרה שלך.
> • אחוז סוללה, האם המכשיר בטעינה והאם המסך דלוק.
>
> **מה לא נאסף לעולם:** מספר הטלפון, מזהי IMEI/IMSI, אנשי קשר, הודעות, שיחות, אפליקציות, תוכן גלישה או קבצים.
>
> **מתי:** רק בשעות המשמרת שהוגדרו לך, או כשהפעלת בעצמך. כשהניטור פעיל תופיע התראה קבועה. אפשר להשהות בכל רגע, מהמסך הראשי או מההתראה. גם כשהניטור מושהה האפליקציה מדווחת מדי פעם שהיא מותקנת ומושהית (סוללה והרשאות, ללא מיקום).
>
> **הרשאת מיקום:** אנדרואיד דורש הרשאת "מיקום מדויק" כדי לקרוא את פרטי התא הסלולרי, גם כשבחרת מיקום מעוגל או ללא מיקום. העיגול מתבצע במכשיר, לפני שהמידע נשמר או נשלח.
>
> **שים/י לב:** גם במיקום מעוגל או ללא מיקום נשלח מזהה התא הסלולרי. מזהה תא מאפשר להעריך באופן גס את האזור שבו המכשיר נמצא.
>
> **מי רואה:** מנהלים ומפעילים ב-CoverageOps יכולים לראות את מיקום המכשיר בזמן אמת ואת המסלול שלו. כל צפייה במסלול נרשמת. צופים רגילים רואים רק מפות מצטברות ללא זיהוי מכשיר.
>
> **כמה זמן:** מדידות גולמיות נשמרות עד 90 יום. נתונים מצטברים נשמרים לתקופה ארוכה יותר (שעתיים — 400 יום, יומיים — ללא הגבלה). הסרת המכשיר מהמערכת מוחקת או מאנונימת את המדידות הגולמיות שלו.
>
> **ביטול:** אפשר לבטל את ההסכמה בכל עת בהגדרות → "ביטול הסכמה והסרת המכשיר". הניטור נעצר מיד, הנתונים במכשיר נמחקים, והמערכת מקבלת בקשה למחוק או לאנונם את המדידות הגולמיות שלך. לשאלות: [איש קשר / ממונה הגנת פרטיות].
>
> ☐ קראתי והבנתי, ואני מסכים/ה לניטור לפי התנאים האלה.  [אני מסכים/ה]  [לא עכשיו]

### 8.4 מצבי מיקום coarse / off (חוזה v2)
- **coarse:** מיקום מבוקש ב-`PRIORITY_BALANCED_POWER_ACCURACY`; עיגול במכשיר ל-0.01° (≈1.1 ק"מ צפון-דרום, ≈0.94 ק"מ מזרח-מערב בקו רוחב 32°): `lat = round(lat/0.01)*0.01`; `loc_accuracy_m = max(accuracy, 1000)`; `altitude_m`, `altitude_msl_m`, `speed_mps`, `bearing_deg`, `timing_advance`, `timing_advance_us` = null. `cell_id` **נשמר**.
- **off:** אין בקשת מיקום; `lat/lon/loc_accuracy_m/loc_age_ms` = null, `loc_provider=none`; שדות התנועה null. `cell_id` **נשמר**. `timing_advance` נשמר **[הנחה — החוזה מאפס TA רק ב-coarse; ב-off ההיגיון זהה, שאלה 12.3]**.
- **פריט משפטי פתוח:** `cell_id` + `pci`, בשילוב מיקום אתר ידוע, מאפשרים לשחזר מיקום בערך ברזולוציה של אתר/סקטור, גם כש-TA מושמט. כלומר coarse/off אינם אנונימיים מבחינת מיקום. חוזה v2 משאיר את `cell_id`; זה חייב להיות כתוב בנוסח ההסכמה (מופיע ב-8.3) ולהיבדק משפטית (8.5).

### 8.5 שיקולי פרטיות בישראל — **פריטים לבדיקה משפטית, לא מסקנות**
1. תחולת חוק הגנת הפרטיות, התשמ"א-1981, ותיקון 13 (בתוקף מאוגוסט 2025 לפי ידיעתי) על נתוני מיקום של עובדים; האם נתוני מיקום/תנועה הם "מידע בעל רגישות מיוחדת" בהקשר זה.
2. תקנות הגנת הפרטיות (אבטחת מידע), התשע"ז-2017 — סיווג רמת האבטחה של המאגר והשלכות על אחסון מקומי במכשיר (9.3).
3. הנחיות הרשות להגנת הפרטיות ופסיקת בית הדין לעבודה בנושא ניטור עובדים: מידתיות, תכלית, שקיפות, והאם הסכמה של עובד על מכשיר פרטי היא הסכמה חופשית.
4. ORG: האם הודעת יידוע מספיקה, או שנדרשת גם מדיניות ארגונית כתובה/הסכמה בהסכם העבודה.
5. heartbeat במצב מושהה (PERSONAL): האם הדיווח "המכשיר חי, X% סוללה" מחוץ למשמרת הוא איסוף שדורש הסכמה נפרדת.
6. זכות עיון: האם בעל מכשיר פרטי צריך לראות את הנתונים שנאספו עליו מעבר להיסטוריה המקומית של 24 שעות.
7. `cell_id` נשמר גם ב-coarse/off (חוזה v2) — האם הוא "מידע מיקום" לעניין ההסכמה (8.4).
8. תקופות השמירה בחוזה v2 (גולמי 90 יום, אגרגציה שעתית 400 יום, יומית ללא הגבלה) והאם אגרגציה ברמת מכשיר נחשבת מידע אישי.
9. היקף המחיקה/אנונימיזציה ב-withdraw, ומה נשאר באגרגציות.
10. מיקום השרת (ישראל/חו"ל) והשלכות על העברת מידע לחו"ל.

### 8.6 ביטול הסכמה (`withdraw`) — PERSONAL, MVP
זרימה מ-S8 "ביטול הסכמה והסרת המכשיר":
1. מסך אישור: "הניטור ייעצר, הנתונים במכשיר יימחקו, ונבקש מהשרת למחוק או לאנונם את המדידות הגולמיות שלך. לא ניתן לבטל." [ביטול הסכמה] [חזרה].
2. **מקומית, מיד** (גם בלי רשת): עצירת FGS ו-WorkManager, `monitoring_state` נעול על `paused_by_user`, מחיקת כל הדגימות (pending כלולות — לא שולחים עוד נתונים אחרי ביטול), מחיקת ההיסטוריה. נשמרים רק הטוקן ודגל `withdraw_pending`.
3. `POST /api/field/devices/me/withdraw` (device_token).
   - 2xx, או 401 (הטוקן כבר בוטל) → מחיקת הטוקן, המפתח וכל המצב; מסך S9 בגרסת "ההסכמה בוטלה".
   - אין רשת / 5xx / 429 → ניסיון חוזר ב-WorkManager (expedited, constraint רשת, backoff עד 15 דק'). במסך: "ההסכמה בוטלה במכשיר. הבקשה לשרת תישלח כשתהיה רשת." השרת עדיין מחזיק את המדידות שכבר הועלו עד שהבקשה מגיעה.
4. אין "ביטול של הביטול": אחרי שלב 2 האפשרות היחידה היא רישום מחדש עם קוד חדש (ו-`device_uuid` חדש).

---

## 9. אבטחה

### 9.1 תעבורה
- HTTPS בלבד. `network_security_config`: `cleartextTrafficPermitted=false`; ב-release אמון רק ב-CA של המערכת (לא ב-CA של משתמש); ב-build `dev` מותר cleartext ל-`10.0.2.2`/`localhost` בלבד.
- OkHttp `ConnectionSpec.MODERN_TLS` (TLS 1.2+, עדיפות ל-1.3).
- **Certificate pinning — לא ב-MVP** (נדחה בחוזה v2). אם יוחזר בעתיד: pin ל-SPKI של שורשי ISRG Root X1 + X2 ולא לתעודת leaf, כי Let's Encrypt מחליף תעודות כל ~60–90 יום.
- Allowlist של hosts לרישום (7.1).

### 9.2 אימות
- `device_token` בלבד, Bearer, רק ל-host הרשום. אין JWT של משתמש ואין login של משתמש באפליקציה.

### 9.3 הגנה על נתונים במכשיר
- Sandbox של האפליקציה + File-Based Encryption של אנדרואיד (credential-encrypted storage, נעול עד פתיחה ראשונה של המכשיר אחרי אתחול).
- `allowBackup=false` + `dataExtractionRules` שמוציא הכל.
- הטוקן מוצפן ב-Keystore (7.2).
- ה-DB **לא** מוצפן ברמת האפליקציה ב-MVP. נימוק: FBE מגן במנוחה; הנתונים המקומיים מוגבלים בזמן (24 שעות/7 ימים להיסטוריה); SQLCipher מוסיף תלות native ועלות. **החלטת ארכיטקט** (12.3 #14), בהתאם לסיווג לפי תקנות אבטחת מידע.
- release: R8/minify, `debuggable=false`, אין לוגים עם מיקום או טוקן (logger עם רמות; release משמיט DEBUG/VERBOSE).
- המסך לא מסומן `FLAG_SECURE` (אין סודות על המסך; צילומי מסך עוזרים לתמיכה).

### 9.4 מה לעולם לא נאסף
IMEI, MEID, IMSI, ICCID, מספר טלפון/MSISDN, Android ID, Advertising ID, חשבונות Google, אנשי קשר, SMS, יומן שיחות, רשימת אפליקציות מותקנות, SSID/BSSID ו-scan של Wi-Fi, Bluetooth, תוכן תעבורה, היסטוריית גלישה, קבצים, מיקרופון. מצלמה משמשת רק לסריקת QR, והתמונה לא נשמרת. בדיקה אוטומטית ב-CI: רשימת ההרשאות ב-merged manifest חייבת להיות זהה לרשימה ב-5.1 (מונעת "זליגת" הרשאה מספרייה).

---

## 10. מסכים

הכל בעברית, RTL, ערכת הצבעים הכהה של CoverageOps (`--bg #0a0e14`, `--panel #111928`, `--accent #00d4ff`…), פונטים Rajdhani (ספרות/ערכים) ו-IBM Plex Sans Hebrew (טקסט). ערכים מספריים ויחידות (`-88 dBm`) מוצגים ב-LTR מבודד.

**חובה (חוזה v2): בכל מקום שמוצגת רמה — תג, מקרא, לשונית SIM, התפלגות, התראה — מופיעים יחד צבע + תווית טקסט + גליף פסים 0–4.** כל מקרא מציין את המדד והספים ("RSRP / SS-RSRP dBm").

**רכיב רמת איכות (TierBadge)** — כללי סיווג לפי חוזה v2 (ב-`:core:model`, נבדק מול `field_tier_vectors.json`). הקלט: `rsrp_dbm` (LTE, עוגן LTE ב-NR_NSA, או SS-RSRP ב-NR_SA) ו-`service_state`:
| רמה | תנאי | גליף |
|---|---|---|
| מצוין | `x ≥ −85` | 4 פסים מלאים |
| טוב | `−95 ≤ x < −85` | 3 |
| בינוני | `−105 ≤ x < −95` | 2 |
| חלש | `−115 ≤ x < −105` | 1 |
| שולי/אין | `x < −115`, או `service_state ∈ {out_of_service, emergency_only}` (בכל הטכנולוגיות, גם עם RSRP טוב) | 0 (4 מסגרות ריקות) |
| **לא מסווג** (אפור, מחוץ ל-5 הרמות) | `service_state = power_off`; או `in_service` בלי RSRP (UMTS/GSM או null) | 4 מסגרות אפורות עם קו אלכסוני |

"לא מסווג" לא נכלל במכנה של אחוזי הרמות (S6). דגימה ישנה (`cell_info_age_ms > 10000`) מסווגת כרגיל אבל מסומנת "ישן" בממשק.

| # | מסך | מטרה ותוכן | מצבים |
|---|---|---|---|
| S0 | Router | בוחר יעד לפי מצב: לא רשום → S1; רשום בלי הסכמה → S2/S3; חסרות הרשאות חובה → S4; REVOKED → S9; אחרת S5 | — |
| S1 | רישום | לוגו, "סריקת QR" (CameraX, מסגרת כוונון), "הזנת קוד ידנית" (קוד בפורמט `XXXX-XXXX`, סוג מכשיר, שרת), host השרת מוצג לאישור | טעינה (רישום); שגיאה: קוד שגוי/פג/נוצל, host לא מורשה, אין רשת; הרשאת מצלמה נדחתה → מעבר לקוד ידני |
| S2 | יידוע ORG | סעיפי "מה נאסף / מי רואה / מתי", "הבנתי" | — |
| S3 | הסכמה PERSONAL | נוסח 8.3, גלילה חובה, תיבת סימון, בחירת מצב מיקום, "אני מסכים/ה" / "לא עכשיו" | כפתור מנוטרל עד גלילה + סימון; "לא עכשיו" → חזרה ל-S1 בלי רישום |
| S4 | הרשאות | צ'קליסט: מיקום מדויק, מצב טלפון, התראות, מיקום ברקע, פטור סוללה, שירותי מיקום, הגדרות יצרן. כל שורה: סטטוס (✓ / ! / –), "חובה/מומלץ", כפתור פעולה. אחוז השלמה | הכל תקין → "התחל ניטור"; חובה חסרה → הכפתור מנוטרל עם הסבר; נשלל אחרי "אל תשאל שוב" → כפתור "פתח הגדרות" |
| S4b | מדריך יצרן | צעדים ממוספרים ליצרן שזוהה, deep link אם זמין, "סימנתי שביצעתי" | יצרן לא מוכר → הנחיות כלליות |
| S5 | סטטוס חי (בית) | כרטיס גדול: רמה + RSRP; RSRQ/SINR/RSSI; סוג רשת; מפעיל + MCC-MNC; תא משרת (ECI/NCI, PCI, TAC, EARFCN, band); לשוניות SIM 1/SIM 2; מיקום (דיוק, ספק); שורת סנכרון (תור, העלאה אחרונה, heartbeat); מתג ניטור; "דגום עכשיו"; פס מצב (ORG/PERSONAL, משמרת) | **פעיל**; **מושהה** (כרטיס אפור, "הניטור מושהה" + חידוש); **מחוץ למשמרת** (מתי המשמרת הבאה + "הפעל ידנית"); **degraded** (באנר כתום עם הסיבה ופעולה לתיקון); **אין שירות** (שולי/אין + "אין שירות"); **מצב טיסה**; **offline** ("X דגימות ממתינות"); **הרשאה חסרה** (באנר חוסם + מעבר ל-S4); **טעינה ראשונה** (שלד עד הדגימה הראשונה); **SIM אחד** (בלי לשוניות) |
| S6 | היסטוריה | בורר טווח (שעה / 6 שעות / 24 שעות / 7 ימים ב-ORG); גרף RSRP על ציר זמן עם רצועות רמה; שרטוט מסלול **ללא מפת רקע** (נקודות צבועות לפי רמה, סרגל קנה מידה) — ללא בקשות אריחים לשרת חיצוני, מטעמי פרטיות; התפלגות זמן לפי רמה (ברים + טקסט); רשימת אירועים (החלפת תא, אובדן שירות) | **ריק** ("עדיין אין מדידות בטווח הזה"); **טעינה**; coarse/off → שרטוט המסלול מוחלף בהודעה "מיקום מעוגל — מסלול לא מוצג" |
| S7 | אבחון וסנכרון | מונים: נוצרו / התקבלו / כפולים / נדחו (לפי סיבה מהרשימה הסגורה) / פונו; `degraded_reasons` פעילות; פקודות אחרונות מהשרת ותוצאתן (done / skipped_paused); `effective_location_mode`; הטיה מול `server_time`; תור (pending, inflight, גודל MB); העלאה אחרונה + סטטוס HTTP + ניסיון הבא; heartbeat אחרון; config (גרסה, ערכים, מתי הוחל); הרשאות; מצב סוללה/חיסכון; הטיית שעון; יומן אירועים; פערים ו-`ApplicationExitInfo`; מונים out-of-range לפי שדה; device_id, גרסה, 4 תווים אחרונים של הטוקן. פעולות: "העלה עכשיו", "שלח heartbeat", "ייצוא אבחון" (קובץ JSON בלי מיקום, דרך share sheet) | **offline**; **שגיאת שרת** (קוד + זמן ניסיון הבא); **תור ריק** |
| S8 | הגדרות ופרטיות | סוג מכשיר, תווית, שרת; מצב מיקום (PERSONAL: רק החמרה); חלון משמרת (קריאה בלבד, מהשרת); "התחלה אוטומטית במשמרת" (PERSONAL); "מה נאסף" (קישור לנוסח ההסכמה + גרסה); "ביטול הסכמה והסרת המכשיר" (PERSONAL, זרימה 8.6); מתג השהיה נעול כש-`allow_user_pause=false` (ORG); אודות/רישיונות | withdraw ממתין לרשת ("הבקשה תישלח כשתהיה רשת") |
| S9 | המכשיר הוסר | "המכשיר הוסר מהמערכת ע"י מנהל. הנתונים המקומיים נמחקו." + "רישום מחדש" | גרסה אחרי withdraw: "ההסכמה בוטלה והמכשיר הוסר" |
| S10 | תחילת משמרת (PERSONAL) | נפתח מההתראה: "המשמרת שלך התחילה (08:00–17:00). להפעיל ניטור?" [הפעל] [לא היום] | — |
| N1 | התראה קבועה (FGS) | ערוץ "ניטור קליטה", `IMPORTANCE_LOW` (בלי צליל). כותרת: "ניטור פעיל · טוב −88 dBm"; טקסט: "5G NSA · Cellcom · תור 0". **גליף הרמה:** ה-small icon בשורת המצב מתחלף בין 6 גרסאות (פסים 0–4 + לא מסווג) — אנדרואיד צובע small icons במונוכרום, ולכן הגליף + הטקסט נושאים את המידע גם בלי צבע; בתוך גוף ההתראה הצבע מופיע רק כשהמערכת מכבדת `setColor` **[לאימות לפי OEM]**; פעולות: "השהה" (PERSONAL; ORG לפי מדיניות), "דגום עכשיו". מתעדכנת בכל דגימה. ב-Android 14+ המשתמש יכול להחליק אותה **[לאימות]** — השירות ממשיך ונראה ב-Task Manager | פעיל / degraded ("ניטור חלקי — חסרה הרשאה") / offline ("12 דגימות ממתינות") |
| N2 | התראות מידע | ערוץ נפרד, `IMPORTANCE_DEFAULT`: תחילת/סוף משמרת, הרשאה נשללה, "הקש להפעלת הניטור" אחרי אתחול, עצירה אוטומטית אחרי 12 שעות | — |

נגישות: TalkBack לכל רכיב (TierBadge מוקרא "רמת קליטה: טוב, מינוס 88 dBm"), יעדי מגע ≥ 48dp, תמיכה בהגדלת גופן עד 200% בלי חיתוך, ניגודיות ≥ 4.5:1 לטקסט.

---

## 11. תוכנית בדיקות

### 11.1 בדיקות יחידה (JVM)
- **CellInfoValidator:** כל sentinel (`U`, `UL`), כל גבול טווח ±1, ערכי 0 חשודים, RSSNR ×10.
- **NetworkTypeResolver:** מטריצה מלאה (`dataNetworkType` × `overrideNetworkType` × `service_state` × SIM נתונים/לא-נתונים).
- **בחירת תא משרת:** CA, שני רשומים, `CONNECTION_UNKNOWN`, NSA עם/בלי `CellInfoNr`.
- **QualityTier:** מורץ על **`field_tier_vectors.json`** — הבעלים: צוות הדאשבורד, בריפו של CoverageOps (חוזה v2). האפליקציה מחזיקה עותק ב-`core/model/src/test/resources/field_tier_vectors.json`, עם קובץ `field_tier_vectors.source` שמתעד commit/hash של המקור. בדיקת CI משווה את ה-hash לערך המתועד, כך שעדכון העותק הוא תמיד מכוון (PR נפרד). אין עריכה מקומית של הקובץ; וקטור חסר → בקשה לצוות הדאשבורד. בנוסף בדיקות משלנו שלא מחליפות את הקובץ: בחירת `rsrp` לפי `network_type` (עוגן ב-NSA), ו-6 גרסאות הגליף.
- **מדדי טריות:** חישוב `cell_info_age_ms`/`loc_age_ms`, timestamp לא תקין → null, **שדות אות לא מתאפסים** בגלל גיל, fix מלפני הסשן לא נשלח.
- **`nr_leg`:** ארבעת מקורות ה-fallback (3.3), שכן NR שהוא הרגל לא מופיע ב-neighbors; `rat` בשכנים.
- **פקודות:** טבלת 4.3 (מצב × בעלות × סוג), דה-דופליקציה לפי id, `skipped_paused` ב-PERSONAL.
- **withdraw:** מחיקה מקומית מיידית, ניסיון חוזר offline, 401 = הצלחה.
- **מכונת מצבי תנועה:** מעברים, low battery, coarse/off.
- **SampleAssembler:** dual SIM → N דגימות, `sample_id` ייחודי, **`ts` משותף** בטריגר, cell_change רק ל-SIM שהשתנה; coarse/off מאפסים בדיוק את השדות של 8.4 ו-`cell_id` נשמר.
- **סריאליזציה מול החוזה:** כל Sample מאומת מול JSON Schema שנגזר מהחוזה (מקור משותף עם השרת). שמות שדות, null-ים, ISO8601 עם `Z`.
- **Schedule:** ימים, חציית חצות, מעברי שעון קיץ/חורף ב-`Asia/Jerusalem`, רשימה ריקה.
- **Config validation:** clamp לכל טווח של 7.4, `location_mode` לא מוכר, `effective_location_mode` (PERSONAL מחמיר בלבד), `allow_user_pause` מתעלם ב-PERSONAL, החלה אטומית.
- **Backoff ושגיאות:** jitter, תקרה, `Retry-After` (שניות ותאריך), 422 → בנייה מחדש + עצירה (בלי bisect), 403 `device_mismatch` → בלי מחיקה, `rejected[]` סופי.
- **פינוי תור:** סדר העדיפויות, מונים.
- **תיקון שעון:** אותו boot / boot אחר.
- **Coarsener:** עיגול, השמטת שדות.
- **Replay fixtures:** snapshots של `CellInfo` שהוקלטו ממכשירי המטריצה ב-M0 (Parcel → JSON) רצים דרך כל ה-pipeline ומשווים ל-golden output.

### 11.2 בדיקות אינסטרומנטציה
- Room migrations (כל גרסה → הבאה).
- ProbeService: התחלה/עצירה, `startForeground` עם type, התנהגות כשהרשאה נשללת תוך כדי ריצה.
- WorkManager (`TestDriver`): watchdog, העלאה, heartbeat.
- `FieldApiClient` מול MockWebServer: `Content-Encoding: gzip`, 401 כפול → REVOKED ומחיקה, 403 `device_mismatch`, 413, 422 מעטפה, 429, ניתוק באמצע, כל `detail_code` של enroll, תשובת heartbeat עם `commands`/`server_time`, withdraw.
- זרימות הרשאה עם UiAutomator (כולל "רק הפעם", "משוער", דחייה כפולה).
- מוות תהליך באמצע inflight → אין אובדן, אין כפילות מזהים.
- Compose UI: כל מצבי S5/S6/S7, RTL, הגדלת גופן 200%, TalkBack labels.
- בדיקת CI: merged manifest מכיל בדיוק את ההרשאות שב-5.1.

### 11.3 מטריצת מכשירים
יעד: לכסות שבבים (Qualcomm, Exynos, MediaTek, Unisoc), שכבות OEM וגרסאות 11–16. רשימה סופית תיבחר לפי צי המכשירים בפועל (שאלה 12.3 #2).

| קטגוריה | דוגמה | למה |
|---|---|---|
| ייחוס AOSP | Google Pixel (8/9 ומעלה) | התנהגות "נקייה" של API, Android האחרון |
| Samsung ביניים (Exynos) | Galaxy A3x / A5x | הנפוצים בישראל; One UI sleeping apps |
| Samsung דגל (Qualcomm) | Galaxy S24/S25 | NR SA/NSA, dual SIM / eSIM |
| Samsung מוקשח | Galaxy XCover (7 / 7 Pro) | מכשירי שטח ORG טיפוסיים, Knox |
| Xiaomi (MediaTek) | Redmi Note | HyperOS autostart, SIM שני |
| Xiaomi (Qualcomm) | Xiaomi / POCO | |
| Oppo / OnePlus / Realme | דגם עדכני | ColorOS |
| Unisoc זול | Galaxy A0x / Realme C | מקרי קצה ב-CellInfo |
| Huawei / Honor (אם קיים בצי) | | בלי GMS |
| Android 11 | מכשיר ישן כלשהו | גבול minSdk, `PhoneStateListener` |

לכל מכשיר: SIM של שלושת המפעילים הגדולים (Partner 425-01, Cellcom 425-02, Pelephone 425-03) בצירופים, לפחות מכשיר אחד עם dual SIM פעיל.

### 11.4 פרוטוקול drive-test בשטח
- **מסלולים:** עירוני צפוף (תל אביב / ירושלים), בין-עירוני מהיר (כביש 1 / 6), כפרי/פריפריה (נגב / גליל), מקרים מיוחדים (מנהרה / חניון תת-קרקעי — אובדן שירות וחזרה; מבנה סגור).
- **מערך:** 2 מכשירים מכל דגם במסלול, מסך כבוי, מעוגנים ברכב. מכשיר ייחוס: כלי drive-test מקצועי אם זמין בארגון, אחרת engineering mode של היצרן (למשל Samsung ServiceMode) או אפליקציית מדידה מוכרת על אותו דגם, עם צילום מסך/לוג בנקודות ציון.
- **משך:** ≥ 2 שעות למסלול, כולל עצירה של ≥ 30 דק' (מעבר ל-STATIONARY).
- **נבדק:** ספירת דגימות לפי טריגר מול הצפוי; פערים; זיהוי החלפות תא מול הייחוס; הפרש RSRP מול הייחוס; דיוק מיקום; התנהגות OOS → in_service; העלאה אחרי מנהרה; NR NSA/SA.
- **תוצר:** דוח לכל מסלול + עדכון `oem_quirks`.

### 11.5 בדיקת סוללה
- זוגות מכשירים זהים (דגם, גרסה, SIM, סוללה בבריאות דומה), אחד עם האפליקציה ואחד בלי, אותו מסלול באותו זמן. Wi-Fi כבוי, מסך כבוי, בלי אפליקציות נוספות.
- תרחישים: 4 שעות נסיעה; 8 שעות נייח (לילה, כולל Doze); משמרת PERSONAL מעורבת של 9 שעות.
- מדידה: `BatteryManager` כל 5 דק', `dumpsys batterystats` + Battery Historian, ובמכשיר אחד לפחות — מד זרם חיצוני אם זמין.
- בדיקת Doze כפויה (`adb shell dumpsys deviceidle force-idle`): האם callbacks המיקום ממשיכים ובאיזה קצב, האם העלאה עוברת.
- קבלה לפי יעדי 4.5.

### 11.6 קריטריוני קבלה לאיכות נתונים
| מדד | יעד |
|---|---|
| דגימות LTE/NR in_service עם `rsrp_dbm` תקין | ≥ 95% (במכשירי ייחוס) |
| דגימות in_service עם `cell_id` + `pci` | ≥ 95% (LTE); NSA — יימדד ויתועד |
| דגימות בנסיעה בחוץ עם מיקום ו-`loc_accuracy_m ≤ 30` | ≥ 90% |
| הפרש RSRP חציוני מול מכשיר הייחוס באותו דגם | ≤ 3 dB **[יעד ראשוני — לכייל אחרי M0]** |
| החלפות תא שזוהו מול הייחוס | ≥ 90% |
| אובדן דגימות (נוצרו מקומית מול `accepted+duplicates` בשרת, לא כולל פינוי מדווח) | 0 |
| `sample_id` כפולים שונים בתוכן | 0 |
| `ts` לא מונוטוני בתוך אותו SIM ו-boot (לא כולל CLOCK_JUMP) | 0 |
| השהיית העלאה p95 כשיש רשת | ≤ `upload_interval_s` + 30 ש' |
| פערים > 3× מרווח במצב active במכשיר עם כל ההרשאות + פטור | ≤ 1 ל-24 שעות |
| heartbeats שהוחמצו במצב active | ≤ 1% |

### 11.7 Definition of Done — ראו בכל אבן דרך בסעיף 12.

---

## 12. אבני דרך, סיכונים ושאלות פתוחות

### 12.1 אבני דרך (ללא הערכות זמן)

**M0 — ספייק ודה-ריסק**
היקף: אפליקציית מעבדה שמקליטה CellInfo / ServiceState / DisplayInfo / Location גולמיים לקובץ מקומי; הרצה על כל מטריצת המכשירים; מדידת סוללה ראשונית; בדיקת כל פריטי **[לאימות]** במסמך.
DoD: קובץ fixtures לכל מכשיר; טבלת `oem_quirks` ראשונה; תשובה כתובה לכל פריט [לאימות]; מספרי סוללה אמיתיים מול 4.5; אימות מקורות `nr_leg` (3.3) מול engineering mode; זמינות `altitude_msl_m`/`timing_advance_us` ב-API 34+; עותק ראשון של `field_tier_vectors.json` עובר; אישור ארכיטקט להמשך.

**M1 — מנוע מדידה מקומי**
היקף: `:probe:*`, `:core:model`, `:core:database`, ProbeService (FGS), כל הטריגרים, מכונת תנועה, dual SIM, שכנים, אימות, מסך סטטוס חי (S5) ומסך אבחון (S7) בגרסה מקומית, התראה N1. בלי שרת.
DoD: כל בדיקות היחידה של 11.1 עוברות; replay fixtures ירוקים; JSON תקף מול ה-schema של החוזה; drive-test עירוני אחד עם קריטריוני 11.6 (פרט לאלה שתלויים בשרת).

**M2 — חיבור לשרת**
היקף: רישום (S1, QR `coverageops-enroll:v1` + ידני, קודי שגיאה), טוקן ב-Keystore, batch upload עם gzip/backoff, טיפול ב-`rejected[]`/403/422 (6.3), heartbeat מורחב, פקודות `sample_now`/`upload_now`, config sync עם clamp, ביטול (S9), מונים באבחון. מול שרת staging של צוות הדאשבורד.
DoD: בדיקת אינטגרציה מקצה לקצה מול staging: 10,000 דגימות → 0 אובדן, 0 כפילויות לוגיות; כל ערך `rejected[].reason` מהרשימה הסגורה נספר ולא נשלח שוב; ביטול טוקן → מחיקה תוך ≤ 2 דק'; שינוי config בדאשבורד → מוחל תוך ≤ 5 דק' במצב פעיל; `sample_now`/`upload_now` → מבוצעות ו-acked תוך ≤ 5 דק' במצב פעיל; 72 שעות offline → ריקון תור מלא אחרי חזרה.

**M3 — ORG מוקשח ופיילוט**
היקף: מסך הרשאות (S4) + מדריכי OEM (S4b), BootReceiver, watchdog, זיהוי פערים, degraded, יידוע ORG (S2), היסטוריה בסיסית (S6). פיילוט ORG על 5–10 מכשירים.
DoD: פיילוט של 7 ימים: קריטריוני 11.6 מתקיימים; יעדי סוללה של 4.5 מתקיימים במכשירי הייחוס; אתחול → ניטור חוזר בלי מגע אדם (במכשירים עם כל ההרשאות); כל פער מוסבר ב-`ApplicationExitInfo` או מתועד כ-quirk.

**M4 — PERSONAL**
היקף: הסכמה (S3) עם הנוסח המאושר ו-`consent_version`/`consented_at` ב-enroll, השהיה, schedule, התראת תחילת משמרת (S10), הפעלה ידנית עם תקרה, coarse/off ו-`effective_location_mode`, ביטול הסכמה דרך `withdraw` (8.6), `skipped_paused` לפקודות, שמירה מקומית של 24 שעות.
DoD: **אישור משפטי כתוב** לנוסח ולסעיפי 8.5; withdraw מקצה לקצה (כולל offline → שליחה בחזרת רשת) מוחק מקומית מיד והשרת מאשר מחיקה/אנונימיזציה; פקודות במצב מושהה מוחזרות `skipped_paused` ולא מבוצעות; בדיקות schedule כולל שעון קיץ; השהיה → עצירת מיקום תוך שנייה (מאומת ב-`dumpsys location`); אף דגימה עם מיקום מחוץ לחלון משמרת/הפעלה ידנית (בדיקה אוטומטית על נתוני הפיילוט).

**M5 — בדיקות פעילות והיסטוריה**
היקף: `active_test` (latency/jitter/loss — 4.6), שיפורי S6, ייצוא אבחון.
DoD: בדיקה פעילה לא חורגת מ-50 KB תעבורה; תוצאות עקביות (±20%) בין שני מכשירים זהים באותו מקום.

**מאוחר:** throughput (נדחה בחוזה v2), פקודות `active_test_now`/`send_diagnostics`, FCM לפקודות חיות, Managed configuration, מפת רקע פנימית, SQLCipher, Play.

### 12.2 סיכונים

| # | סיכון | השפעה | מיטיגציה |
|---|---|---|---|
| R1 | OEM הורג את השירות למרות הכל | פערים, OFFLINE בדאשבורד | מדריכים, MDM, זיהוי פערים ודיווח; לבחור דגמים "ידידותיים" לצי ORG |
| R2 | נתוני NSA לא עקביים בין יצרנים | `network_type`/רמה לא אחידים | M0 מכריע; מקורות `nr_leg` עם היוריסטיקות שניתנות לכיבוי לפי דגם (3.3) |
| R3 | הגבלת קצב של `requestCellInfoUpdate` | ערכים כפולים/ישנים | `cell_info_age_ms` בכל דגימה; השרת מסנן (> 10 ש') |
| R4 | צריכת סוללה מעל היעד | התנגדות משתמשים (PERSONAL) | העלאה פחות תכופה, BALANCED, המלצת config |
| R5 | ביקורת משפטית מגבילה PERSONAL | שינוי היקף M4 | M4 מגיע אחרי ORG; מסמך 8.5 נשלח מוקדם |
| R6 | heartbeat ב-paused נדחה ב-Doze מעבר ל-15 דק' | "ללא קשר" שגוי אחרי 6 שעות | חוזה v2: OFFLINE רק ל-active/degraded; הסיכון השיורי מתועד ב-13.2 |
| R11 | פקודה מרחוק במצב מושהה מתעכבת או לא מבוצעת | ציפייה שגויה של המפעיל בדאשבורד | ack עם סטטוס (12.3 #10); תיעוד ב-4.3 |
| R7 | QR זדוני / שרת מזויף | דליפת נתונים | allowlist של hosts |
| R8 | שינויי Android עתידיים ב-FGS/מיקום | שבירה | target עדכני, M0 על Android האחרון, מעקב אחרי release notes |
| R9 | חבילת גלישה פרטית (PERSONAL) | עלות למשתמש | 6.6, `upload_wifi_only` |
| R10 | הפצת APK/MDM — עדכונים איטיים | באגים חיים זמן רב בשטח | `app_version` ב-heartbeat; config יכול לכבות פיצ'רים; תוכנית עדכון ב-MDM |

### 12.3 שאלות פתוחות לבעל המוצר / לאדריכל
1. האם יש מכשירי Android 10 או ישנים יותר בצי? (קובע minSdk 30.)
2. מהם דגמי המכשירים בפועל (ORG) והאם יש Huawei/Honor בלי GMS?
3. איזה MDM בשימוש, והאם המכשירים במצב Device Owner?
4. מה זמן ההשהיה המקובל ל"בקרה חיה"? `upload_interval_s=60` עולה בסוללה (4.5). להציע 300 ש' כברירת מחדל ל-PERSONAL?
5. ORG: ברירת המחדל של `allow_user_pause` (חוזה v2: true, פתוח).
6. PERSONAL: מה התקרה להפעלה ידנית (הצעה: 12 שעות)?
7. `device_uuid` חדש בכל רישום (ברירת המחדל בחוזה v2, פתוח).
8. PERSONAL dual SIM: לנטר את כל ה-SIM-ים (חוזה) או לאפשר בחירה?
9. `cell_id` ב-coarse/off — תלוי בבדיקה משפטית (8.4). ובנוסף: האם `timing_advance` צריך להתאפס גם ב-off (כרגע נשמר)?
10. **פורמט `acked_commands`:** `[id]` לא יכול לשאת `skipped_paused`. הצעה: `[{id, status: "done"|"skipped_paused"|"unsupported"}]` (4.3).
11. **`sample_now` → איזה `trigger`?** כרגע `manual`. האם הדאשבורד צריך להבדיל בין "ידני במכשיר" ל"מרחוק" (ערך `remote` חדש)?
12. **ORG מושהה:** האם `sample_now` צריך להתבצע? כרגע `skipped_paused` (4.3).
13. **ownership ב-QR:** הוספת רמז `&ownership=` ל-QR (לא מחייב — השרת עדיין מכריע) תחסוך שלב בחירה וסבב 409. לשיקול האדריכל.
14. הצפנת DB מקומית (SQLCipher): נדרש?
15. האם מכשירי ORG משותפים בין עובדים (משמרות)? אם כן — האם צריך "מי מחזיק במכשיר", ואיך זה משתלב בפרטיות?

---

## 13. סטטוס בקשות שינוי (מול חוזה v2)

### 13.1 טבלת סטטוס

| CR | נושא | סטטוס | הערה |
|---|---|---|---|
| A-CR1 | NR_NSA: עוגן LTE + `nr_leg` | התקבל | מקורות `nr_leg` ב-3.3 |
| A-CR2 | `rat` בשכנים | התקבל | |
| A-CR3 | `loc_accuracy_m` nullable | התקבל | |
| A-CR4 | יחוס גובה | התקבל בשינוי | `altitude_msl_m` נוסף; המרה בשרת — מאוחר. באפליקציה: API 34+ בלבד |
| A-CR5 | מדדי טריות | התקבל בשינוי | השרת מסנן; האפליקציה **לא** מאפסת שדות אות (שונה מההצעה שלי) |
| A-CR6 | heartbeat כשמושהה | התקבל | best-effort ≥ 15 דק'; OFFLINE רק ל-active/degraded |
| A-CR7 | מיפוי 2G/3G | התקבל | |
| A-CR8 | יחידות TA | התקבל | `timing_advance_us` ל-NR |
| A-CR9 | תשובת heartbeat + פקודות | התקבל בשינוי | MVP: `sample_now`, `upload_now`; PERSONAL מושהה → `skipped_paused`. פורמט ack פתוח (12.3 #10) |
| A-CR10 | שדות heartbeat | התקבל | + `effective_location_mode` |
| A-CR11 | `allow_user_pause` | התקבל | ברירת מחדל ORG פתוחה |
| A-CR12 | HTTP | התקבל | 422 = מעטפה בלבד → bisect בוטל (6.3); 403 `device_mismatch` |
| A-CR13 | throughput | נדחה לשלב מאוחר | |
| A-CR14 | רישום / QR | התקבל בשינוי | QR בלי `ownership`; ownership נקבע בקוד, אי-התאמה → 409 |
| A-CR15 | פורמט `schedule` | התקבל | |
| A-CR16 | withdraw | התקבל | MVP, חובה ל-PERSONAL (8.6) |
| A-CR17 | הגדרת coarse | התקבל בשינוי | `cell_id` נשמר (פריט משפטי) |
| A-CR18 | גבולות רמות | התקבל בשינוי | `power_off` → "לא מסווג" (לא שולי, שונה מההצעה שלי) |
| A-CR19 | הרשאת מיקום מדויק בנוסח | התקבל | |

### 13.2 נקודות בחוזה v2 שלא ניתנות למימוש מלא באנדרואיד
1. **heartbeat כל 15 דק' במצב מושהה** — אפשרי רק כ-best-effort (החוזה אכן אומר "כשהמערכת מאפשרת"). WorkManager נדחה ב-Doze ובדליי Standby, לפעמים לשעות, ומכשירים עם OEM killer עלולים לא לשלוח כלל. צפוי שהסף "ללא קשר אחרי 6 שעות" ייפול גם על מכשירים תקינים מושהים, למשל בלילה.
2. **"השהיה צפויה לביצוע פקודה: עד 5 דק'"** — מתקיים רק ב-`active|degraded`. במצב מושהה פקודה (למשל `upload_now` ב-ORG) מגיעה רק ב-heartbeat הבא, כלומר ≥ 15 דק' ובלי חסם עליון.
3. **`acked_commands: [id]` מול "ack עם `skipped_paused`"** — סתירה פנימית בחוזה, לא מגבלת אנדרואיד. נדרשת הכרעה (12.3 #10).
4. **`altitude_msl_m` ו-`timing_advance_us`** — קיימים רק מ-API 34. ב-API 30–33 תמיד null. זמינות בפועל גם ב-34+ לא ידועה **[לאימות]**.

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
