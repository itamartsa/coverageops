# אפיון — דאשבורד קליטה בשטח (`/field`) ב-CoverageOps

> סבב: אפיון + עיצוב בלבד. אין במסמך קוד מוצר; קטעי קוד הם המחשה של חתימות/מבנים בלבד.
> מסמך מחייב: `/home/claude/work/SHARED_CONTRACT.md` (להלן "החוזה"), **כולל "גרסה 2 — הכרעות האדריכל"** שגוברת על כל מה שמעליה. הפניות בסגנון "(v2)" מצביעות על הכרעה בגרסה 2.
> מוקאפ: `/home/claude/work/dashboard/mockup.html`.
> **רוויזיה 2** (לאחר סקירת האדריכל): התאמה לחוזה v2 — שדות Sample חדשים וסינון מיושנות, heartbeat + תור פקודות, מודל סטטוס, withdraw, קודי שגיאה ב-enroll, רשימת דחיות סגורה, הפרדת LTE/NR ב-KPI, קובץ וקטורי הרמות, טיפול בדגימות coarse, החלטות תלויות, Go/No-Go לאירוח, וגזירת קבוע ההמרה RSSI→RSRP.

## 0. מוסכמות שנלמדו מהקוד הקיים (וההפניות אליהן)

כל טענת מוסכמה במסמך מפנה לקובץ. הקבצים נקראו מ-`/mnt/user-data/uploads/CoverageOps/coverageops/`.

| נושא | מה מצאתי | קובץ |
|---|---|---|
| רישום נתבים | `app.include_router(x.router, prefix="/api/...", tags=[...])`; מודולי מודלים מיובאים ב-`main.py` כדי ש-`create_all` יכיר אותם (`from app.models import cell_site  # noqa: F401`) | `backend/app/main.py` |
| אתחול DB | `init_db()` ב-lifespan: `create_all` → `run_startup_migrations` → seed; כשל seed לא חוסם עלייה (`print("⚠️ ...")`) | `backend/app/main.py` |
| מיגרציות | רשימת `(Model, column)` ב-`_ADDITIVE_COLUMNS` + `_INDEXES` (`CREATE INDEX IF NOT EXISTS`), כל פעולה בטרנזקציה נפרדת, כשל נרשם ולא נזרק; טבלאות חדשות נוצרות שלמות ע"י `create_all` ("link_results ... new table created whole by create_all") | `backend/app/core/migrations.py` |
| הרשאות | `require_role("ADMIN", "OPERATOR")` מחזיר dependency; `get_current_user` מפענח JWT מ-`OAuth2PasswordBearer(tokenUrl="/api/auth/login")`; הודעות שגיאה בעברית (`"אין הרשאה לפעולה זו"`) | `backend/app/core/security.py` |
| תאריכים | `datetime.utcnow()` נאיבי ב-DB, ובפלט ISO עם `"Z"` (`_iso()`) | `backend/app/api/cell_sites.py`, `models/cell_site.py` |
| מחרוזות-סטטוס | עמודות סטטוס/מקור הן `String(16)` עם ערכים מתועדים בהערה (לא `Enum` של DB), למשל `status`, `height_source` | `backend/app/models/cell_site.py` |
| אינדקס חלקי חוצה-דיאלקטים | `Index(..., unique=True, postgresql_where=..., sqlite_where=...)` | `backend/app/models/user.py` (`uq_sites_owner_cell_site`) |
| רשימה דחוסה למפה | `{"fields": [...], "rows": [[...], ...]}` והלקוח ממפה לאובייקטים | `api/cell_sites.py` (`LIST_FIELDS`), `frontend/src/services/api.ts` (`cellSitesApi.list`) |
| נתיבים סטטיים לפני `/{id}` | "נתיבים סטטיים (`/meta`, `/type-defaults`) מוגדרים לפני `/{site_id}`" | `docs/TECHNICAL.md` |
| הורדות | `Content-Disposition` עם שם ASCII + `filename*` RFC 5987; CORS חושף `Content-Disposition`; בלקוח `downloadAuthed()` עם `responseType: "blob"` | `docs/TECHNICAL.md`, `main.py`, `services/api.ts` |
| יומן פעילות | `ActivityLog(user_id, action, detail, ip_address)` נכתב דרך `_log(db, ...)`; "לא נחשף ב-API נוכחי" | `models/user.py`, `api/auth.py`, `docs/TECHNICAL.md` |
| בדיקות API | SQLite בזיכרון (`StaticPool`), fixture `factory`, `_client(factory, role)` שדורס `get_db` ו-`get_current_user`, fixtures `admin`/`viewer`, `app.dependency_overrides.clear()`; בדיקת מיגרציה יוצרת טבלה "ישנה" ומריצה `run_startup_migrations` פעמיים | `backend/tests/test_cell_sites.py` |
| ניתוב בצד לקוח | `BrowserRouter` + `PrivateRoute`/`AdminRoute` ב-`main.tsx`; `QueryClient` יחיד | `frontend/src/main.tsx` |
| כניסה מהסרגל העליון | כפתור `adminBtn` עם `navigate("/admin")` מותנה בתפקיד | `frontend/src/components/TopBar.tsx`, `TopBar.module.css` |
| בדיקת תפקיד ב-UI | `canWrite(role)` = ADMIN/OPERATOR; `READ_ONLY_NOTE` | `frontend/src/lib/roles.ts` |
| לקוח API | axios יחיד, interceptor של JWT, logout אוטומטי ב-401, `apiErrorText()`; "Axios client + all API calls + TS types" בקובץ אחד, מקובץ לפי `xxxApi = {...}` | `services/api.ts`, `docs/TECHNICAL.md` |
| state | zustand עם `persist`; whitelist ב-`partialize` + `merge` מפורש; שכבות מערכת ב-`SYSTEM_LAYERS`, `LayerType` union | `store/useStore.ts` |
| שכבה + פאנל מידע | שורת שכבה עם 👁, כפתור ⓘ שפותח רכיב מידע/סינון (`CellSitesInfo`) | `components/LayersPanel.tsx`, `CellSitesInfo.tsx` |
| צבעי מפעילים | `CELL_OPERATORS` (PHI כתום, פלאפון כחול, סלקום סגול) | `frontend/src/lib/cellSites.ts` |
| טוקנים עיצוביים | `--bg … --cov-marginal`, פונטים Rajdhani + IBM Plex Sans Hebrew, `direction: rtl` | `frontend/src/index.css` |
| שליפה בדפים | `MapPage` משתמש ב-`useQuery`; `AdminPage` משתמש ב-`useState/useEffect` ישירות | `pages/MapPage.tsx`, `pages/AdminPage.tsx` |

**חריגה מודעת:** `AdminPage.module.css` משתמש בפלטה ופונט משלו (`#0d1117`, `'Segoe UI'`) ובכותרת עצמאית במקום `TopBar`. הדף `/field` יעקוב אחרי `MapPage` (שימוש ב-`TopBar` ובטוקנים של `index.css`) ולא אחרי AdminPage, כי זה הדף המבצעי ודרישת החוזה היא "אותם צבעים של CoverageOps".

**קבצים שעדיין לא זמינים — רוויזיה 2.** המתאם ציין שהקבצים החסרים נגישים כעת, אך בבדיקה (`find` על `/mnt/user-data/uploads/CoverageOps/coverageops/` ועל כל מערכת הקבצים) **נמצאו אותם 28 קבצים בלבד**, בלי אף קובץ חדש. לכן לא ניחשתי. מה שהשתנה: רשימות התלויות של `package.json` ושל ה-backend נמסרו בהודעת המתאם (לא כקבצים) ומשמשות בהחלטות 8.5.
פריטי **[לא אומת]** שנשארו, בדיוק:
1. `components/MapView.tsx` — איך שכבות מרונדרות לפי `mapLayers` (8.1).
2. `services/coverage_engine.py` — פונקציית הפענוח של `rssi_raster` (5.5).
3. `services/itm.py` — האם קיימת המרה הפוכה WGS84→ITM (לא נדרשת ב-MVP).
4. `docker-compose.yml` / `docker-compose.prod.yml` — גרסת PostgreSQL ‏(≥ 11 נדרשת ל-partitioning), האם האימג' כולל PostGIS, ואיפה יכול לרוץ cron (2.5, 9.5, 11.4).
5. סוויטת Playwright — מבנה fixtures והתחברות (10.4).
6. כלי בדיקות dev: `pytest` ו-`httpx` (נדרש ל-`TestClient`) **אינם** ברשימת התלויות שנמסרה, אף שהבדיקות הקיימות משתמשות בהם — כנראה קובץ dev נפרד שלא נמסר; בפרונטאנד אין test runner ברשימה (Vitest לא קיים) (10.4).
7. `Sidebar.module.css`, `LayersPanel.module.css`, `MapPage.module.css`, `Notification.tsx`, `services/coverageFormat.ts`, `api/sites.py`, `api/coverage.py`, `scripts/seed.py` — לא נדרשים להחלטות במסמך; אין הסתמכות על תוכנם.
8. תנאי השכבות החינמיות אצל ספקי אירוח (11.4) — מידע חיצוני, לא נבדק.
ממצא מהתלויות: `alembic` מותקן אך לא בשימוש (`migrations.py`: "without Alembic") — המודול ממשיך בגישת `create_all` + מיגרציות אתחול כפי שהחוזה קובע.

**ממצא:** `main.py` מתאר "FastAPI + PostgreSQL + PostGIS", אך אף מודל שהועלה לא משתמש בטיפוס גאומטרי (lat/lon הם `Float`). לכן העיצוב **לא תלוי ב-PostGIS** ב-MVP; PostGIS הוא אופציה לשלב מאוחר.

---

## 1. מטרות, היקף ומחוץ להיקף

### 1.1 מטרות (מהחוזה)
1. **בקרה חיה** — מצב כל מכשיר (מקוון/מושהה/מוגבל/לא מקוון), מיקום ורמת קליטה עדכניים, התראות, ושינוי תצורה מרחוק.
2. **ניתוח היסטורי** — מפת חום, KPI מוגדרים, השוואת מפעילים, אזורים מתים, ייצוא.
3. **חיזוי** — הערכת קליטה צפויה בתא/מסלול, עם רמת ביטחון גלויה, ובריאות מכשיר (סוללה/סיכון ניתוק).
4. **כיול** — השוואת "נמדד מול חזוי" מול `coverage_results` והערכת היסט/שיפוע של מודל ההפצה.

### 1.2 MVP (אבני דרך M1–M3, סעיף 11)
- ממשקי הקליטה מהחוזה (enroll / batch / heartbeat / config / **withdraw** — v2) כולל אידמפוטנטיות, אימות טוקן מכשיר, הגבלת קצב, רשימת דחיות סגורה וסינון מיושנות (v2).
- **תור פקודות MVP** (v2): `sample_now`, `upload_now` — מסירה בתשובת heartbeat, ack מהמכשיר, רישום ביומן מי הנפיק.
- ניהול מכשירים, קבוצות, תצורה (כולל `allow_user_pause`, `schedule` לפי v2), קודי צירוף + QR (בכפוף לאישור תלות — 8.5).
- דף `/field`: סקירה (KPI), מפה חיה, טבלת מכשירים, פרטי מכשיר + ציר זמן + מסלול (עם רישום ביומן) + שליחת פקודה, התראות בסיסיות (לא מקוון, ללא קשר, סוללה, ניטור מוגבל, השהיה ב-ORG, פינוי תור).
- אגרגציה לגריד × שעה, מפת חום (כולל כשכבה במפה הראשית), ניתוח בסיסי (התפלגות רמות, P5/P50 בנפרד ל-LTE ול-NR, השוואת מפעילים, דקות ללא שירות), אזורים מתים, ייצוא CSV/GeoJSON, משימת שמירה (retention).

### 1.3 שלבים מאוחרים
- M4: נמדד-מול-חזוי + התאמת תא-לאתר + דוח כיול.
- M5: חיזוי שלב P0/P1 + תיקוף + בריאות מכשיר.
- M6 (מותנה בנתונים): מודל ML, SSE/WebSocket, H3, מחיצות שבועיות אוטומטיות מתקדמות.

### 1.4 מחוץ להיקף
- אפליקציית האנדרואיד עצמה (צוות אחר; האפיון שלהם ב-`/home/claude/work/android/SPEC.md` נקרא לצורך תיאום בלבד).
- שינוי מודל ההפצה הקיים (`coverage_engine.py`) — הכיול מפיק **דוח והמלצה**, לא שינוי אוטומטי של פרמטרים.
- איסוף IMEI/IMSI/מספר טלפון (אסור בחוזה), הקלטת תעבורה/תוכן, מעקב אחר משתמשי VIEWER.
- פקודות `active_test_now`, `send_diagnostics`, FCM/push, endpoints ל-throughput, החלפת טוקן, certificate pinning — נדחו לשלב מאוחר (v2).
- התראות SMS/מייל/Push למפעילים (אפשרי בהמשך; ב-MVP — בתוך האפליקציה בלבד).
- המרת `altitude_m` (אליפסואיד) לגובה מעל פני הים בצד שרת — שלב מאוחר (v2); ב-MVP משתמשים ב-`altitude_msl_m` כשנשלח.

## 2. מודל נתונים

### 2.1 עקרונות
- **קידומת `field_`** לכל הטבלאות — כדי לא להתנגש עם `device_profiles` הקיימת (`models/device_profile.py`, פרופילי מקלט לניתוח) ועם המונח "device" שם.
- קובץ מודלים חדש `backend/app/models/field.py`, מיובא ב-`main.py` כמו `cell_site` (`# noqa: F401`).
- **טבלאות חדשות נוצרות שלמות ע"י `create_all`** (כמו `link_results`); אין עמודות ב-`_ADDITIVE_COLUMNS` בגרסה הראשונה. עמודות שיתווספו בהמשך — לפי מנגנון `_ADDITIVE_COLUMNS` הקיים.
- **סטטוסים כ-`String(n)`** עם ערכים מתועדים (כמו `cell_sites.status`), לא `Enum` של DB — מונע `ALTER TYPE` בעתיד.
- **זמנים:** `DateTime` נאיבי ב-UTC (`datetime.utcnow`) כמו בשאר המערכת; בפלט ISO עם `Z`.
- **UUID:** `sqlalchemy.Uuid` (SQLAlchemy 2; `UUID` ב-PG, `CHAR(32)` ב-SQLite).
- **JSON:** `JSON().with_variant(JSONB, "postgresql")`.
- **מזהה פנימי מול חיצוני:** מפתח ראשי פנימי `Integer` (קטן — חשוב לאינדקסים של מיליוני מדידות); מזהה חיצוני `public_id` (UUID שהשרת מנפיק) הוא ה-`device_id` שבחוזה. `device_uuid` הוא המזהה שהאפליקציה מייצרת (החוזה) — נשמר, ייחודי, אך לא משמש כטוקן.

### 2.2 טבלאות

#### `field_device_groups`
| עמודה | סוג | הערות |
|---|---|---|
| id | Integer PK | |
| name | String(64) unique | |
| description | String(256)? | |
| config | JSON | תצורה מלאה לפי החוזה (ללא `config_version`) |
| created_at / updated_at | DateTime | |
| updated_by | String(64)? | username (כמו `height_updated_by`) |

קבוצת ברירת מחדל "כללי" נזרעת באתחול (seed אידמפוטנטי כמו `seed_device_profiles`).

#### `field_devices`
| עמודה | סוג | הערות |
|---|---|---|
| id | Integer PK | פנימי |
| public_id | Uuid unique | = `device_id` בחוזה |
| device_uuid | Uuid unique | מהאפליקציה; לפי v2 **חדש בכל רישום מחדש** (ברירת מחדל) → רישום מחדש = רשומת מכשיר חדשה (ראו 3.3) |
| label | String(64)? | שם תצוגה ("ניידת 4") |
| ownership | String(8) | `ORG` \| `PERSONAL` — **נקבע מקוד הצירוף** (v2), לא מהאפליקציה |
| consent_version | String(32)? | חובה ב-PERSONAL (v2) |
| consented_at | DateTime? | חובה ב-PERSONAL (v2); נשמר כ-UTC |
| group_id | FK→field_device_groups (SET NULL) | NULL → קבוצת ברירת המחדל |
| config_override | JSON? | שדות דורסים בלבד |
| config_version | Integer NOT NULL default 1 | גרסת התצורה האפקטיבית של **המכשיר** (ראו 3.6) |
| model, manufacturer, os_version, app_version | String(64) | |
| token_hash | String(64) unique | SHA-256 hex של הטוקן (ראו 9.2) |
| token_issued_at | DateTime | |
| status | String(12) | `active` \| `revoked` \| `withdrawn` \| `deleted` (סטטוס ניהולי; סטטוס חי מחושב — 3.5) |
| enrolled_at, enrolled_by_code_id (FK) | | |
| revoked_at, withdrawn_at, deleted_at | DateTime? | |
| notes | String(256)? | |
| — **מצב אחרון (denormalized, מתעדכן בקליטה)** — | | |
| last_heartbeat_at, last_sample_at, last_sample_received_at | DateTime? | |
| last_sample_ts_reported | DateTime? | `last_sample_ts` מה-heartbeat (v2) — לזיהוי פער בין מה שנאסף למה שהועלה |
| last_lat, last_lon, last_loc_accuracy_m, last_loc_ts | Float/DateTime? | רק מדגימה עם מיקום **לא-coarse** ו-`loc_age_ms ≤ 30000`; לדגימת coarse נשמר בנפרד `last_coarse_cell` (gx25,gy25) |
| last_tier | SmallInteger? | 0–4 / NULL (5.1); `last_tier_stale` Boolean אם `cell_info_age_ms > 10000` |
| last_rsrp_dbm, last_network_type, last_operator_key, last_operator_name | | ב-NR_NSA: RSRP של עוגן ה-LTE |
| last_nr_ss_rsrp_dbm | Float? | מ-`nr_leg` (NSA) או מהשדה הראשי (SA) — מוצג בנפרד |
| last_battery_pct, last_is_charging, last_queue_size | | מה-heartbeat |
| last_permissions | JSON? | `location: precise\|approximate\|none` + בוליאנים (v2) |
| last_monitoring_state | String(16)? | `active` \| `paused_by_user` \| `paused_schedule` \| `degraded` |
| last_degraded_reasons | JSON? | רשימת קודים סגורה שמגדיר צוות האנדרואיד (v2); מוצגים עם תווית עברית מטבלת מיפוי ב-`lib/fieldStatus.ts` |
| last_effective_location_mode | String(8)? | `precise` \| `coarse` \| `off` (v2) |
| last_location_services_enabled | Boolean? | (v2) |
| last_applied_config_version | Integer? | (v2) — "תצורה ממתינה" כש-`< config_version` |
| last_evicted_samples_total | BigInteger? | (v2) — מונה מצטבר; עלייה → התראת אובדן נתונים |
| state_changed_at | DateTime? | מתי `last_monitoring_state` השתנה לאחרונה — לסטטוס "מושהה מאז" |

אינדקסים: `ix_field_devices_group_id`, `ix_field_devices_last_heartbeat_at`.

#### `field_enrollment_codes`
| עמודה | סוג | הערות |
|---|---|---|
| id | Integer PK | |
| code_hash | String(64) unique | SHA-256 של הקוד; הקוד בטקסט גלוי מוצג **פעם אחת** בעת יצירה |
| code_hint | String(4) | 4 תווים אחרונים להצגה ברשימה |
| label | String(64)? | "צוות צפון — נובמבר" |
| group_id | FK? | הקבוצה שאליה ישויכו המכשירים |
| ownership | String(8) NOT NULL | `ORG` \| `PERSONAL` — **חובה** (v2: הבעלות נקבעת בקוד; אין עוד `ANY`) |
| max_uses | Integer default 1 | 1 = חד-פעמי (החוזה); עד 200 |
| use_count | Integer default 0 | |
| expires_at | DateTime | ברירת מחדל: +24 ש'; מקסימום 30 יום |
| created_by | FK→users | |
| created_at, revoked_at | DateTime | |

#### `field_measurements` (הטבלה הגדולה)
עמודות = כל שדות Sample בחוזה **כפי שתוקנו ב-v2**, ועוד שדות מחושבים:

| עמודה | סוג | הערות |
|---|---|---|
| device_pk | Integer FK→field_devices | (ON DELETE CASCADE) |
| sample_id | Uuid | ייחודי לכל SIM (v2) |
| ts | DateTime | זמן המדידה; שני SIM באותו רגע חולקים `ts` (v2) |
| received_at | DateTime | זמן קליטה בשרת |
| lat, lon | Float? | ב-coarse: מעוגלים ל-0.01° (v2) |
| loc_accuracy_m | Float? | null כש-lat/lon null (v2) |
| altitude_m | Float? | **מעל אליפסואיד WGS84** (v2) |
| altitude_msl_m | Float? | חדש (v2) — מעל פני הים, כשהמכשיר מחשב |
| speed_mps, bearing_deg | Float? | null ב-coarse |
| loc_provider | String(8) | |
| loc_age_ms | Integer? | חדש (v2) |
| network_type | String(8) | `NR_SA\|NR_NSA\|LTE\|UMTS\|GSM\|NONE` |
| mcc, mnc | String(3) | |
| operator_key | String(7) | `"{mcc}-{mnc}"` מחושב, לקיבוץ |
| operator_name | String(32) | |
| sim_slot | SmallInteger | |
| is_roaming | Boolean | |
| cell_id | BigInteger? | ECI / NCI / CID — NCI עד 36 ביט; ב-NR_NSA — של עוגן ה-LTE (v2) |
| pci, tac, earfcn_arfcn, band | Integer? | ב-NSA — עוגן LTE; UMTS: tac=LAC, pci=PSC; GSM: tac=LAC, pci=BSIC (v2) |
| rsrp_dbm, rsrq_db, sinr_db | Float? | LTE/NSA: של LTE; NR_SA: SS-RSRP/SS-RSRQ/SS-SINR; UMTS/GSM: null (v2) |
| rssi_dbm | Float? | ב-UMTS/GSM — המדד היחיד (v2) |
| nr_pci, nr_arfcn, nr_band, nr_ss_rsrp_dbm, nr_ss_rsrq_db, nr_ss_sinr_db | Integer?/Float? | פריסה של `nr_leg` (v2) לעמודות — כדי שיהיו זמינים ל-KPI ולאגרגציה בלי פענוח JSON |
| cqi | SmallInteger? | |
| timing_advance | SmallInteger? | **אינדקס LTE בלבד** (v2) |
| timing_advance_us | Integer? | חדש (v2) — NR, במיקרו-שניות |
| signal_level | SmallInteger? | |
| cell_info_age_ms | Integer? | חדש (v2) — בסיס לסינון מיושנות |
| data_state, service_state, trigger | String(16) | |
| neighbors | JSON? | עד 8; כל איבר עם `rat: LTE\|NR\|UMTS\|GSM` (v2, במקום `network_type`) |
| battery_pct | SmallInteger | |
| is_charging, screen_on | Boolean | |
| active_test | JSON? | |
| config_version | Integer | מתוך המעטפה |
| **tier** | SmallInteger? | מחושב בשרת (5.1); NULL = "לא מסווג" |
| **meas_tech** | String(6) | מפתח טכנולוגיית המדידה (5.1): `LTE` (LTE + עוגן NSA), `NR` (NR_SA), `LEGACY`, `NONE` |
| **gx, gy** | Integer? | תא 100 מ' (2.4); NULL כשאין מיקום, coarse, `loc_accuracy_m > 100`, או `loc_age_ms > 30000` |
| **gx25, gy25** | Integer? | תא 2.5 ק"מ; NULL כשאין מיקום, `loc_accuracy_m > 2000` או `loc_age_ms > 30000` |
| **dt_s** | SmallInteger? | משקל-זמן של הדגימה (5.2), מחושב באגרגציה |
| **flags** | SmallInteger default 0 | ביטים: 2=מחוץ לאזור ישראל, 4=ts עתידי קל (≤ 5 דק') תוקן, 8=anonymized, 16=`stale_cell` (`cell_info_age_ms > 10000`), 32=`coarse` (2.4), 64=`stale_loc` (`loc_age_ms > 30000`), 128=sentinel נורמל ל-null |

**שמירת הערכים כפי שהם (v2):** האפליקציה לא מאפסת שדות אות ישנים; השרת שומר את הגולמי כפי שהגיע ומסמן ב-`flags`. הסינון נעשה **בשלב השימוש** (KPI, אגרגציה, התראות, last_*), לא בשמירה.

**מפתח ראשי:** `(device_pk, ts, sample_id)`.
- משמש לאידמפוטנטיות: אותה דגימה שנשלחת שוב נושאת את אותם `sample_id` **ו-`ts`** (נוצרים במכשיר) ולכן מתנגשת.
- `ts` חלק מהמפתח כי ב-PostgreSQL טבלה מחולקת מחייבת שמפתח ייחודי יכלול את מפתח החלוקה. **מגבלה:** אם באג באפליקציה ישלח אותו `sample_id` עם `ts` אחר — תיווצר כפילות. מכוסה בבדיקת "duplicates" מקדימה (3.3) ובבדיקה ייעודית.
- משמש גם לשאילתת מסלול/ציר זמן של מכשיר (`WHERE device_pk=? AND ts BETWEEN`).

אינדקסים נוספים: `ix_fm_device_sample (device_pk, sample_id)` (בדיקת כפילויות), `ix_fm_ts (ts)` — ב-PG כ-**BRIN**, ב-SQLite B-tree רגיל. אין אינדקס מרחבי על הגולמי — שאילתות מרחביות רצות על האגרגציות.

#### `field_heartbeats`
`id BigInteger PK, device_pk FK, ts, received_at, battery_pct, is_charging, app_version, queue_size, permissions JSON, monitoring_state, degraded_reasons JSON, effective_location_mode, location_services_enabled, applied_config_version, evicted_samples_total, last_sample_ts, acked_commands JSON` (שדות v2).
אינדקס `(device_pk, ts)`. שמירה 30 יום (הגדרה `FIELD_HEARTBEAT_RETENTION_DAYS`) — נדרש לבריאות מכשיר וחקירת ניתוקים. **ה-heartbeat לא נושא מיקום** (במצב מושהה — חובה לפי v2; גם במצב פעיל אין שדה מיקום בחוזה). המצב האחרון משוכפל ל-`field_devices`.

#### `field_device_commands` — תור פקודות (חדש, v2)
| עמודה | סוג | הערות |
|---|---|---|
| id | Uuid PK | נשלח למכשיר כ-`id` |
| device_pk | FK→field_devices (CASCADE) | |
| type | String(24) | MVP: `sample_now` \| `upload_now`. מאוחר: `active_test_now`, `send_diagnostics` |
| status | String(16) | `queued` → `delivered` → `done` \| `skipped_paused` \| `unsupported` \| `failed`; או `expired` / `cancelled` |
| issued_by | FK→users | מי הנפיק — גם ב-`activity_log` (9.3) |
| issued_at | DateTime | נשלח למכשיר כ-`issued_at` |
| delivered_at | DateTime? | הפעם הראשונה שהפקודה הוחזרה בתשובת heartbeat |
| acked_at | DateTime? | |
| expires_at | DateTime | `issued_at + 20 דק'` (הנחה: 5 דק' השהיה צפויה (v2) × 4); פקודה שלא נמסרה עד אז → `expired` |
| note | String(128)? | |

אינדקס `(device_pk, status)`. אינדקס ייחודי חלקי `uq_cmd_open (device_pk, type) WHERE status IN ('queued','delivered')` — פקודה פתוחה אחת מכל סוג לכל מכשיר (לחיצה כפולה לא יוצרת שתיים). אותה טכניקה של `uq_sites_owner_cell_site` (`models/user.py`). שמירה: 90 יום.

#### `field_agg_hourly` — תא 100 מ' × שעה
| עמודה | סוג | הערות |
|---|---|---|
| gx, gy | Integer | |
| hour | DateTime | תחילת שעה UTC |
| operator_key | String(7) | |
| meas_tech | String(6) | `LTE` (LTE + עוגן NSA), `NR` (NR_SA), `LEGACY` (UMTS/GSM), `NONE`. **ערכי RSRP של דלי `LTE` הם תמיד LTE RSRP ושל `NR` תמיד SS-RSRP** — אין דלי שמערבב (v2) |
| n | Integer | מספר דגימות (אחרי סינון מיושנות) |
| n_rsrp | Integer | דגימות עם RSRP תקין |
| sum_rsrp, sumsq_rsrp, min_rsrp, max_rsrp | Float | |
| rsrp_hist | JSON | 60 תאי היסטוגרמה של 2 dB, ‎-160..-40 — מאפשר **אחוזונים ממוזגים** |
| n_nsa | Integer | מתוך דלי `LTE`: כמה מהדגימות היו NR_NSA |
| n_ss, ss_rsrp_hist | Integer, JSON | ב-`LTE`: SS-RSRP של רגל ה-NR (`nr_leg`) בדגימות NSA — היסטוגרמה **נפרדת**; ב-`NR` לא בשימוש |
| t0..t4, t_unk | Integer | ספירת דגימות לפי רמה (0=שולי … 4=מצוין; unk = "לא מסווג") |
| w0..w4, w_unk | Integer | אותו דבר בשקלול זמן (שניות) |
| oos_n, oos_s | Integer | דגימות/שניות ללא שירות |
| sum_sinr, n_sinr, sum_rsrq, n_rsrq | Float/Integer | |
| n_stale_excluded | Integer | כמה דגימות סוננו בגלל `cell_info_age_ms > 10000` (שקיפות) |
| n_devices | SmallInteger | מכשירים שונים בדלי |
| device_set | JSON | רשימת `device_pk` (פנימי, **לעולם לא נחשף ב-API**) — לאיחוד בזמן שאילתה עבור סף k-אנונימיות (9.4) |
| updated_at | DateTime | |

מפתח ייחודי: `(gx, gy, hour, operator_key, meas_tech)`; אינדקס `(hour)` ואינדקס `(gx, gy)`.
**כלל כניסה:** רק דגימות עם `gx/gy` (כלומר לא coarse, דיוק ≤ 100 מ', מיקום טרי) **וללא** `stale_cell`.

#### `field_agg_daily` — תא 100 מ' × יום (שמירה ללא הגבלה)
אותן עמודות עם `day` (Date, יום לפי **Asia/Jerusalem**) במקום `hour`. נגזרת מ-`field_agg_hourly`.

#### `field_agg_daily_coarse` — תא 2.5 ק"מ × יום (שמירה ללא הגבלה)
אותן עמודות עם `gx25, gy25, day`. נבנית **מהגולמי** (לא מקיבוץ של G100), כדי לכלול דגימות coarse שאין להן `gx/gy`. משמשת את `/heatmap?level=2500` ואת KPI ברמת אזור.

#### `field_agg_dirty` — תור עבודה לאגרגציה
`(gx, gy, hour, operator_key, tech)` PK + `marked_at`. כל batch מוסיף את הדליים שנגע בהם (`INSERT … ON CONFLICT DO NOTHING`). המשימה מחשבת מחדש **את כל הדלי מהגולמי** — אידמפוטנטי, ועובד גם לנתונים שמגיעים באיחור (תור offline במכשיר).

#### `field_alert_rules`
`id, type String(24), scope String(8) ('all'|'group'|'device'), scope_id?, params JSON, severity String(8) ('info'|'warning'|'critical'), enabled Boolean, updated_by, updated_at`. נזרעות ברירות מחדל (סעיף 7).

#### `field_alerts`
| עמודה | סוג | הערות |
|---|---|---|
| id | Integer PK | |
| rule_id | FK | |
| type | String(24) | |
| dedup_key | String(96) | למשל `offline:device:17` / `dead_zone:425-03:LTE:3301:3611` |
| device_pk | FK? | |
| gx, gy, operator_key | ? | להתראות מרחביות |
| status | String(8) | `open` \| `acked` \| `resolved` |
| severity | String(8) | |
| message_he | String(256) | |
| details | JSON | ערכים שהפעילו את ההתראה |
| opened_at, last_seen_at, resolved_at | DateTime | |
| occurrences | Integer | |
| acked_by, acked_at | | |

אינדקס ייחודי חלקי: `uq_field_alert_open (dedup_key) WHERE status != 'resolved'` — באותה טכניקה של `uq_sites_owner_cell_site` (`postgresql_where` + `sqlite_where`, `models/user.py`). מבטיח התראה פתוחה אחת לכל מפתח.

#### `field_cell_site_map` — התאמת תא סלולרי לאתר (M4)
`operator_key, node_id BigInteger (eNB = ECI>>8; gNB לפי אורך gNB-ID — ראו 5.6), cell_site_id FK→cell_sites (SET NULL), source String(8) ('auto'|'manual'), confidence Float, n_samples, est_lat, est_lon, updated_at, updated_by`. PK `(operator_key, node_id)`.
**שים לב:** `cell_sites` מוחלפת כולה ב-`import_data` (TECHNICAL.md), ולכן `cell_site_id` עלול להשתנות ברענון. נשמור גם `(operator, site_code, status)` כמפתח שחזור — אותה שיטה שבה הייבוא משמר גבהים ידניים (TECHNICAL.md, "גבהים בייבוא/רענון").

#### `field_operator_map` (טבלת הגדרה קטנה, ADMIN)
`operator_key PK ("425-01"), name_he, cell_sites_operator ('PHI'|'PELEPHONE'|'CELLCOM'|'OTHER'), note`. זריעה:
- 425-01 פרטנר → PHI; 425-07 הוט מובייל → PHI; 425-03 פלאפון → PELEPHONE; 425-02 סלקום → CELLCOM.
- 425-08 (גולן) ו-MVNOs — **[לא ודאי]**, נזרעים כ-OTHER עד אימות; ניתנים לעריכה.

#### יומן ביקורת
שימוש בטבלת `activity_log` הקיימת (`models/user.py`) — ללא טבלה חדשה. ראו 9.3.

### 2.3 נפח — הנחות והערכה (הנחות, לא מדידות)
| הנחה | ערך |
|---|---|
| גודל צי ב-MVP / יעד תכנון | 50 / 500 מכשירים |
| ORG (70% מהצי): ניטור 24/7; בתנועה 8 ש'/יום ב-30 ש', נייח 16 ש' ב-300 ש' | ≈ 960 + 192 = 1,152 דגימות/יום |
| PERSONAL (30%): משמרת 9 ש', מחציתה בתנועה | ≈ 540 + 54 = ~600 |
| מקדם dual-SIM (20% מהמכשירים עם 2 SIM פעילים) | ×1.2 |
| טריגרים (cell_change/service_change/movement) | +15% |
| **ממוצע משוקלל** | **≈ 1,350 דגימות/מכשיר/יום** |

| | 50 מכשירים | 500 מכשירים |
|---|---|---|
| דגימות ליום | ~68K | ~675K |
| שורות גולמיות ב-90 יום | ~6M | ~61M |
| נפח (≈ 450 בתים לשורה כולל JSON שכנים, + ~35% אינדקסים) | ~3.5 GB | ~37 GB |
| heartbeats ליום (288/מכשיר) / 30 יום | 14K / 430K | 144K / 4.3M |
| בקשות batch ממוצעות (upload כל 60 ש') | ~0.8/ש' | ~8/ש' (שיא ×3) |

`field_agg_hourly`: מכשיר נע ב-50 קמ"ש עם דגימה כל 30 ש' עובר ~400 מ' בין דגימות → **כמעט אין דחיסה** לתנועה (שורה לכל דגימה), ודחיסה גבוהה למכשירים נייחים. הערכה גסה: ~35% ממספר הדגימות ⇒ ~240K שורות/יום ב-500 מכשירים ⇒ ~87M בשנה. לכן:
- `field_agg_hourly` — שמירה 400 יום (מספיק לדפוסי שעה-בשבוע ולהשוואה שנתית). (אושר ב-v2.)
- `field_agg_daily` — ללא הגבלה (חזרה על אותם צירים מקטינה מאוד; הערכה ≤ 30K שורות/יום).

**השלכה על אירוח:** ראו פריט Go/No-Go ‏11.4. שדות v2 (`nr_*`, גילאים, `timing_advance_us`, `altitude_msl_m`) מוסיפים ~40 בתים לשורה — בתוך מרווח ההערכה.

### 2.4 גריד מרחבי
- **MVP: גריד שווה-זווית "G100"**: `gy = floor(lat / 0.0009)`, `gx = floor(lon / 0.00106)` — בקו רוחב 31.5° ≈ 100 × 100 מ'.
  - רמות גסות: `floor(gx/5)` (≈500 מ') בזמן שאילתה; רמת 2.5 ק"מ מטבלה נפרדת (`gx25 = floor(lon / 0.0265)`, `gy25 = floor(lat / 0.0225)`).
  - נבחר על פני H3 כי: אין תלות חדשה; ניתן לחישוב ב-SQL בשני הדיאלקטים; מלבנים מתרנדרים פשוט ב-Leaflet. H3 — אופציה ל-M6.
- **דגימות coarse (v2: `effective_location_mode=coarse` → lat/lon מעוגלים ל-0.01°, `loc_accuracy_m ≥ 1000`, altitude/speed/bearing/TA = null):**
  - **זיהוי בצד שרת לכל דגימה** — ה-Sample עצמו לא נושא `effective_location_mode` (הוא מדווח רק ב-heartbeat), ולכן אי אפשר לסמוך על המצב האחרון של המכשיר (ייתכן שהשתנה באמצע תור offline). כלל: `coarse` ⇔ `loc_accuracy_m ≥ 1000` (ההגדרה של v2). בנוסף, אם `lat*100` ו-`lon*100` שלמים (סטייה ≤ 1e-6) והדיוק < 1000 — הדגימה מסומנת `coarse` גם כן (הגנה מפני באג באפליקציה) ונרשם מונה חריגה.
  - **לא נכנסות לגריד 100 מ'** (`gx/gy = NULL`) ולא לאגרגציות השעתיות/יומיות של 100 מ'. סריג של 0.01° (≈ 1.1 × 0.95 ק"מ) "נוחת" תמיד על אותם תאי 100 מ', כך שהכללתו הייתה יוצרת "נקודות חמות" מלאכותיות במרכזי הסריג ומזהמת אחוזונים של תאים אמיתיים.
  - **נכנסות לגריד 2.5 ק"מ בלבד** (`field_agg_daily_coarse`). שגיאת העיגול (≤ ~0.75 ק"מ) קטנה ביחס לתא — מקובל, ומוצהר בממשק ("כולל דגימות במיקום משוער").
  - **תצוגה:** לא משורטטות כנקודה מדויקת. במפה החיה מכשיר במצב coarse מוצג כעיגול חצי-שקוף ברדיוס ~1 ק"מ עם תווית "מיקום משוער", ללא קו מסלול; במסלול — כשרשרת תאי 0.01° ולא כקו רציף; בייצוא GeoJSON — `Polygon` של תא העיגול ולא `Point`, עם `properties.location_precision="coarse"`.
  - לא משמשות להתאמת תא-לאתר (5.6) ולא לאמידת מיקום צומת — רזולוציה לא מספקת, ומניעת שחזור מיקום מדויק משילוב `cell_id` + TA (TA ממילא null ב-coarse).
- **מצב `off`:** lat/lon null → אין גריד כלל; הדגימה משמשת ל-KPI לא-מרחביים בלבד (% זמן לפי רמה, OOS, שינויי תא).
- **מיקום ישן:** `loc_age_ms > 30000` → `gx/gy/gx25/gy25 = NULL` (המיקום לא שייך לרגע המדידה). זו החלטת הדאשבורד (v2 מגדיר סף רק ל-`cell_info_age_ms`); הסף ניתן להגדרה `FIELD_MAX_LOC_AGE_MS`.

### 2.5 חלוקה (partitioning) ושמירה
- **PostgreSQL:** `field_measurements` נוצרת כ-partitioned table: `__table_args__ = {"postgresql_partition_by": "RANGE (ts)"}` (פרמטר דיאלקט של SQLAlchemy; ב-SQLite מתעלמים ממנו). `create_all` יוצר את טבלת האב בלבד.
  - `run_startup_migrations` + משימת התחזוקה יוצרות מחיצות שבועיות: `CREATE TABLE IF NOT EXISTS field_measurements_w2026_46 PARTITION OF field_measurements FOR VALUES FROM (...) TO (...)` — לשבוע הנוכחי + 4 קדימה, ומחיצת `DEFAULT` כרשת ביטחון (שורה שנפלה ל-DEFAULT = התראת תחזוקה).
  - שמירה: `DROP TABLE` למחיצה שגבולה העליון < now − 90 יום (פעולה מיידית, ללא bloat), ובנוסף `DELETE` ברצועות של 10K בשבוע הגבולי כדי לכבד 90 יום בדיוק.
  - **[לא אומת]** שגרסת ה-PG בפרודקשן ≥ 11 (נדרש ל-partitioning עם PK ו-DEFAULT). docker-compose לא זמין (סעיף 0, פריט 4).
- **SQLite (בדיקות/פיתוח):** טבלה רגילה; שמירה = `DELETE WHERE ts < cutoff` ברצועות.
- **אגרגציות:** לא מחולקות ב-MVP. הערכה: ~90M שורות ב-500 מכשירים בשנה — לשקול חלוקה חודשית ב-M6.
- ההחלטה למחוק/לחלק מבוצעת במשימת תחזוקה (סעיף 9.5), לא ב-request.

### 2.6 התאמה ל-`create_all` + מיגרציות
- כל הטבלאות החדשות → `create_all` (חדשות, שלמות).
- `_INDEXES` מקבל: אינדקס BRIN (PG בלבד — ייכשל בשקט ב-SQLite לפי ההתנהגות הקיימת של `_run`, אבל עדיף להוסיף רשימה `_PG_ONLY_INDEXES` שרצה רק כש-`is_pg`), ופקודות יצירת המחיצות.
- בדיקה חדשה במבנה של `test_migration_splits_rf_source_once` (`tests/test_cell_sites.py`): הרצה כפולה של `run_startup_migrations` על SQLite לא נכשלת ולא משכפלת.

---

## 3. Backend API

### 3.1 מבנה
- `app/api/field_ingest.py` — ממשקי המכשיר (טוקן מכשיר בלבד).
- `app/api/field.py` — ממשקי הדאשבורד (JWT משתמש).
- `app/core/device_auth.py` — `get_current_device`.
- `app/services/field_quality.py` (סיווג רמות, `meas_tech`, נורמליזציית sentinel, דגלי stale/coarse, משקלי זמן), `field_status.py` (סטטוס חי 3.5 + תוויות `degraded_reasons`), `field_commands.py` (תור פקודות), `field_privacy.py` (`purge_device`, 9.6), `field_agg.py`, `field_alerts.py`, `field_predict.py`, `field_maintenance.py`.
- `app/scripts/field_maintenance.py` (CLI — כמו `python -m app.scripts.import_cell_sites`, TECHNICAL.md), `app/scripts/field_simulator.py`.
- רישום ב-`main.py` — **ingest לפני field** כדי ש-`/devices/me/config` ו-`/devices/enroll` יקדמו ל-`/devices/{device_id}`:
  ```
  app.include_router(field_ingest.router, prefix="/api/field", tags=["field-ingest"])
  app.include_router(field.router,        prefix="/api/field", tags=["field"])
  ```
- הגדרות חדשות ב-`core/config.py` (`Settings`): `FIELD_RAW_RETENTION_DAYS=90`, `FIELD_HEARTBEAT_RETENTION_DAYS=30`, `FIELD_HOURLY_AGG_RETENTION_DAYS=400`, `FIELD_TOKEN_PEPPER` (חובה בפרודקשן), `FIELD_MAX_BATCH_BYTES=5_000_000` (אחרי פריסה), `FIELD_VIEWER_K_MIN=3`, `FIELD_PUBLIC_BASE_URL` (לתוכן ה-QR), `FIELD_STALE_CELL_MS=10000` (v2 — ערך החוזה; הגדרה רק לבדיקות), `FIELD_MAX_LOC_AGE_MS=30000`, `FIELD_COMMAND_TTL_MIN=20`, `FIELD_OFFLINE_MIN=15`, `FIELD_NO_CONTACT_H=6`.

### 3.2 אימות מכשיר — `get_current_device`
- `HTTPBearer(auto_error=False)` (לא `OAuth2PasswordBearer` — כדי ש-Swagger יציג סכימה נפרדת).
- פורמט טוקן: `fdt_` + 43 תווי base64url (256 ביט מ-`secrets.token_urlsafe(32)`).
- בדיקה: `sha256(pepper + token)` → חיפוש `field_devices.token_hash` → `status == 'active'`. אין bcrypt — הטוקן בעל אנטרופיה גבוהה, ו-bcrypt לכל batch יחנוק את הקליטה.
- **401 רק לטוקן לא תקף/מבוטל** (v2) — גוף `{"detail": "טוקן מכשיר לא תקף", "detail_code": "token_invalid"}`. האפליקציה מוחקת נתונים מקומיים אחרי שני 401 בהפרש 60 ש' (אפיון האנדרואיד), ולכן **אסור** ש-401 יצא בגלל תקלה זמנית: כל חריגה בגישה ל-DB בתוך ה-dependency → **503 + `Retry-After: 30`**, לא 401. נבדק בבדיקה ייעודית (10.1).
- **הפרדה:** טוקן מכשיר אינו JWT → `get_current_user` נכשל עליו. JWT משתמש אינו בפורמט `fdt_` → `get_current_device` דוחה. שתי ההפרדות נבדקות.
- `last_seen` לא נכתב ב-dependency; נכתב בקליטה עצמה.

### 3.3 ממשקי המכשיר (לפי החוזה v2)

#### `POST /api/field/devices/enroll` — ללא אימות
גוף: `{enrollment_code, device_uuid, model, manufacturer, os_version, app_version, ownership?, label?, consent_version?, consented_at?}`.

סדר בדיקות ותשובות (v2 — גוף `{detail, detail_code}`):
| מצב | HTTP | `detail_code` |
|---|---|---|
| גוף לא תקין (שדה חסר/פורמט) | 422 | — (שגיאת מעטפה) |
| קוד לא קיים או בוטל (`revoked_at`) | 400 | `code_invalid` |
| `expires_at ≤ now` | 410 | `code_expired` |
| `use_count ≥ max_uses` | 409 | `code_used` |
| `ownership` נשלח ושונה מבעלות הקוד | 409 | `ownership_mismatch` |
| בעלות הקוד `PERSONAL` ואין `consent_version` + `consented_at` | 422 | `consent_required` (שם קוד מוצע; ראו 12.2) |
| הצלחה | 201 | — |

- נרמול הקוד: uppercase, הסרת `-`/רווחים → hash → חיפוש.
- `ownership` בתשובה = של הקוד (v2: "נקבע בקוד ומוחזר לאפליקציה").
- `consented_at` ISO UTC; נדחה (422) אם בעתיד (> now + 5 דק') או ישן מ-7 ימים (הסכמה לא אמורה להיות ישנה מהרישום). `consent_version` ≤ 32 תווים. ב-ORG השדות אופציונליים ונשמרים אם נשלחו.
- **רישום מחדש:** v2 קובע `device_uuid` חדש בכל רישום (ברירת מחדל) → כל enroll יוצר רשומת מכשיר חדשה; ההיסטוריה של המכשיר הקודם נשארת תחת הרשומה הקודמת (ADMIN יכול לבטל אותה). קישור בין רשומות — שאלה פתוחה (11.5).
- **ניסיון חוזר של אותו enroll** (התשובה אבדה ברשת): אם מגיע enroll עם **אותו קוד ואותו `device_uuid`** שכבר נרשם ב-15 הדק' האחרונות, ושהטוקן שהונפק עוד לא שימש (אין heartbeat/batch) — מונפק טוקן חדש לאותה רשומה (הקודם נפסל), 201, **בלי** לספור שימוש נוסף. אחרת `device_uuid` קיים → 409 `code_used`. בלי זה, קוד חד-פעמי + אובדן תשובה = מכשיר תקוע. **לתאם עם צוות האנדרואיד** (12.2).
- הנעילה: `use_count` מוגדל ב-`UPDATE … WHERE use_count < max_uses` (אטומי).
- **הערת אבטחה:** הבחנה בין `code_invalid`/`code_expired`/`code_used` חושפת אם קוד קיים. הסיכון נמוך (~49 ביט אנטרופיה + הגבלת קצב), ולכן מקובל — אך ההגבלה לפי IP חובה (10/דקה; אחרי 5 כשלונות ב-10 דק' → 429 ל-15 דק').
- תשובה 201: `{device_id, device_token, ownership, config}`.
- יומן: `FIELD_DEVICE_ENROLLED` ב-`activity_log` (user_id = יוצר הקוד, detail = device_id, ownership, consent_version).

#### `POST /api/field/measurements/batch` — טוקן מכשיר
- **gzip (v2):** `Content-Encoding: gzip` + `Content-Type: application/json`. `GZipMiddleware` הקיים (`main.py`) דוחס **תגובות** בלבד; ה-endpoint קורא `await request.body()` ופורס ב-`zlib.decompressobj` עם תקרה `FIELD_MAX_BATCH_BYTES` (5MB) → 413. גוף דחוס > 1MB → 413 לפני פריסה. ללא `Content-Encoding` — מתקבל JSON רגיל (לבדיקות וסימולטור).
- **מעטפה:** `device_id` ≠ `public_id` של בעל הטוקן → **403 `device_mismatch`** (v2). `samples` 1–500 → מעל 500 → 413. מעטפה פגומה → 422.
- **נורמליזציה לפני אימות:** ערכי sentinel של אנדרואיד (`2147483647`, `Long.MAX_VALUE`, ובשדות NR גם `Integer.MIN_VALUE`) → null + flag 128. זה לא "מחוץ לטווח" — זה "לא זמין".
- **אימות דגימה בודדת — רשימה סגורה (v2), כל הסיבות סופיות:**

  | בדיקה | `reason` |
  |---|---|
  | `sample_id` לא UUID | `invalid_sample_id` |
  | `ts` חסר/לא פרסיבילי | `invalid_ts` |
  | `ts > sent_at + 5 דק'` או `ts > now + 5 דק'` | `ts_in_future` |
  | `ts < now − FIELD_RAW_RETENTION_DAYS` | `ts_too_old` |
  | ערך enum לא מוכר (`network_type`, `service_state`, `data_state`, `loc_provider`, `trigger`, `neighbors[].rat`) | `invalid_enum:<field>` |
  | ערך מספרי מחוץ לטווח הפיזיקלי (טבלה למטה) | `out_of_range:<field>` |

  טווחים (לפי v2: "השרת מאמת"; אותם טווחים מופיעים בטבלה 3.2 באפיון האנדרואיד שעושה null + מונה בצד המכשיר, כך שבפועל הדחייה בשרת צפויה להיות נדירה):

  | שדה | טווח |
  |---|---|
  | lat / lon | [-90, 90] / [-180, 180]; רק אחד מהם null → `out_of_range:lat` |
  | loc_accuracy_m | [0, 50000] |
  | rsrp_dbm (LTE/NSA) / SS-RSRP (SA, nr_leg) | [-156, -31] |
  | rsrq_db | [-43, 20] |
  | sinr_db | [-23, 40] |
  | rssi_dbm | [-130, -10] |
  | battery_pct | [0, 100] |
  | signal_level | [0, 4] |
  | timing_advance | [0, 1282] (אינדקס LTE) |
  | timing_advance_us | [0, 2000] |
  | cell_info_age_ms, loc_age_ms | [0, 86 400 000] |
  | sim_slot | [0, 3] |

  **טווחי ה-RF הם לפי טווחי הדיווח של 3GPP/Android כפי שאני מכיר אותם — לאמת מול טבלה 3.2 של צוות האנדרואיד לפני הקפאה**, כדי ששני הצדדים ידחו/יאפסו באותם ספים.
- **תיקונים שקטים (לא דחייה):** `loc_accuracy_m` לא-null כש-lat/lon null → מאופס (v2 קובע null; אין סיבת דחייה מתאימה ברשימה הסגורה, וזריקת הדגימה כולה בגלל שדה מטא לא מוצדקת); `neighbors` > 8 → חיתוך; `active_test` בדגימה שה-`trigger` שלה אינו `active_test` → מאופס. כל תיקון נספר במונה שרת (`field_ingest_fixups_total{field}`).
- **מחוץ לישראל** (bbox 29.3–33.5, 34.0–36.0): מתקבל, flag 2, לא נכנס לגריד.
- **סינון מיושנות (v2):** `cell_info_age_ms > 10000` → נשמר גולמי, flag 16 (`stale_cell`); לא נכנס ל-KPI, לאגרגציות, להערכת התראות קליטה ולעדכון `last_tier` (ראו 5.3). `cell_info_age_ms` null → נחשב טרי (המכשיר לא דיווח) — **הנחה, לאשר מול האנדרואיד** (12.2).
- **אידמפוטנטיות:** (1) `SELECT sample_id … WHERE device_pk=? AND sample_id IN (…)` → `duplicates`; (2) `INSERT … ON CONFLICT DO NOTHING` (`postgresql.insert` / `sqlite.insert`) לכיסוי מרוץ. אינווריאנט `accepted + duplicates + len(rejected) == len(samples)`.
- באותה טרנזקציה: `tier`, `meas_tech`, `gx/gy`, `gx25/gy25`, `operator_key`, flags; עדכון `last_*` רק מדגימה חדשה יותר לפי `ts` (ובלי `stale_cell` לשדות האות); הוספת דליים ל-`field_agg_dirty`; הערכת התראות מיידיות (7).
- תשובה 200: `{accepted, duplicates, rejected: [{sample_id, reason}], config_version}`.
- הגבלת קצב: 30 בקשות/דקה למכשיר (burst 10) → 429 + `Retry-After`. עומס DB → 503 + `Retry-After` (v2).

#### `POST /api/field/devices/heartbeat` — טוקן מכשיר
גוף (מקורי + v2): `{ts, battery_pct, is_charging, app_version, queue_size, permissions: {location: precise|approximate|none, background_location, phone_state, notifications, battery_optimization_exempt}, monitoring_state, degraded_reasons: [string], effective_location_mode, location_services_enabled, applied_config_version, evicted_samples_total, last_sample_ts, acked_commands}`.
- **`permissions.location`** כעת enum (v2). ערך בוליאני ישן → 422 (אין תאימות לאחור; האפליקציה נבנית מול v2).
- **`degraded_reasons`:** הרשימה הסגורה מוגדרת ע"י צוות האנדרואיד (v2) — כרגע 15 קודים בסעיף 5.6 באפיון שלהם (`location_permission_missing`, `location_approximate_only`, `background_location_missing`, …). השרת **לא דוחה** קוד לא מוכר (שומר ומציג אותו גולמי עם תווית "סיבה לא מוכרת: …") — כדי ששינוי באפליקציה לא ישבור heartbeats; קוד לא מוכר נספר במונה ומדווח לצוות. רשימת התוויות העבריות נלקחת מהטבלה שלהם ותוחזק ב-`lib/fieldStatus.ts` + `services/field_status.py`.
- **`effective_location_mode`:** `precise|coarse|off`; ב-PERSONAL הוא "המחמיר" מבין המשתמש לשרת (v2) — השרת מציג, לא מתקן.
- **`acked_commands`:** v2 מגדיר `[id]`, אך גם "ack עם `skipped_paused`" — רשימת מזהים לא נושאת סטטוס (סתירה; ראו 12.2). השרת **מקבל את שתי הצורות**: מחרוזת (= `done`) או `{id, status: done|skipped_paused|unsupported|failed}`. מזהה לא מוכר / של מכשיר אחר → מתעלמים ונספר.
- **`evicted_samples_total`:** מונה מצטבר; ירידה (התקנה מחדש/איפוס) → נשמר כבסיס חדש בלי התראה.
- פעולה: הוספה ל-`field_heartbeats`, עדכון `last_*` ו-`state_changed_at`, עדכון סטטוס הפקודות, הערכת התראות.
- **תשובה 200 (v2):** `{config_version, server_time, commands: [{id, type, issued_at}]}` — כל הפקודות בסטטוס `queued`/`delivered` שלא פג תוקפן (כולל `delivered` שלא אושרו — מסירה חוזרת; האפליקציה עושה דה-דופליקציה לפי id). הראשונות מסומנות `delivered_at`.
- **השרת לא מסנן פקודות לפי מצב** — גם ל-PERSONAL מושהה הן נשלחות, והמכשיר מחזיר `skipped_paused` (v2). (ה-UI מזהיר מראש — 8.3.)
- קצב צפוי (v2): כל 5 דק' ב-`active|degraded`; best-effort לפחות כל 15 דק' במושהה. הגבלת קצב: 6/דקה למכשיר.

#### `GET /api/field/devices/me/config` — טוקן מכשיר
התצורה האפקטיבית המלאה (כל שדות החוזה כולל `config_version`, `allow_user_pause`, `schedule` לפי v2). `ETag: "<config_version>"`; `If-None-Match` תואם → 304.

#### `POST /api/field/devices/me/withdraw` — טוקן מכשיר (חדש ב-MVP, v2)
- גוף: ריק או `{reason?: string ≤ 128}`.
- פעולה **סינכרונית**: `token_hash` מאופס, `status='withdrawn'`, `withdrawn_at`, ביטול כל הפקודות הפתוחות, רישום `FIELD_DEVICE_WITHDRAWN` (user_id = null, detail = device_id, ownership). **אסינכרונית** (משימת התחזוקה הבאה, ≤ 5 דק'): מחיקה/אנונימיזציה של הנתונים לפי 9.6.
- תשובה 200 `{status: "withdrawn", data_action: "delete"|"anonymize", completes_by}`. קריאה חוזרת → 401 (הטוקן כבר בוטל) — אפיון האנדרואיד מתייחס ל-401 על withdraw כהצלחה, ולכן זה תקין.
- PERSONAL → `delete` (חובה לפי v2). ORG → `anonymize` — **החלטה שלי, לא בחוזה**; ראו 12.2 (ההרשאה לעובד למחוק נתוני ארגון).
- batch/heartbeat שמגיעים אחרי withdraw → 401.

### 3.4 ממשקי הדאשבורד
מוסכמות: `{fields, rows}` לרשימות גדולות (כמו `/api/cell-sites/`); זמנים ISO עם `Z`; פרמטרי זמן `from`/`to` (ISO, ברירת מחדל 24 ש' אחרונות, מקסימום 90 יום לגולמי, ללא הגבלה לאגרגציות יומיות); `bbox=sw_lat,sw_lon,ne_lat,ne_lon`; פילטרים מרובים מופרדים בפסיקים (כמו `operator` ב-cell-sites). VIEWER = רק נתונים מצרפיים (9.4).

**תפקידים:** A = ADMIN, O = OPERATOR, V = VIEWER.

| # | Method | Path | תפקיד | פרמטרים | תשובה (תמצית) |
|---|---|---|---|---|---|
| 1 | GET | `/summary` | A,O,V | `from,to,group_id?,operator?` | `{fleet:{total,active,degraded,paused,no_contact,offline,never_seen,withdrawn}, kpis:{pct_time_by_tier[5], pct_time_unclassified, pct_time_unknown, rsrp:{lte:{p5,p50,n}, nr_sa:{p5,p50,n}, nsa_nr_leg:{p5,p50,n}}, oos_minutes, cell_change_per_hour, devices_reporting_15m, stale_excluded_pct}, freshness:{last_sample_received_at}}`. ל-V: `fleet` רק סכומים (ללא פילוח לפי קבוצה/מכשיר) |
| 2 | GET | `/devices` | A,O | `group_id?,status?,ownership?,q?,sort?` | `{fields:[id,label,ownership,group,status,status_since,monitoring_state,degraded_reasons,effective_location_mode,tier,tier_stale,rsrp,nr_ss_rsrp,operator_name,network_type,battery_pct,is_charging,last_seen_at,app_version,config_pending,open_alerts,open_commands], rows}`. `status` לפי 3.5 |
| 3 | GET | `/devices/live` | A,O | `bbox?,since?` | `{server_time, fields:[id,lat,lon,acc,precision,tier,tier_stale,rsrp,network_type,operator_key,status,loc_ts], rows}` — רק מכשירים `active|degraded|offline` שמיקומם ≤ 60 דק' (מושהים לא מוחזרים). `precision=coarse` → הלקוח מצייר עיגול ~1 ק"מ ולא נקודה (2.4); `since` מחזיר רק שהשתנו |
| 4 | GET | `/devices/{id}` | A,O | — | כל שדות המכשיר + תצורה אפקטיבית (וגם `applied_config_version`) + `permissions` + `degraded_reasons` (קוד + תווית) + `effective_location_mode` + `current_cell` (עוגן LTE ב-NSA) + `nr_leg` אחרון + 10 הפקודות האחרונות + התראות פתוחות. **ללא** מיקום היסטורי |
| 5 | GET | `/devices/{id}/track` | A,O | `from,to (≤ 7 ימים),max_points=2000` | GeoJSON LineString מפולח לפי רמה + נקודות; קטעי coarse כ-Polygon של תא 0.01° ולא כקו (2.4); נקודות `stale_cell` מסומנות `properties.stale=true` ובצבע אפור; דילול (Douglas-Peucker על זמן-מרחק) מעבר ל-`max_points`. **רושם `FIELD_TRACK_VIEW`** |
| 6 | GET | `/devices/{id}/timeseries` | A,O | `metric=rsrp,nr_ss_rsrp,rsrq,sinr,battery; from,to; bucket=auto` | `{metric, unit, bucket_s, points:[[t, min, avg, max, n, tier_mode]], gaps:[[t0,t1,reason]]}` — `reason`: `oos` / `no_data` / `paused` / `stale`. `rsrp` = LTE RSRP (כולל עוגן NSA) ו-SS-RSRP ב-SA מוחזרים כסדרות נפרדות לפי `meas_tech`, לעולם לא באותה סדרה (v2). ללא מיקום → לא נרשם ביומן (החוזה מחייב רק מסלול) |
| 7 | PATCH | `/devices/{id}` | A | `{label?, group_id?, notes?, config_override?}` | המכשיר; שינוי group/override מעלה `config_version` |
| 8 | POST | `/devices/{id}/revoke` | A | — | `{status:"revoked"}`; הטוקן נפסל מיד |
| 9 | DELETE | `/devices/{id}` | A | `mode=delete\|anonymize` | 202 `{data_action, completes_by}`; ראו 9.6 |
| 9a | POST | `/devices/{id}/commands` | A,O | `{type: sample_now\|upload_now}` | 201 `{id,type,status:"queued",issued_at,expires_at,warning_he?}`; 409 `command_pending` אם יש פקודה פתוחה מאותו סוג; 409 `device_inactive` למכשיר `revoked/withdrawn/deleted`; `warning_he` כשהמכשיר PERSONAL מושהה ("צפוי skipped_paused"). מגבלה 10/שעה למכשיר. **רושם `FIELD_COMMAND_ISSUED`** (user, device, type) |
| 9b | GET | `/devices/{id}/commands` | A,O | `limit=20` | `[{id,type,status,issued_by,issued_at,delivered_at,acked_at,expires_at}]` |
| 9c | DELETE | `/commands/{id}` | A,O | — | ביטול פקודה ב-`queued` בלבד (לא אחרי מסירה) → `cancelled`; רושם `FIELD_COMMAND_CANCELLED` |
| 10 | GET | `/heatmap` | A,O,V | `bbox (חובה),from,to,operator?,measure=lte_rsrp\|nr_ss_rsrp (חובה, v2 — אין ברירת מחדל מעורבת),metric=p50\|p5\|tier\|n\|oos_pct,level=100\|500\|2500` (רמה 2500 כוללת דגימות coarse ומסומנת כך) | `{level, cell_deg:[dlat,dlon], fields:[gx,gy,n,value,tier,n_devices*], rows, suppressed}` — \* `n_devices` רק ל-A,O; ל-V תאים עם < k מכשירים מושמטים ו-`suppressed` סופר אותם. מקסימום 20K תאים — אחרת 413 "הגדל רמה" |
| 11 | GET | `/timeseries` | A,O,V | `metric,from,to,bucket=1h\|1d,group_id?,operator?,meas_tech?,bbox?` | `{series:[{key, meas_tech, points:[[t,value,n]]}]}` מצרפי בלבד; מדדי RSRP תמיד מפוצלים לפי `meas_tech` |
| 12 | GET | `/breakdown` | A,O,V | `by=operator\|tech\|band\|cell\|group,from,to,bbox?` | שורה לכל ערך: `{key,label,n,time_s,pct_time_by_tier,rsrp:{lte,nr_sa,nsa_nr_leg},oos_min,cell_changes_per_h,nsa_share}`. `by=group` — A,O בלבד. `by=cell` מחזיר `operator_key+cell_id` (לא מזהה אדם; ל-V עם סף k) |
| 13 | GET | `/dead-zones` | A,O,V | `from,to,operator?,tech?,bbox?` | `[{id, operator_key, tech, centroid_lat, centroid_lon, cells, area_km2, p50, oos_pct, n, n_devices*, severity, first_seen, last_seen, polygon}]` (5.4) |
| 14 | GET | `/compare/coverage/{result_id}` | A,O,V | `from,to,bw_mhz=10` | 5.5. ל-V ללא נקודות גולמיות — רק סטטיסטיקה, מטריצת בלבול ושאריות בגריד עם סף k |
| 15 | GET | `/calibration` | A,O | `operator?,from,to` | 5.6 — היסט/שיפוע לכל מפעיל וסוג אתר + מספר דגימות משויכות |
| 16 | GET/PUT | `/cell-site-map` | GET A,O; PUT A,O | PUT `{operator_key,node_id,cell_site_id\|null}` | התאמה ידנית (`source=manual`) |
| 17 | GET | `/predict/cells` | A,O,V | `bbox,operator,tech,hour_of_week?` | `{model_stage:"P0", fields:[gx,gy,pred_p50,pi80_lo,pi80_hi,p_tier_ge_medium,confidence,n,n_days], rows}`; `confidence ∈ high/medium/low/insufficient` (6.2) |
| 18 | POST | `/predict/route` | A,O,V | `{points:[[lat,lon]…≤500], operator, tech, depart_at?}` | פילוח מסלול ל-100 מ' עם ערך/רווח/ביטחון לכל קטע + `% קטעים insufficient` |
| 19 | GET | `/predict/validation` | A,O,V | — | תוצאות backtest אחרונות (6.4) |
| 20 | GET | `/device-health` | A,O | `group_id?` | `[{device_id,label,battery_pct,drain_pct_per_h,eta_empty_min,offline_risk,reasons[],evicted_last_24h}]` (6.6) |
| 21 | GET | `/alerts` | A,O | `status=open,acked,resolved; type?; device_id?; limit=200` | רשימה (סעיף 7) |
| 22 | POST | `/alerts/{id}/ack` · `/alerts/{id}/resolve` | A,O | `{note?}` | ההתראה |
| 23 | GET/PUT | `/alert-rules` · `/alert-rules/{id}` | GET A,O · PUT A | `{enabled?,params?,severity?}` | |
| 24 | GET | `/enrollment-codes` | A | `include_expired=false` | `[{id,label,code_hint,group,ownership,max_uses,use_count,expires_at,created_by,revoked_at}]` |
| 25 | POST | `/enrollment-codes` | A | `{label?,group_id?,ownership: ORG\|PERSONAL (חובה, v2),max_uses=1 (1–200),ttl_hours=24 (1–720)}` | 201 `{…, code, qr_payload, qr_svg?}` — `qr_payload` = `coverageops-enroll:v1?server=<FIELD_PUBLIC_BASE_URL>&code=<CODE>` (v2); `qr_svg` רק אם אושרה התלות (8.5) — **הקוד בטקסט גלוי מוחזר רק כאן** |
| 26 | DELETE | `/enrollment-codes/{id}` | A | — | ביטול (`revoked_at`), 204 |
| 27 | GET | `/groups` | A,O | — | `[{id,name,description,config,device_count}]` |
| 28 | POST/PUT/DELETE | `/groups` · `/groups/{id}` | A | גוף: `{name,description?,config}` | אימות טווחים: `sampling_interval_s 5–3600`, `min_distance_m 0–1000`, `stationary_interval_s 30–3600`, `upload_interval_s 15–3600`, `active_test_interval_s 0 או 300–86400`, `low_battery_threshold_pct 5–50`, `schedule` ≤ 14 חלונות, `days ⊆ 0..6`, `start/end "HH:MM"`. מחיקת קבוצה עם מכשירים → 409. הטווחים אושרו ב-v2. שדות v2: `allow_user_pause` (נעול `true` ל-PERSONAL), `schedule` (3.6) |
| 29 | GET | `/export/measurements.csv` · `.geojson` | A,O | `device_id?,group_id?,from,to (≤31 ימים),bbox?` | זרם (StreamingResponse), תקרה 2M שורות → 413; `Content-Disposition` עם `filename*`. **רושם `FIELD_EXPORT`** (מכיל מיקום ברמת מכשיר) |
| 30 | GET | `/export/heatmap.geojson` · `/export/kpis.csv` | A,O,V | כמו `/heatmap` / `/breakdown` | מצרפי; ל-V עם סף k |
| 31 | GET | `/audit` | A | `action?,user_id?,device_id?,from,to` | רשומות `activity_log` שה-action שלהן מתחיל ב-`FIELD_` |
| 32 | GET | `/operators` | A,O,V | — | `field_operator_map` |
| 33 | POST | `/maintenance/run` | A | `task=aggregate\|retention\|partitions\|alerts\|purge\|commands` | הרצה ידנית (לפיתוח/חירום) |

סדר הגדרת נתיבים בקובץ: כל הנתיבים הסטטיים (`/devices/live`) לפני `/devices/{id}` (TECHNICAL.md).

### 3.5 סטטוס חי של מכשיר (מחושב, לא נשמר) — לפי v2
`hb_age = now − last_heartbeat_at`. סדר עדיפויות:

| # | סטטוס (key) | תנאי | תווית בממשק |
|---|---|---|---|
| 1 | `withdrawn` | `status='withdrawn'` | "ההסכמה בוטלה" |
| 2 | `revoked` | `status='revoked'` | "בוטל" |
| 3 | `never_seen` | אין heartbeat ואין דגימה | "טרם התחבר" |
| 4 | `offline` | `last_monitoring_state ∈ {active, degraded}` **וגם** `hb_age > 15 דק'` (v2) | "לא מקוון · נראה לאחרונה …" |
| 5 | `no_contact` | `last_monitoring_state ∈ {paused_by_user, paused_schedule}` **וגם** `hb_age > 6 ש'` (v2) | "ללא קשר · מושהה מאז …" |
| 6 | `paused` | `last_monitoring_state ∈ {paused_by_user, paused_schedule}` | "מושהה ע"י המשתמש · נראה לאחרונה …" / "מחוץ למשמרת · נראה לאחרונה …" |
| 7 | `degraded` | `last_monitoring_state = 'degraded'` | "ניטור מוגבל" + תוויות `degraded_reasons` |
| 8 | `active` | אחרת | "פעיל" |

- **השרת לא גוזר עוד "degraded" בעצמו מהרשאות** (הגרסה הקודמת עשתה זאת): v2 מעביר את ההגדרה לאפליקציה (`degraded_reasons`). השרת רק מציג.
- תת-דגלים (מוצגים כאייקון ליד הסטטוס, לא משנים אותו): `config_pending` — `last_applied_config_version < config_version` במשך > 15 דק'; `no_recent_samples` — `active` ו-`now − last_sample_at > max(3 × upload_interval_s, 10 דק')`; `upload_lag` — `last_sample_ts_reported − last_sample_at > 15 דק'` (המכשיר אוסף אך לא מעלה); `cell_stale` — הדגימה האחרונה `stale_cell`; `coarse` — `last_effective_location_mode = coarse`.
- מכשיר מושהה לא מוצג במפה החיה (גם אין לו מיקום עדכני); בטבלה — שורה עם "מושהה · נראה לאחרונה לפני X".

### 3.6 תצורה וגרסאות
- אפקטיבית = ברירות מחדל של החוזה ⊕ `group.config` ⊕ `device.config_override` (מיזוג רדוד לפי מפתח).
- `config_version` **לכל מכשיר**, מונוטוני: שינוי ב-config של קבוצה מריץ `UPDATE field_devices SET config_version = config_version + 1 WHERE group_id = :g` (ובקבוצת ברירת המחדל — גם `group_id IS NULL`); שינוי override/העברת קבוצה מעלה את המכשיר עצמו.
- **שדות v2:**
  - `allow_user_pause: bool` — ב-ORG ברירת מחדל `true` (שאלה פתוחה לבעל המוצר); ב-PERSONAL **השרת כותב תמיד `true` בתצורה האפקטיבית** ומתעלם מערך אחר בקבוצה/override (וה-UI מציג את השדה נעול עם הסבר).
  - `schedule`: `[{days: [0..6] (0=ראשון), start: "HH:MM", end: "HH:MM"}]` בשעון Asia/Jerusalem; `end ≤ start` = חוצה חצות; null/ריק = PERSONAL ידני בלבד, ORG 24/7. אימות: ≤ 14 חלונות, `days` לא ריק וללא כפילויות, `start == end` → 422 (חלון של 0 או 24 ש' לא חד-משמעי).
  - `location_mode`: ב-PERSONAL הערך בשרת הוא **תקרה** בלבד — המשתמש יכול רק להחמיר; המצב בפועל מגיע ב-`effective_location_mode`. ה-UI מציג את שניהם ("הוגדר: precise · בפועל: coarse").
- טווחי התצורה (v2: לפי טבלה 3.4 #28) — השרת מאמת (422), האפליקציה עושה clamp.

### 3.7 הגבלת קצב
- ברשימת התלויות שנמסרה אין ספריית rate-limit. מוצע: token bucket בזיכרון התהליך לפי `device_pk` (ingest) ולפי IP (enroll), במודול `app/core/ratelimit.py` — ללא תלות חדשה.
- מגבלה: עם כמה workers של uvicorn המגבלה היא לכל worker (גבול עליון ×workers). ההגנה העיקרית מול IP (enroll) מומלצת גם ב-Nginx (`limit_req`) בפרודקשן (docker-compose.prod כולל Nginx לפי TECHNICAL.md).
- התשובה: 429 + `Retry-After` שניות.

---

## 4. עדכונים חיים — המלצה: **Polling עם react-query ב-MVP**

| | Polling (react-query `refetchInterval`) | SSE | WebSocket |
|---|---|---|---|
| התאמה למחסנית | קיים כבר (`useQuery` ב-`MapPage.tsx`) | צריך StreamingResponse ארוך + pub/sub בין workers | צריך שכבת WS + pub/sub |
| אימות | axios + interceptor הקיים (`services/api.ts`) | `EventSource` **לא תומך בכותרות** → JWT ב-query string → דליפה ללוגים של Nginx | אפשרי, אבל עוד מנגנון |
| Nginx/פרוקסי | ללא שינוי | דורש `proxy_buffering off`, timeouts | דורש Upgrade |
| טריות מקסימלית | מוגבלת ממילא ע"י `upload_interval_s` = 60 ש' (החוזה) ו-heartbeat כל 5 דק' | אותו דבר — הנתון לא מגיע לשרת מהר יותר | אותו דבר |

**ההחלטה:** polling. מרווחים: `/devices/live` כל 10 ש' (עם `since` — תשובה ריקה כמעט תמיד), `/summary` ו-`/alerts?status=open` כל 30 ש', `/devices` כל 30 ש'; `refetchIntervalInBackground: false` (טאב לא פעיל לא מושך). עומס צפוי: 20 משתמשים פתוחים × ~0.2 req/s = 4 req/s של שאילתות זולות — זניח.
מעבר ל-SSE ישקל ב-M6 רק אם יוגדר צורך < 5 ש' **וגם** יוקטן `upload_interval_s`.

**פקודות:** המסירה עצמה תלויה ב-heartbeat (עד 5 דק' — v2), כך שגם כאן polling מספיק: כשהמגירה פתוחה ויש פקודה פתוחה, `GET /devices/{id}/commands` כל 10 ש'; אחרת לא. ה-UI מציג "נשלחה · ממתינה למכשיר (עד ~5 דק')" ולא ספינר אינסופי.

---

## 5. ניתוח

### 5.1 סיווג רמה (מקור אמת יחיד) — לפי v2
פונקציה `classify_tier(network_type, rsrp_dbm, service_state) -> int | None` ב-`services/field_quality.py`, ומראה ב-`frontend/src/lib/fieldTiers.ts`.

| ערך | רמה | תנאי (v2, חצי-פתוח) | token |
|---|---|---|---|
| 4 | מצוין | `rsrp ≥ -85` | `--cov-excellent` |
| 3 | טוב | `-95 ≤ rsrp < -85` | `--cov-good` |
| 2 | בינוני | `-105 ≤ rsrp < -95` | `--cov-medium` |
| 1 | חלש | `-115 ≤ rsrp < -105` | `--cov-weak` |
| 0 | שולי/אין | `rsrp < -115` **או** `service_state ∈ {out_of_service, emergency_only}` (בכל טכנולוגיה) | `--cov-marginal` |
| NULL | לא מסווג | `power_off`; או `in_service` בלי RSRP (UMTS/GSM, או null) | `--cov-unk` (אפור) |

- **איזה RSRP:** `LTE` ו-**`NR_NSA` → `rsrp_dbm` של עוגן ה-LTE** (v2); `NR_SA` → `rsrp_dbm` שהוא SS-RSRP. ה-`nr_leg` **לא** משפיע על הרמה.
- **"לא מסווג" מחוץ למכנה** של אחוזי הרמות (v2), ומוצג בנפרד ("לא מסווג: 2.1% מהזמן").
- `service_state` גובר על RSRP: OOS עם `rsrp=-80` → 0.
- **הרמה מחושבת גם לדגימה מיושנת** (`stale_cell`) — הסינון הוא בשימוש, לא בסיווג. כך הסיווג נשאר פונקציה טהורה של שלושת הקלטים.
- `meas_tech` (מפתח לאגרגציה ול-KPI): `LTE` ← `LTE`, `NR_NSA`; `NR` ← `NR_SA`; `LEGACY` ← `UMTS`, `GSM`; `NONE` ← `NONE`.

#### `field_tier_vectors.json` — פורמט ותוכן (הבעלים: צוות הדאשבורד, v2)
מיקום: `backend/tests/fixtures/field_tier_vectors.json` בריפו של CoverageOps. נקרא ע"י pytest, ע"י בדיקת יחידה בפרונטאנד, ומועתק לאפליקציה. שינוי = עדכון `version` + הודעה לצוות האנדרואיד.
```json
{
  "schema": "coverageops.field_tier_vectors",
  "version": 1,
  "contract": "SHARED_CONTRACT v2",
  "tiers": {"4": "excellent", "3": "good", "2": "medium", "1": "weak", "0": "marginal", "null": "unclassified"},
  "vectors": [
    {"id": "lte_excellent_boundary", "network_type": "LTE", "service_state": "in_service",
     "rsrp_dbm": -85.0, "expected_tier": 4, "note": "x >= -85"}
  ]
}
```
כללים: `rsrp_dbm` מספר או `null` (אחרי נורמליזציית sentinel); `expected_tier` שלם 0–4 או `null`; `id` ייחודי; ההשוואה על הערך כפי שהוא, בלי עיגול. וקטורי נורמליזציה (sentinel) נמצאים במקטע נפרד `"normalization"` כי הם בודקים את שלב הקלט, לא את הסיווג.

רשימת הווקטורים (גרסה 1):

| id | network_type | service_state | rsrp_dbm | expected |
|---|---|---|---|---|
| lte_max | LTE | in_service | -31 | 4 |
| lte_excellent_boundary | LTE | in_service | -85 | 4 |
| lte_just_below_85 | LTE | in_service | -85.1 | 3 |
| lte_good_lower_boundary | LTE | in_service | -95 | 3 |
| lte_just_below_95 | LTE | in_service | -95.1 | 2 |
| lte_medium_lower_boundary | LTE | in_service | -105 | 2 |
| lte_just_below_105 | LTE | in_service | -105.1 | 1 |
| lte_weak_lower_boundary | LTE | in_service | -115 | 1 |
| lte_just_below_115 | LTE | in_service | -115.1 | 0 |
| lte_min | LTE | in_service | -156 | 0 |
| nrsa_excellent_boundary | NR_SA | in_service | -85 | 4 |
| nrsa_good_lower_boundary | NR_SA | in_service | -95 | 3 |
| nrsa_just_below_115 | NR_SA | in_service | -115.1 | 0 |
| nsa_anchor_decides_weak | NR_NSA | in_service | -110 (+ `nr_leg.ss_rsrp_dbm: -80`) | 1 |
| nsa_anchor_decides_good | NR_NSA | in_service | -90 (+ `nr_leg.ss_rsrp_dbm: -120`) | 3 |
| lte_oos_strong_rsrp | LTE | out_of_service | -80 | 0 |
| lte_oos_null_rsrp | LTE | out_of_service | null | 0 |
| nrsa_emergency_only | NR_SA | emergency_only | -90 | 0 |
| none_oos | NONE | out_of_service | null | 0 |
| umts_emergency_only | UMTS | emergency_only | null | 0 |
| lte_power_off | LTE | power_off | -90 | null |
| none_power_off | NONE | power_off | null | null |
| lte_in_service_null_rsrp | LTE | in_service | null | null |
| nrsa_in_service_null_rsrp | NR_SA | in_service | null | null |
| umts_in_service | UMTS | in_service | null (`rssi_dbm: -75`) | null |
| gsm_in_service | GSM | in_service | null (`rssi_dbm: -95`) | null |
| none_in_service | NONE | in_service | null | null |
| lte_stale_still_classified | LTE | in_service | -100 (+ `cell_info_age_ms: 25000`) | 2 |

מקטע `normalization`: `rsrp_dbm: 2147483647 → null`; `rsrp_dbm: 9223372036854775807 → null`; `nr_leg.ss_rsrp_dbm: -2147483648 → null`; ואחרי הנורמליזציה התוצאה נבדקת שוב מול הסיווג (`LTE/in_service/null → null`).

**[הנחה]** `NONE` + `in_service` אינו מצב סביר; הווקטור קובע `null` כדי שהתנהגות תהיה מוגדרת.

### 5.2 משקל זמן (מדוע לא "אחוז דגימות" בלבד)
הדגימה אדפטיבית (30 ש' בתנועה, 300 ש' בעמידה, ועוד דגימות-טריגר ב-cell_change/service_change). לכן "אחוז דגימות" מוטה לכיוון תנועה ולכיוון אירועים (בדיוק סביב בעיות). מגדירים לכל דגימה:
`dt_s = min(ts_next − ts, cap)` כאשר `ts_next` היא הדגימה הבאה של **אותו מכשיר ואותו sim_slot**, ו-`cap = 2 × stationary_interval_s` (ברירת מחדל 600 ש'). פער גדול מ-cap (או מעבר דרך heartbeat עם `paused_*`) → הזמן שמעבר ל-cap נחשב "לא ידוע", לא נזקף לאף רמה.
- **"% זמן"** (KPI עיקרי) משתמש ב-`dt_s`.
- **"% דגימות"** מוצג משני — מתאים לשאלות מרחביות ("באיזה חלק מהמקומות שנמדדו").
- **"% מרחק"** (קילומטרים בכל רמה) — אופציונלי ל-M4 לצורך השוואה ל-drive-test.

### 5.3 KPI — הגדרות מדויקות
**כללי סינון לכל KPI ואגרגציה (v2 + החלטות):**
1. `stale_cell` (`cell_info_age_ms > 10000`) **לא נכנסות** (v2). זמן ה-`dt_s` שלהן נזקף ל"לא ידוע" ולא לאף רמה. מוצג מונה "סוננו N דגימות ישנות".
2. **אין ערבוב LTE RSRP ו-SS-RSRP** (v2): כל KPI של RSRP מחושב לפי `meas_tech` בנפרד ומוצג עם שם המדד:
   - "**RSRP ‏LTE**" — `meas_tech = LTE` (LTE + עוגן NSA). ניתן לפצל עוד ל-"LTE בלבד" / "עוגן NSA".
   - "**SS-RSRP ‏NR**" — `NR_SA` (שדה ראשי) **ובנפרד** "SS-RSRP רגל NR ב-NSA" (`nr_ss_rsrp_dbm`). שניהם אותו מדד פיזיקלי, אך מצבי פריסה שונים; הם מוצגים זה לצד זה ולא מאוחדים כברירת מחדל.
3. ברירת מחדל `is_roaming = false`; KPI מרחביים — רק דגימות עם `gx/gy` (לא coarse, דיוק ≤ 100 מ', מיקום טרי).
4. אחוזי רמות — מכנה ללא "לא מסווג" (v2).

| KPI | הגדרה | הערות |
|---|---|---|
| `pct_time_tier[k]` | `Σ dt_s(tier=k) / Σ dt_s(tier≠NULL)` | לצדו `pct_time_unclassified` ו-`pct_time_unknown` (פערים + מיושנות). הרמה ב-NSA לפי עוגן LTE (v2) |
| `pct_samples_tier[k]` | `count(tier=k) / count(tier≠NULL)` | משני |
| `rsrp_p5`, `rsrp_p50` | אחוזון 5/50 **לכל `meas_tech` בנפרד** (כלל 2 לעיל); מהגולמי לחלונות ≤ 7 ימים, מ-`rsrp_hist` מעבר | ממשק ה-API מחזיר `{lte: {p5,p50,n}, nr_sa: {…}, nsa_nr_leg: {…}}` — לעולם לא מספר אחד מעורב |
| `oos_minutes` | `Σ dt_s(service_state ∈ {out_of_service, emergency_only}) / 60` | `service_state` אינו תלוי בגיל נתוני התא — **אבל** הדגימה מסוננת אם `stale_cell` (v2 לא מבחין בין שדות); ראו 12.2 |
| `oos_events` | מעברים `in_service → oos/emergency` | |
| `cell_change_per_hour` | זוגות עוקבים (אותו מכשיר+SIM, שניהם לא-stale, פער ≤ cap) שבהם `(operator_key, cell_id)` השתנה (אם `cell_id` חסר — `(earfcn_arfcn, pci)`), חלקי שעות ניטור | ב-NSA — שינוי עוגן LTE בלבד; שינויי רגל NR נספרים בנפרד (`nr_leg_change_per_hour`). "שינויי תא נצפים", לא handover רשמי |
| `ping_pong_rate` | % שינויים A→B→A בתוך 60 ש' | |
| `fallback_pct` | % זמן ב-`UMTS/GSM/NONE` מתוך זמן עם שירות | |
| `nsa_share` | % זמן ב-`NR_NSA` מתוך `meas_tech=LTE` | "כמה מהזמן היה 5G NSA" |
| `data_connected_pct` | % זמן עם `data_state = connected` | |
| `active_test` | p50/p95 `latency_ms`, ממוצע `loss_pct`, p50 `dl_kbps/ul_kbps` | |
| `devices_reporting_15m` | מכשירים עם heartbeat ב-15 דק' / מכשירים במצב `active|degraded` | מכשירים מושהים לא במכנה (v2) |
| `data_loss_samples` | `Σ` עליות ב-`evicted_samples_total` בחלון | חדש (v2) |
| `stale_excluded_pct` | % דגימות שסוננו כ-`stale_cell` | שקיפות; אחוז גבוה במכשיר → ראו `degraded_reasons` (`cell_info_frozen`) |
| השוואת מפעילים | אותם KPI לפי `operator_key`, **רק בתאי גריד שנמדדו ע"י כל המפעילים המושווים** ("השוואה הוגנת") ובנוסף על הכל | |

### 5.4 זיהוי אזורים מתים
- **קלט:** אגרגציות 100 מ' בלבד — כלומר ללא `stale_cell` וללא coarse (2.4, 5.3); לכל `operator_key × meas_tech` בנפרד.
- **תא גריד "מת"** (לכל `operator_key × meas_tech`): בחלון הנבחר `n ≥ 10` דגימות, מ-`≥ 2` מכשירים **או** ב-`≥ 2` ימים שונים, ו-(`w0 / Σw ≥ 0.6` **או** `p50 < -115`). תנאי הריבוי מונע "אזור מת" מטלפון בכיס במעלית.
- **אשכולות:** רכיבים קשירים 8-שכנים (BFS) — אותה הגדרה כמו "אשכולות אפלה" ב-`/result/{id}/report` (TECHNICAL.md), למען עקביות מושגית.
- **חומרה:** `critical` אם `oos_pct ≥ 30%` או אשכול ≥ 5 תאים; `warning` אחרת.
- **"אזור מת לכל הצי"**: תא שבו כל המפעילים שנמדדו בו מתים.
- מוצג: פוליגון קווקו בצבע `--cov-marginal` + תווית "אזור מת" (לא צבע בלבד).

### 5.5 נמדד מול חזוי
**מה קיים:** `coverage_results.rssi_raster` הוא רסטר RSSI חזוי **לאתר יחיד** (`site_id`), int16 ×0.1 dB, zlib, שורה 0 = צפון, על ה-bbox השמור (TECHNICAL.md). פונקציית הפענוח ב-`coverage_engine` **[לא אומת — הקובץ לא זמין]**.

> **כל תוצאות "נמדד מול חזוי" הן אינדיקטיביות בלבד עד שהכיול (5.6) הושלם עבור המפעיל וסוג האתר.** המשפט הזה מוצג בממשק מעל כל תוצאה, בייצוא ובדוח — לא בהערת שוליים.

#### קבוע ההמרה RSSI→RSRP — הנחה וגזירה
**מה כל צד מודד:**
- **הצד החזוי:** המנוע מחשב `RSSI = Tx_power − FSPL − Diffraction − Clutter + HeightGain` (TECHNICAL.md), כאשר `Tx_power` הוא EIRP של האתר. ב-`cell_sites` ה-EIRP **משוער לפי סוג אתר** (`RF_NOTE_HE` ב-`api/cell_sites.py`). כלומר הערך החזוי הוא "סך ההספק המתקבל מהנשא", בהנחה שה-EIRP הוא ההספק הכולל של הנשא.
- **הצד הנמדד:** RSRP (3GPP TS 36.214) — ממוצע ההספק **לאלמנט משאב יחיד** (תת-נושא אחד של 15 kHz) שנושא אות ייחוס.

**גזירה (בהנחות):** ערוץ LTE ברוחב B מכיל `N_RB` בלוקים × 12 תת-נושאים. אם ההספק הכולל P מתחלק שווה על כל תת-הנושאים (עומס מלא, ללא הגברת אותות ייחוס, ואנטנה אחת), הספק לאלמנט אחד הוא P/(12·N_RB), ולכן:

`RSRP_pred ≈ RSSI_pred − 10·log10(12 · N_RB)`

| רוחב ערוץ | N_RB | 12·N_RB | היסט (dB) |
|---|---|---|---|
| 5 MHz | 25 | 300 | 24.8 |
| 10 MHz | 50 | 600 | **27.8** (ברירת המחדל בממשק) |
| 15 MHz | 75 | 900 | 29.5 |
| 20 MHz | 100 | 1200 | 30.8 |

**מדוע זו הנחה ולא קבוע:**
1. רוחב הערוץ של כל מפעיל/פס **לא ידוע** למערכת (ולכן `bw_mhz` הוא פרמטר גלוי; ברירת מחדל 10 MHz — בחירה, לא עובדה).
2. לא ידוע אם ה-EIRP המשוער מייצג הספק כולל לנשא, לכל יציאת אנטנה, או הספק אות ייחוס. בריבוי אנטנות (2T/4T) RSRP נמדד על יציאה 0 → ‎−3 עד ‎−6 dB נוספים אם ה-EIRP כולל את כל היציאות.
3. הגברת אותות ייחוס (power boosting, טיפוסית 0–3 dB) ועומס חלקי מזיזים את היחס בכמה dB.
4. "RSSI" של המנוע אינו RSSI של LTE (שכולל הפרעות ורעש); הוא הספק נשא בלבד.
5. ה-EIRP עצמו משוער לפי סוג אתר.

**הערכת אי-ודאות:** סכום הגורמים מגיע בקלות ל-±6 dB ויותר סביב 27.8. לכן ההיסט מוצג כ-"‎−27.8 dB (הנחה: 10 MHz, עומס מלא, יציאה אחת)" ליד כל תוצאה; מטריצת הרמות מושפעת ישירות מההנחה.
**איך מקטינים את אי-הוודאות:** מקדם `a` בכיול (5.6) אומד את סכום ההיסט+שגיאת ה-EIRP בפועל לכל מפעיל וסוג אתר. אחרי כיול, ההשוואה עוברת ל"מכוילת" עם רווח סמך — ורק אז מותר להציג מסקנות על דיוק המודל.

**בעיות נוספות שיש לשקף בממשק:**
- **אותם צבעים, ספים שונים:** הרמות הקיימות במנוע הן על RSSI (`excellent > -70 …`, TECHNICAL.md); הרמות בחוזה על RSRP. מקראות נפרדים (v2). בהשוואה משווים **dB לאחר המרה**, ומטריצת הבלבול ברמות החוזה על ה-RSRP-המומר.
- **אתר יחיד מול שרת מיטבי:** משווים רק דגימות של המפעיל של האתר (`sites.cell_site_id → cell_sites.operator` ↔ `field_operator_map`). מצב מקורב (ברירת מחדל): כל דגימות המפעיל בתחום ה-bbox — צפויה הטיה חיובית (נמדד > חזוי), מוסברת בממשק. מצב מדויק (M4): רק דגימות משויכות.
- **טכנולוגיה:** משווים רק `meas_tech = LTE` (כולל עוגן NSA) — המנוע לא מבחין ב-SS-RSRP ו-NR דורש היסט אחר (רוחב/מרווח תת-נושא שונים). השוואת NR — לא ב-M4.
- **מיושנות ו-coarse:** רק דגימות עם `gx/gy` (לא coarse) וללא `stale_cell`.
- **תדר:** רק דגימות שה-`band` שלהן ממופה לתדר האתר (טבלה קטנה B3→1800, B7→2600, B28→700, B1→2100 — **לאמת**).

**פלט** (`/compare/coverage/{result_id}`): `{status: "indicative"|"calibrated", n, n_devices, bias_db, mae_db, rmse_db, pearson_r, tier_confusion[5][5], tier_exact_pct, tier_within1_pct, residual_grid:{fields,rows}, by_distance:[…], assumptions:{bw_mhz, conversion_db, conversion_basis_he, matching:"approx"|"attributed"}, warnings_he:[…]}`.

### 5.6 כיול מודל ההפצה והתאמת תא-לאתר
**הבעיה:** `cell_sites` (ממשלתי) **ללא Cell ID** (USER_GUIDE "מה אין במאגר"; גם מסמך הפרויקט `cell-sites-data-source.md` מאשר: "חסר: אזימוט, גובה, תדרים, הספק, Cell ID"). לכן כל שיוך "התא המשרת = אתר X" הוא **הסקה**.

**גישות מוצעות (משולבות, לפי סדר):**
1. **קיבוץ לפי צומת:** כל התאים של אותו eNB (`node_id = ECI >> 8`) יושבים פיזית באותו אתר → מקבצים דגימות לפי `(operator_key, node_id)` ולא לפי תא. ב-NR אורך ה-gNB-ID משתנה (22–32 ביט) ואינו ידוע מהמכשיר — **[פתוח]**; ב-NR מקבצים לפי `(operator_key, nci)` בלבד או לפי PCI+ARFCN מקומי.
2. **אמידת מיקום הצומת:** ממוצע משוקלל של מיקומי הדגימות בעלות ה-RSRP הגבוה ביותר (העשירון העליון), ותיקון לפי **Timing Advance**: ב-LTE יחידת TA ≈ 78 מ' (מרחק חד-כיווני), כך שכל דגימה עם TA מגדירה טבעת; חיתוך טבעות מכמה מיקומים → אמידה. דורש ≥ 30 דגימות מ-≥ 3 כיוונים.
3. **שיוך לאתר:** האתר של אותו מפעיל (לפי `field_operator_map`) הקרוב ביותר לאומדן; מתקבל `auto` רק אם המרחק < R (300 מ' עירוני / 1 ק"מ פתוח) **וגם** האתר השני הקרוב רחוק פי 2 לפחות. אחרת "לא חד-משמעי".
4. **אישור ידני** ע"י A/O (`PUT /cell-site-map`, `source=manual`) — דומה לעריכת גובה ידנית הקיימת.

**מגבלות שיש לומר בגלוי:** אתרים מסווגים/ביטחוניים לא במאגר (USER_GUIDE) → תאים שישויכו בטעות לאתר שכן; אתרים זעירים פנימיים (1,597 לפי מסמך הפרויקט); PHI משותף להוט ופרטנר — אותו אתר, שני MCC-MNC; בערים אתרים במרחק 100–200 מ' — שגיאת ה-GPS ושגיאת האומדן באותו סדר גודל; NR_NSA מדווח לעתים את עוגן ה-LTE; אזימוט סקטורים לא ידוע.

**הכיול עצמו (פלט דוח, לא שינוי אוטומטי):** על דגימות משויכות (`confidence ≥ 0.7`), שארית `r = RSRP_meas − RSRP_pred_converted`. מתאימים `r = a + b·log10(d_km) + ε` לכל `(operator, site_type)`:
- `a` ≈ שגיאת EIRP/היסט ההמרה (מוצע כתיקון ל-`default_tx_power_dbm` בטבלת `cell_site_type_defaults` הקיימת — **המלצה למנהל, לא עדכון אוטומטי**).
- `b` ≈ שגיאת שיפוע (מעיד על בעיה ב-clutter/גובה האנטנה המשוער).
- מדווחים גם `σ(ε)` (shadow fading אמפירי) ורווחי סמך (bootstrap לפי מכשיר — דגימות של אותו מכשיר מתואמות, לא בלתי תלויות).
- תנאי סף לפרסום המלצה: ≥ 500 דגימות משויכות מ-≥ 3 מכשירים ו-≥ 10 אתרים בקבוצה.

---

## 6. חיזוי — מדורג וזהיר

### 6.1 מה לא מבטיחים לפני שיש נתונים
- לא מבטיחים "חיזוי קליטה לכל נקודה בארץ". חיזוי מוצג **רק** בתאים/מסלולים עם כיסוי מדידות; בשאר — "אין מספיק נתונים" (מצב UI מפורש, לא ריק ולא אפור שנראה כמו "אין כיסוי").
- לא מבטיחים חיזוי **זמני** של RSRP (לפי שעה). RSRP נקבע בעיקר מגאומטריה; תלות בשעה (עומס) מופיעה ב-RSRQ/SINR/throughput. רכיב "שעה-בשבוע" יוצג רק אם נמצא מובהק בתיקוף.
- לא מבטיחים ML. אין מודל ML עד שעובר את שער הנתונים (6.5) **וגם** משפר את ה-baseline בתיקוף.
- לא מבטיחים דיוק מספרי מראש. מספרים כמו "MAE 6 dB" יופיעו רק כתוצאת backtest אמיתי.

### 6.2 שלב P0 — סטטיסטיקה לכל תא (M5)
- לכל `(gx, gy, operator_key, tech)` מ-`field_agg_daily` על 90 הימים האחרונים: `pred_p50` (מההיסטוגרמה), רווח 80% (`P10..P90` אמפירי), `p_tier_ge_medium` = חלק הזמן ברמה ≥ בינוני.
- **ביטחון** (כללים שקופים, לא "ציון קסם"):
  - `insufficient`: `n < 10` או `n_days < 2`.
  - `low`: `n < 30` או `n_devices < 2` או `n_days < 3`.
  - `medium`: אחרת, אם `P90 − P10 > 20 dB`.
  - `high`: `n ≥ 100`, `n_devices ≥ 3`, `n_days ≥ 7`, ורווח ≤ 20 dB.
- מסלול (`/predict/route`): פילוח ל-100 מ', ערך לכל קטע, `% קטעים לא ידועים`, ו"קטע חלש צפוי" = `p_tier_ge_medium < 0.5` עם ביטחון ≥ medium.

### 6.3 שלב P1 — אינטרפולציה מרחבית (M5)
- מילוי תאים חסרים **בסמוך** למדידות: IDW / kriging רגיל על שאריות מהממוצע המקומי, רדיוס מקסימלי 300 מ' (מעבר — `insufficient`). שונות kriging → רווח.
- **P1b (רק אם 5.5 זמין):** regression-kriging — מגמה = חיזוי המנוע המומר (+ היסט מכויל `a`), אינטרפולציה של השאריות. זה המקום היחיד שבו המנוע הפיזיקלי משמש לחיזוי בתאים לא מדודים — ותמיד עם תווית "מבוסס מודל + כיול".

### 6.4 תיקוף
- **חלוקה מרחבית בבלוקים** (spatial block CV): בלוקים של 1×1 ק"מ, 5 קיפולים — כדי שלא "ננחש" תא משכנו הצמוד (דליפה מרחבית שמנפחת דיוק).
- **חלוקה בזמן:** אימון על שבועות 1..N, בדיקה על N+1 (מחקה שימוש אמיתי).
- **מדדים:** MAE, RMSE, bias (dB); דיוק רמה מדויק ו-±1 רמה; **כיול הרווח** — כמה מהמדידות נפלו בתוך רווח ה-80% (יעד 75–85%); Brier score ל-`p_tier_ge_medium`.
- **בסיסי השוואה חובה:** (א) חציון גלובלי לכל מפעיל+tech, (ב) חיזוי המנוע המומר ללא כיול. מודל שלא מנצח את (א) לא מוצג.
- התוצאות נשמרות (טבלה קטנה `field_predict_runs` — `id, stage, params JSON, metrics JSON, created_at`) ומוצגות בטאב החיזוי.

### 6.5 שער נתונים ל-ML (M6, מותנה)
הצעה לשער (לאשר ע"י בעל המוצר): ≥ 60 יום נתונים, ≥ 500K דגימות עם מיקום ≤ 50 מ', ≥ 20 מכשירים, ≥ 2,000 תאי גריד עם `n ≥ 30` לכל מפעיל מרכזי. מודל מועמד: gradient boosting על תכונות (מרחק/זווית לאתר משויך, גובה DEM, חיזוי המנוע, צפיפות אתרים, שעה), עם אותו פרוטוקול תיקוף. מתקבל רק אם משפר MAE ב-≥ 1.5 dB על P1 ב-block CV **ושומר** על כיול הרווח.

### 6.6 בריאות מכשיר
מוצדק כבר ב-MVP+ כי הנתונים זמינים מה-heartbeat והתועלת התפעולית ברורה (מכשיר שעומד להיכבות = חור במדידה).
- **קצב פריקה:** רגרסיה לינארית של `battery_pct` על heartbeats ב-60 הדק' האחרונות **שבהן** `is_charging = false` (≥ 6 נקודות). `eta_empty_min = battery_pct / drain` אם `drain > 0.5%/ש'`. ללא מספיק נקודות → "לא ידוע".
- **סיכון ניתוק** — כללים שקופים (לא ML; אין תוויות היסטוריות עדיין): `high` אם `eta_empty < 60`, או `degraded_reasons` כולל `background_location_missing` / `location_permission_missing` / `repeated_gaps` (ORG); `medium` אם `battery_optimization_not_exempt` (ORG), `queue_size` גדל ב-3 heartbeats רצופים, `queue_evicting` / עלייה ב-`evicted_samples_total` (v2), או `app_version` ישנה מהמינימום; מוצגים `reasons[]` עם התוויות של צוות האנדרואיד. הכללים נשענים על `degraded_reasons` של המכשיר (v2) ולא על גזירה עצמאית מהרשאות.
- ML לסיכון ניתוק — רק אחרי שנאסף יומן ניתוקים מסומן (≥ 3 חודשים).

---

## 7. התראות

### 7.1 סוגים וספים (ברירות מחדל, ניתנות לעריכה ע"י ADMIN) — לפי v2
| type | תנאי פתיחה | תנאי סגירה (היסטרזיס) | חומרה | הערכה |
|---|---|---|---|---|
| `device_offline` | **רק** כש-`last_monitoring_state ∈ {active, degraded}` ו-`hb_age > 15 דק'` (v2) | heartbeat חדש, או מעבר ל-`paused_*` | warning (ORG) / info (PERSONAL) | משימה כל דקה |
| `no_contact` | `last_monitoring_state ∈ {paused_*}` ו-`hb_age > 6 ש'` (v2) | heartbeat חדש | **info** (v2: "התראת מידע, לא קריטית") | משימה כל דקה |
| `dead_zone_entered` | 3 דגימות רצופות **לא-stale** של המכשיר ברמה 0 בתוך ≥ 60 ש' | 3 דגימות רצופות לא-stale ברמה ≥ 1 | warning | בקליטה |
| `sustained_weak` | `dt`-משוקלל ≥ 80% ברמה ≤ 1 במשך ≥ 10 דק' (לא-stale בלבד) | ≥ 50% ברמה ≥ 2 במשך 5 דק' | warning | בקליטה |
| `low_battery` | `battery_pct ≤ low_battery_threshold_pct` ו-`is_charging = false` | `≥ threshold + 5` או טעינה | warning; ≤ 5% critical | heartbeat |
| `monitoring_degraded` | `monitoring_state = degraded`. חומרה לפי הקוד ב-`degraded_reasons`: **critical** ל-ORG על `location_permission_missing`, `location_approximate_only`, `location_services_off`, `background_location_missing`, `phone_state_missing`; **warning** לשאר; ב-PERSONAL הכל **info** | המצב ≠ degraded | לפי העמודה הקודמת | heartbeat |
| `monitoring_paused` | `paused_by_user` ב-**ORG** בלבד (המקור: "עצירה … תמיד מדווחת"). ב-PERSONAL **לא מתריעים** | חזרה ל-`active/degraded` | warning (ORG) | heartbeat |
| `data_loss` | עלייה ב-`evicted_samples_total` (v2) | אין עלייה 60 דק' | warning | heartbeat |
| `config_pending` | `last_applied_config_version < config_version` > 30 דק' כשהמכשיר `active|degraded` | שוויון | info | משימה |
| `command_expired` | פקודה עברה ל-`expired` | — (נסגרת מיד, ליומן ולמגירה) | info | משימה |
| `new_dead_zone_cluster` | אשכול חדש (5.4) עם severity `critical` | האשכול נעלם 7 ימים | info | משימה שעתית |
| `ingest_stalled` (מערכת) | אין דגימות מאף מכשיר 30 דק' כשיש ≥ 3 מכשירים `active` | דגימה | critical | משימה |

הוסר מהגרסה הקודמת: `permission_degraded` שנגזר בשרת מההרשאות — הוחלף ב-`monitoring_degraded` המבוסס על `degraded_reasons` (v2).

### 7.2 Dedup והיסטרזיס
- `dedup_key` + אינדקס ייחודי חלקי (2.2) → התראה פתוחה אחת; הישנות מעלה `occurrences` ו-`last_seen_at`.
- **cooldown:** התראה שנסגרה לא נפתחת מחדש באותו מפתח במשך 10 דק' (מונע "ריצוד" סביב סף); אם נפתחת שוב — `occurrences` של חדשה, וקישור `details.previous_id`.
- ספים כפולים (פתיחה ≠ סגירה) כמפורט בטבלה.
- `acked` = נראתה ע"י מפעיל; נסגרת אוטומטית ב-resolve של התנאי. `resolve` ידני אפשרי (סוגר גם אם התנאי עדיין מתקיים — ואז היא תיפתח מחדש אחרי ה-cooldown; מוצג בממשק).

### 7.3 היכן מופיעות
- תג מונה התראות פתוחות על כפתור "קליטה בשטח" ב-`TopBar` (A,O).
- טאב "התראות" ב-`/field` + תצוגה מקדימה בסקירה.
- שורת מכשיר בטבלה (עמודת התראות) ובמגירת פרטי המכשיר.
- סמן מכשיר במפה — טבעת מהבהבת (אנימציית `pulse` הקיימת ב-`index.css`) להתראה critical.
- `no_contact`, `config_pending`, `command_expired` — info: בטאב ההתראות ובמגירה בלבד, לא בתג ה-TopBar (התג סופר warning+critical).
- VIEWER לא רואה התראות (מכילות זהות מכשיר).

---

## 8. Frontend

### 8.1 ניתוב וכניסה
- `main.tsx`: `<Route path="/field" element={<PrivateRoute><FieldPage /></PrivateRoute>} />` — לכל התפקידים (התוכן משתנה לפי תפקיד). מומלץ `React.lazy` ל-`FieldPage` (לא קיים במאגר כיום — הצעה, כדי לא להגדיל את ה-bundle של המפה).
- `TopBar.tsx`: כפתור חדש `📶 קליטה בשטח` לכל משתמש מחובר, לפני כפתור ניהול המשתמשים; ב-`/field` הוא מסומן פעיל ומופיע כפתור `🗺 מפת כיסוי` לחזרה ל-`/`. מחלקה חדשה `.navBtn`/`.navBtnActive` ב-`TopBar.module.css` בסגנון `.adminBtn` אך בצבע `--accent`. תג התראות (`.alertBadge`) ל-A,O, מתוך `useQuery(["field","alerts","open-count"])` עם `refetchInterval: 30000`.
- `Sidebar`/שכבות: **לא** מוסיפים טאב לסרגל הצד (הוא מוקדש לכלי ניתוח). במקום זה שכבת מערכת חדשה בלשונית "🗂 שכבות":
  - `useStore.ts`: `LayerType` מקבל `"field"`; `SYSTEM_LAYERS` מקבל `{ id: "fieldheat", name: "מדידות שטח – מפת חום", type: "field", visible: false }`.
  - `LayersPanel.tsx`: `LAYER_ICONS.field = "📶"`, `LAYER_DESCS.fieldheat`, וכפתור ⓘ שפותח `FieldHeatInfo` (בתבנית `CellSitesInfo`): מפעיל, tech, חלון זמן, מדד (P50/רמה/OOS), מקרא רמות **עם ספי RSRP כתובים** (להבדיל ממקרא ה-RSSI של ניתוחי הכיסוי — v2), וקישור "פתח דאשבורד שטח".
  - `MapView.tsx` מרנדר את השכבה (**[לא אומת]** — הקובץ לא זמין; ההנחה שקיים שם מנגנון רינדור לפי `mapLayers`).

### 8.2 פריסת הדף
```
TopBar (קיים + כניסה חדשה)
FieldHeader: כותרת "קליטה בשטח" · חותמת טריות ("עודכן לפני 12 ש'") · סרגל פילטרים בשורה אחת:
             חלון זמן (שעה/24ש'/7י'/30י'/מותאם) · קבוצה · מפעיל · טכנולוגיה
FieldTabs:   סקירה | מפה חיה | מכשירים | ניתוח | חיזוי | התראות (n) | ניהול
Content      (לפי טאב; מגירת פרטי מכשיר נפתחת בצד ה-end = שמאל ב-RTL)
```
- הטאב הפעיל ב-URL: `/field?tab=devices&device=<id>` (`useSearchParams`) — קישורים ניתנים לשיתוף ו-back עובד.
- פילטרים נשמרים ב-store (8.4) ומוחלים על כל הטאבים.

### 8.3 תצוגות
| תצוגה | תוכן | תפקידים |
|---|---|---|
| **סקירה** | אריחי KPI: מכשירים מקוונים / מכשירים שאמורים לנטר (`active|degraded`; מושהים מוצגים בנפרד), % זמן ≥ בינוני (+ "לא מסווג" בנפרד), RSRP P50/P5 **LTE (כולל עוגן NSA)**, SS-RSRP P50 **NR** באריח נפרד, דקות ללא שירות, שינויי תא/ש', התראות פתוחות; מפה מוקטנת; 5 התראות אחרונות; התפלגות רמות | V: אריחים מצרפיים בלבד (ללא "מכשירים מקוונים" לפי קבוצה, ללא התראות) |
| **מפה חיה** | react-leaflet; סמני מכשיר עם צבע רמה **+ גליף פסי קליטה (0–4) + תווית רמה בטולטיפ**; טבעת לפי סטטוס (מקווקו = לא מקוון; מושהים לא מוצגים); מכשיר coarse — עיגול ~1 ק"מ חצי-שקוף עם תווית "מיקום משוער"; סמן עם `tier_stale` — אפור עם "נתוני תא ישנים"; אשכול סמנים בזום נמוך; שכבת מפת חום (מלבני גריד, שקיפות 0.45) ניתנת להפעלה; פוליגוני אזורים מתים; מקרא; לחיצה על סמן → מגירה | V: מפת חום + אזורים מתים בלבד, ללא סמנים |
| **מכשירים** | טבלה: שם, ORG/PERSONAL, סטטוס (לפי 3.5: "מושהה · נראה לאחרונה לפני X", "ללא קשר · מושהה מאז …", "ניטור מוגבל" + סיבה ראשונה), רמה (כולל "לא מסווג" עם גליף ריק), RSRP, מפעיל, סוג רשת ("5G NSA · עוגן LTE"), סוללה, נראה לאחרונה, גרסה, התראות, פקודות פתוחות. מיון, חיפוש, סינון סטטוס. בטלפון — כרטיסים | A,O |
| **מגירת מכשיר** | כותרת (שם, תג בעלות, סטטוס); ב-NSA: **"רמה לפי עוגן LTE"** ליד תגית הרמה, ושורה נפרדת "רגל NR: SS-RSRP ‎−97 dBm · n78" (מ-`nr_leg`, ללא רמה); תא נוכחי (PCI/band/ECI/TA — של העוגן); `effective_location_mode`; `degraded_reasons` כרשימה עם תוויות; הרשאות כצ'יפים (`location` כ-מדויק/משוער/אין); תצורה "v18 · הוחלה v17 (ממתינה)"; סוללה + ETA; **גרף RSRP LTE לאורך זמן** (סדרה אחת למדד אחד; SS-RSRP של רגל ה-NR — מתג להצגת גרף נפרד, לא על אותו ציר) עם רצועות רמה, צירים מתויגים, פערי OOS/no-data/stale מסומנים; **פקודות:** כפתורי "דגימה עכשיו" / "העלאה עכשיו" (A,O) + רשימת 5 פקודות אחרונות עם סטטוס (נשלחה · ממתינה (עד ~5 דק') / בוצעה / דולגה — המכשיר מושהה / פג תוקף) ומי הנפיק; ב-PERSONAL מושהה הכפתורים מושבתים עם הסבר "מכשיר אישי מושהה — פקודות לא מבוצעות"; ב-ORG מושהה — אזהרה "ייתכן שתדולג"; כפתור "הצג מסלול" עם הודעה "צפייה במסלול נרשמת ביומן הפעילות"; עריכת תצורה (A) | A,O |
| **ניתוח** | התפלגות רמות לפי מפעיל (עמודות מוערמות אופקיות, % זמן, עם תוויות); טבלת השוואת מפעילים (P5, P50, % ≥ בינוני, OOS דק', שינויי תא/ש', n) + מתג "השוואה הוגנת (תאים משותפים)"; סדרה זמנית יומית; נמדד-מול-חזוי: בחירת תוצאת כיסוי → פיזור (חזוי מומר × נמדד, קו y=x), bias/MAE/RMSE, מטריצת בלבול, אזהרות הנחות | V: הכל מצרפי; פיזור כגריד צפיפות ולא נקודות |
| **חיזוי** | באנר שלב ("P0 — סטטיסטיקה היסטורית, לא מודל ML"); מפת תאים עם ערך וביטחון (מרקם קווים = נמוך, ללא מילוי + "אין מספיק נתונים"); חיזוי מסלול (ציור/בחירת מסלול → פילוח); כרטיס תיקוף (מדדים + בסיס השוואה); בריאות מכשירים (A,O) | V: ללא בריאות מכשירים |
| **התראות** | רשימה עם חומרה (אייקון+טקסט), סוג, מכשיר/אזור, זמן פתיחה, משך, `×n`, סטטוס; פעולות אישור/סגירה; סינון | A,O |
| **ניהול** | קבוצות + טופס תצורה (שדות החוזה עם טווחים ויחידות); קודי צירוף (רשימה, יצירה, ביטול); דיאלוג צירוף: קוד גדול לקריאה, QR, תוקף, קבוצה, בעלות, "הקוד יוצג פעם אחת"; יומן ביקורת שטח (A) | A (O קורא קבוצות) |

### 8.4 מצבים
- **ריק:** אין מכשירים → "עדיין לא צורפו מכשירים" + (A) כפתור "צור קוד צירוף"; (O,V) "פנה למנהל המערכת".
- **טעינה:** שלדים (skeleton) באריחים ובטבלה; ספינר קיים (`@keyframes spin`).
- **שגיאה:** הודעה עם `apiErrorText()` + "נסה שוב" (`refetch`).
- **ללא הרשאה:** VIEWER שמגיע ל-`?tab=devices` → "👁 תצוגה מצרפית בלבד — פרטי מכשירים זמינים למנהל ולמפעיל" (בנוסח של `READ_ONLY_NOTE`), והטאבים האסורים מוסתרים מראש.
- **נתונים לא טריים:** אם `freshness.last_sample_received_at` > 15 דק' → באנר צהוב "לא התקבלו מדידות מאז HH:MM".
- **אין מספיק נתונים:** בחיזוי ובהשוואות — מצב מפורש עם מה חסר ("נדרשות ≥ 10 דגימות מ-2 ימים").
- **k-אנונימיות:** ל-V — "N תאים הוסתרו (פחות מ-3 מכשירים)".
- **סטטוסים לפי v2:** "מושהה ע"י המשתמש · נראה לאחרונה לפני 8 דק'" / "מחוץ למשמרת · נראה לאחרונה …" (אפור, לא אדום); "ללא קשר · מושהה מאז 03:10 (7 ש')" (אפור-כחול, אייקון ⓘ); "לא מקוון" (אדום) רק למכשיר שאמור לנטר; "ההסכמה בוטלה" (מחוק בקו, ללא פעולות).
- **"לא מסווג":** תגית אפורה עם גליף פסים ריק וטקסט "לא מסווג (3G)" / "לא מסווג (מצב טיסה)" — לא "שולי".
- **מיושן:** "נתוני תא ישנים (32 ש')" ליד הרמה כש-`tier_stale`.

### 8.5 רכיבים, store ו-API
- תיקייה `frontend/src/components/field/`: `FieldHeader`, `FieldFilterBar`, `FieldTabs`, `KpiTile`, `TierBadge` (נקודה + פסים + טקסט), `TierLegend`, `FleetMap`, `DeviceMarker`, `HeatGridLayer` (canvas renderer של Leaflet — אלפי מלבנים), `DeadZoneLayer`, `TrackLayer`, `DeviceTable`, `DeviceDrawer`, `TimeSeriesChart`, `TierDistributionChart`, `OperatorCompareTable`, `MeasuredVsPredicted`, `PredictionView`, `RoutePredict`, `DeviceHealthList`, `AlertsList`, `GroupConfigForm`, `EnrollDialog`, `EnrollmentCodesTable`, `FieldEmptyState`. כל רכיב עם `X.module.css` משלו (מוסכמת CSS Modules).
- דף: `pages/FieldPage.tsx` + `FieldPage.module.css`.
- `lib/fieldTiers.ts` — `classifyTier`, `TIERS` (`{key, label_he, min_dbm, token, bars}`), `tierColorVar`. `lib/fieldFormat.ts` — זמן יחסי בעברית, dBm, אחוזים. `lib/fieldStatus.ts` — סטטוס חי (3.5) לתווית/צבע/אייקון, ומיפוי `degraded_reasons` → תווית עברית (מהטבלה בסעיף 5.6 באפיון האנדרואיד; קוד לא מוכר → "סיבה לא מוכרת: <code>"). רכיב חדש: `components/field/DeviceCommands` (כפתורים + רשימת סטטוס).
- `lib/roles.ts`: `canSeeDevices = canWrite` (A,O), `isAdmin`.
- **גרפים — הוחלט: SVG ידני, ללא ספרייה.** התלויות בפרונטאנד: react, react-dom, react-router-dom, leaflet, react-leaflet, zustand, axios, @tanstack/react-query — אין ספריית גרפים. הגרפים הנדרשים מעטים ופשוטים (ציר זמן עם רצועות רמה ופערים, עמודות מוערמות אופקיות, קווים לכמה מפעילים, פיזור, מטריצה), וכולם כבר שורטטו ידנית במוקאפ. רכיבים: `charts/TimeSeries`, `charts/StackedBars`, `charts/MultiLine`, `charts/Scatter`, `charts/Matrix` + `charts/scale.ts` (סקאלות לינאריות ותוויות צירים, ~60 שורות). יתרונות: אפס תלות, שליטה ב-RTL (הגרפים `direction:ltr` בתוך דף RTL), טוקנים ישירים, גליף פסים. חיסרון: tooltip/נגישות נכתבים ידנית (`<title>` + תצוגת טבלה). **אין תלות חדשה.**
- **QR — אין ספרייה בשום צד** (גם לא ב-backend: אין `qrcode`/`segno` ברשימה). אפשרויות:
  1. **`segno` ב-backend (מומלץ) — תלות חדשה, דורשת אישור.** Python טהור, ללא תלויות, רישיון BSD. ה-SVG מוחזר בתשובת `POST /enrollment-codes` בלבד (הקוד לא נשמר בטקסט גלוי, ולכן אי אפשר להפיק QR אחר כך).
  2. ספריית npm `qrcode` בפרונטאנד — תלות חדשה גם היא; הקוד בטקסט גלוי ממילא נמצא בזיכרון הדף ברגע ההצגה.
  3. ללא QR ב-MVP: הדיאלוג מציג את הקוד ואת כתובת השרת להקלדה ידנית — עובד בלי תלות, אבל מסורבל בשטח.
  4. מקודד QR שנכתב ידנית — **לא מומלץ** (Reed-Solomon, מסכות; סיכון באגים שקטים).
  **ברירת המחדל עד אישור: אפשרות 3**, עם מקום מוכן ל-QR בדיאלוג; עם אישור — אפשרות 1.
- תלויות נוספות שיידרשו בהמשך (לא ב-MVP, כולן לאישור): ספריית ML (למשל scikit-learn) ב-M6; `locust` לבדיקות עומס (חלופה ללא תלות: סקריפט `httpx` אסינכרוני — אם httpx אכן מותקן כ-dev). numpy (קיים) מספיק ל-IDW, kriging בסיסי, אחוזונים והסימולטור.
- **QR:** נוצר בשרת כ-SVG בתשובת `POST /enrollment-codes` (ספריית Python `segno` — תלות חדשה **לאישור**), כי הקוד לא נשמר בטקסט גלוי ולכן אי אפשר לייצר QR מאוחר יותר.
- **store** (`useStore.ts`) — slice חדש:
  ```
  fieldFilters: { window: "1h"|"24h"|"7d"|"30d"|"custom"; from?: string; to?: string;
                  groupId: number|null; operators: string[]; techs: string[] }
  setFieldFilters(p: Partial<...>)
  fieldHeatOpts: { metric: "p50"|"tier"|"oos_pct"; operator: string|null; tech: "LTE"|"NR"|null; window: "24h"|"7d"|"30d" }
  setFieldHeatOpts(...)
  ```
  נוספים ל-`partialize` ול-`merge` עם בדיקת טיפוסים (כמו `cellSiteFilter`), ומאופסים ב-`logout`. בחירת מכשיר ומצב מגירה **לא** ב-store — ב-URL.
- **api.ts** — מקטע `// ── Field reception ──` עם טיפוסים (`FieldDevice`, `FieldDeviceRow`, `FieldSummary`, `HeatCell`, `FieldAlert`, `EnrollmentCode`, `FieldGroup`, `CompareResult`, `PredictCell`, …) ו-`fieldApi = { summary, devices, live, device, track, timeseries, updateDevice, revoke, remove, heatmap, timeseriesAgg, breakdown, deadZones, compare, calibration, predictCells, predictRoute, predictValidation, deviceHealth, issueCommand, deviceCommands, cancelCommand, alerts, ackAlert, resolveAlert, alertRules, setAlertRule, enrollmentCodes, createEnrollmentCode, revokeEnrollmentCode, groups, saveGroup, deleteGroup, exportMeasurements, exportHeatmap, audit }`. רשימות `{fields, rows}` ממופות לאובייקטים כמו `cellSitesApi.list`; הורדות דרך `downloadAuthed` הקיים.
- **מפתחות react-query:** `["field", <resource>, filters]`; mutations עושות `invalidateQueries(["field"])` ממוקד.

### 8.6 RTL ונגישות
- כל רמה: צבע + פסים + טקסט ("טוב · ‎-92 dBm"). ערכי dBm עטופים ב-`<bdi>` (המערכת כבר עושה זאת ב-`LayersPanel`) כדי שהמינוס לא יקפוץ.
- **ממצא:** בבדיקה אלגוריתמית בצבעי החוזה על רקע `--panel`, ‏`--cov-excellent` ו-`--cov-good` כמעט זהים (ΔE ≈ 4.8 בראייה רגילה, ≈ 3.2 בעיוורון צבעים אדום-ירוק). כלומר, בין שתי הרמות האלה **רק** הטקסט/הגליף מבדיל. לכן הגליף (מספר פסים) בסמנים, בתאי הגריד ובתגיות הרמה הוא חובה (v2).
- צבעי מפעילים: נעשה שימוש ב-`CELL_OPERATORS` הקיים לעקביות; כחול (פלאפון) וסגול (סלקום) כמעט זהים לעיוורי צבעים (בדיקה אלגוריתמית) → כל סדרה של מפעיל מתויגת בשמה ישירות, לעולם לא מקרא-צבע בלבד.
- טבלאות: `scope` בכותרות; גרפים: `role="img"` + `aria-label` + תצוגת טבלה חלופית.
- רוחב טלפון: אין גלילה אופקית לדף; טבלאות, המפה וגרפים רחבים בתוך wrapper עם גלילה פנימית (או כרטיסים).
- **ניגודיות טקסט משני:** `--text-dim` (#5a7a9a) על `--panel` נותן ≈ 3.9:1 — מתחת ל-4.5:1 הנדרש לטקסט קטן. במוקאפ הוגדר token נוסף `--muted-ink: #7f9bb8` (≈ 6.1:1) לטקסט משני קטן; `--text-dim` נשאר לקווים/אייקונים. זו תוספת token, לא שינוי של קיים — לאישור.
- במוקאפ יש מתג "מנהל/מפעיל ↔ צופה" — **להמחשת הבדלי התפקידים בלבד**; במוצר התפקיד נקבע מה-JWT ואין מתג כזה.

---

## 9. פרטיות ואבטחה

### 9.1 עקרונות מהחוזה, ואיך הם ממומשים
| דרישה בחוזה | מימוש |
|---|---|
| אין IMEI/IMSI/טלפון | הסכמה (pydantic) עם `extra="forbid"` על המעטפה והדגימה — שדה לא מוכר נדחה ב-422, כך ששדה מזהה לא יישמר "בטעות". **הערה:** זה גם אומר שכל שדה חדש באפליקציה מחייב עדכון חוזה (רצוי) |
| טוקן כ-hash, ניתן לביטול | 9.2 |
| VIEWER — אגרגציות בלבד | 9.4 |
| כל צפייה במסלול נרשמת | 9.3 |
| 90 יום גולמי; שעתי 400 יום; יומי ללא הגבלה (v2) | 2.5, 9.5 |
| withdraw ב-MVP (v2) | 3.3, 9.6 |
| PERSONAL: `consent_version`/`consented_at` חובה (v2) | 3.3, `field_devices` |
| PERSONAL: מיקום רק מחמיר (v2) | 2.4, 3.6 |
| מחיקת מכשיר מוחקת/מאנונימת | 9.6 |
| PERSONAL — מינימום איסוף, עצירה | השרת לא מתריע על עצירה ב-PERSONAL (7.1); אוכף `schedule` (3.6); לא שומר מיקום מדויק כשהמכשיר ב-coarse (הנתון לא נכנס לגריד 100 מ', 2.4) |

### 9.2 טוקנים וקודים
- טוקן מכשיר: 256 ביט, נשמר `sha256(FIELD_TOKEN_PEPPER + token)`; מוחזר פעם אחת ב-enroll. ביטול = `status='revoked'` (מיידי — אין cache).
- קוד צירוף: 10 תווים מ-alphabet ללא תווים מבלבלים (`23456789ABCDEFGHJKMNPQRSTUVWXYZ`, ~49 ביט) מוצג `XXXX-XXXX-XX`; hash בלבד ב-DB; תוקף ≤ 30 יום; חד-פעמי כברירת מחדל; ניסיונות כושלים מוגבלים לפי IP.
- `FIELD_TOKEN_PEPPER` חובה בפרודקשן — אם נשאר ערך ברירת מחדל, אזהרה באתחול (בדומה ל-`SECRET_KEY = "CHANGE-ME..."` ב-`config.py`, שכיום **אינו** נאכף — ממצא כללי).
- **תוכן ה-QR (v2):** `coverageops-enroll:v1?server=<FIELD_PUBLIC_BASE_URL>&code=<CODE>`. ה-QR מכיל את הקוד בטקסט גלוי — כמוהו כקוד; הדיאלוג מציין זאת.
- רישום תעבורה: לא לרשום ב-log גופי batch או כותרות Authorization.

### 9.3 יומן ביקורת
שימוש ב-`activity_log` הקיים דרך פונקציית עזר בסגנון `_log` של `api/auth.py` (שם היא פרטית למודול — נעביר עותק/נוציא ל-`app/core/audit.py`; לא משנים את `auth.py`).
| action | מתי | detail (JSON כמחרוזת) |
|---|---|---|
| `FIELD_TRACK_VIEW` | כל `GET /devices/{id}/track` מוצלח | `{device_id, from, to, points}` |
| `FIELD_EXPORT` | ייצוא ברמת מכשיר | `{scope, device_id?, group_id?, from, to, rows}` |
| `FIELD_DEVICE_ENROLLED` / `REVOKED` / `DELETED` / `ANONYMIZED` | | `{device_id, ownership}` |
| `FIELD_CONFIG_CHANGED` | קבוצה/override | `{group_id?/device_id?, changed_keys}` |
| `FIELD_CODE_CREATED` / `REVOKED` | | `{code_id, code_hint, max_uses, expires_at}` |
| `FIELD_CELLMAP_MANUAL` | | `{operator_key, node_id, cell_site_id}` |
| `FIELD_COMMAND_ISSUED` / `FIELD_COMMAND_CANCELLED` | הנפקה/ביטול פקודה (v2) — **מי** (`user_id`), למי, מה | `{command_id, device_id, type}` |
| `FIELD_DEVICE_WITHDRAWN` | withdraw מהמכשיר (v2); `user_id = null` | `{device_id, ownership, data_action}` |
| `FIELD_PURGE_DONE` | סיום מחיקה/אנונימיזציה | `{device_id, mode, rows}` |
`ip_address` מ-`request.client.host`. צפייה ביומן: `GET /api/field/audit` (ADMIN) + טבלה בטאב ניהול. **שמירת היומן:** ללא מחיקה אוטומטית (שאלה פתוחה — Q7).

### 9.4 VIEWER — "ללא זיהוי מכשיר/אדם"
- אין גישה ל-`/devices*`, `/alerts`, `/device-health`, `/export/measurements*`, `/audit` (403 בצד השרת — לא רק הסתרה ב-UI).
- אגרגציות מרחביות: תא מוצג ל-V רק אם `|∪ device_set| ≥ FIELD_VIEWER_K_MIN` (3) בחלון המבוקש; אחרת מושמט ונספר ב-`suppressed`. בלי זה, תא בכביש צדדי עם מכשיר יחיד בשעה מסוימת חושף מסלול אדם.
- רזולוציית זמן ל-V: מינימום יום (`bucket=1d`) במפה ובסדרות מרחביות (שעה ביום + מיקום = זיהוי).
- `n_devices` לא מוחזר ל-V.

### 9.5 משימת תחזוקה (retention + aggregation + partitions + alerts)
- `python -m app.scripts.field_maintenance [--task …]` (כמו `import_cell_sites`) מורץ ע"י cron של הקונטיינר/המארח: aggregation כל 5 דק', alerts כל דקה, retention + partitions + daily rollup פעם בלילה (02:30 Asia/Jerusalem).
- **נעילה:** ב-PG — `pg_try_advisory_lock(<קבוע לכל משימה>)` כדי ששתי הרצות לא ירוצו במקביל; ב-SQLite — לא רלוונטי.
- חלופה שנדחתה: scheduler בתוך תהליך ה-API — עם כמה workers ירוץ כפול, ונופל עם האפליקציה.
- **[לא אומת]** איך מוגדרים קונטיינרים/cron בפרודקשן (docker-compose.prod לא זמין). זה פריט G2 ב-Go/No-Go (11.4).
- משימה נוספת (v2): `purge` — מבצעת מחיקה/אנונימיזציה של מכשירים ב-`withdrawn`/`deleted` שטרם טופלו; רצה בכל ריצה של 5 דק'. `commands` — מעבירה פקודות שפג תוקפן ל-`expired`.

### 9.6 מחיקה ואנונימיזציה של מכשיר (כולל withdraw — v2)
שלושה נתיבים מפעילים את אותה פונקציה `field_privacy.purge_device(device, mode, actor)`:
1. `DELETE /api/field/devices/{id}?mode=delete|anonymize` — ADMIN (ברירת מחדל: `delete` ל-PERSONAL, לבחירה ל-ORG).
2. `POST /api/field/devices/me/withdraw` — המכשיר (v2). PERSONAL → `delete`; ORG → `anonymize` (12.2).
3. (לא ב-MVP) בקשת נושא מידע דרך ADMIN — אותו נתיב 1.

- **מיידי (בתוך הבקשה):** פסילת טוקן, סטטוס `withdrawn`/`deleted`, ביטול פקודות פתוחות, רישום ביומן.
- **`mode=delete`:** מחיקת `field_measurements`, `field_heartbeats`, `field_device_commands` והתראות של המכשיר (ברצועות של 10K, במשימת התחזוקה הבאה — ≤ 5 דק'); איפוס `label`, `model`, `device_uuid` (→ UUID אקראי), `consent_*`, `last_*`, `token_hash`. שורת המכשיר נשארת כ"מצבה" כדי שמפתחות זרים ביומן/אגרגציות לא יישברו. האגרגציות (`field_agg_*`) נשארות — ספירות; `device_set` מכיל מזהה פנימי שאינו מקושר יותר לאדם.
- **`mode=anonymize`:** הדגימות נשארות עד תום 90 יום, אבל `device_pk` → מכשיר-מצבה משותף "anonymous" (אחד לכל יום), `lat/lon` מעוגלים למרכז תא 100 מ', `ts` לשעה, `sample_id` → אקראי, `cell_id`/`neighbors` נשמרים (מזהי רשת), flag 8. **ספק משפטי** אם זו אנונימיזציה מספקת — Q4.
- `completes_by` בתשובה = זמן הריצה המתוכננת הבאה + 5 דק'. עד אז הנתונים לא מוצגים (סינון לפי `status`).
- בדיקה: אחרי ריצת התחזוקה אין שורות גולמיות למכשיר (delete) / אין שורה עם `device_pk` שלו (anonymize).

### 9.7 הערה משפטית
ניטור מיקום עובדים ומכשירים פרטיים בישראל כפוף לדיני הגנת הפרטיות ולדיני עבודה. זה **אינו** ייעוץ משפטי — מומלץ שהמסמך ומסכי ההסכמה יעברו ייעוץ משפטי לפני פריסה ל-PERSONAL (Q4).

---

## 10. תוכנית בדיקות

### 10.1 Backend — pytest (בסגנון `tests/test_cell_sites.py`)
- fixtures: `factory` (SQLite `StaticPool` + `create_all` + seed קבוצת ברירת מחדל ו-`field_operator_map`), `_client(factory, role)`, `admin`/`operator`/`viewer`; **`device(factory)`** — יוצר קוד צירוף, קורא ל-enroll האמיתי (ללא override), ומחזיר `TestClient` + כותרת `Authorization: Bearer fdt_…`. כך נבדק נתיב האימות האמיתי.
- `tests/test_field_tiers.py` — כל וקטורי `field_tier_vectors.json` (5.1) + מקטע `normalization`; בדיקת סכימה לקובץ (id ייחודי, expected ∈ {0..4,null}).
- `tests/test_field_ingest.py`:
  - enroll (v2): קוד תקף → 201 עם `ownership` של הקוד; לא קיים/בוטל → 400 `code_invalid`; פג → 410 `code_expired`; מוצה → 409 `code_used`; ownership שונה → 409 `ownership_mismatch`; PERSONAL בלי הסכמה → 422; `consented_at` נשמר; שני enroll מקבילים לקוד חד-פעמי → אחד מצליח (מונקיפאץ' כמו `test_promote_race_returns_existing`); ניסיון חוזר עם אותו קוד+uuid תוך 15 דק' → אותו `device_id`, טוקן חדש, `use_count` לא עלה.
  - batch: gzip ו-JSON רגיל; 501 דגימות → 413; גוף פתוח > תקרה → 413; `device_id` של מכשיר אחר → 403; JWT משתמש על batch → 401; טוקן מכשיר על `/api/sites/` → 401; שליחה כפולה → `duplicates == n`; batch חלקי → `rejected` עם reasons **רק מהרשימה הסגורה** (בדיקה שכל reason תואם ל-regex של הרשימה); אינווריאנט `accepted+duplicates+rejected == len`; ערך מחוץ לטווח → `out_of_range:<field>`; sentinel → null **ולא** דחייה; `cell_info_age_ms=10001` → נשמר, flag stale, לא באגרגציה ולא ב-KPI, `cell_info_age_ms=10000` → נכנס; coarse (`loc_accuracy_m=1000`, ‎0.01°) → `gx=NULL`, `gx25` מלא; `loc_age_ms > 30000` → אין גריד; NSA → `tier` לפי `rsrp_dbm` ו-`nr_ss_rsrp_dbm` נשמר בנפרד; `neighbors[].rat` לא מוכר → `invalid_enum:neighbors.rat`; `tier`, `gx/gy`, `operator_key` מחושבים; DB לא זמין ב-dependency → **503 ולא 401**; `last_*` מתעדכן רק מדגימה חדשה יותר; `config_version` בתשובה עולה אחרי שינוי קבוצה.
  - heartbeat (v2): עדכון `last_*` כולל `degraded_reasons`, `effective_location_mode`, `applied_config_version`; `permissions.location` בוליאני → 422; קוד degraded לא מוכר נשמר ולא נדחה; תשובה `{config_version, server_time, commands}`; פקודה ב-`queued` מוחזרת ומסומנת `delivered`; `acked_commands` בשתי הצורות (`[id]` ו-`[{id,status}]`), `skipped_paused` נשמר; מזהה של מכשיר אחר מתעלמים; פקודה שפג תוקפה לא מוחזרת.
  - withdraw: 200 + טוקן נפסל מיד (batch → 401); קריאה שנייה → 401; אחרי ריצת `purge` אין שורות גולמיות (PERSONAL) / אנונימיות (ORG); `FIELD_DEVICE_WITHDRAWN` נרשם.
  - commands API: A,O → 201, V → 403; כפילות → 409 `command_pending`; `FIELD_COMMAND_ISSUED` עם `user_id` המנפיק; ביטול `queued` בלבד.
  - config: ETag/304; מיזוג group+override.
  - rate limit: 31 בקשות בדקה → 429 עם `Retry-After`.
- `tests/test_field_api.py`: הרשאות לכל נתיב בטבלה 3.4 (מטריצה A/O/V — 403 במקומות הנכונים, בסגנון `test_viewer_cannot_edit_heights`); סטטוס חי (3.5) עם זמנים מוזרקים; heatmap — k-anonymity ל-V (2 מכשירים → מושמט, 3 → מוצג) ו-`n_devices` לא מוחזר ל-V; KPIs על dataset סינתטי עם תשובה ידועה (למשל 10 דק' OOS בדיוק); dead zones (אשכול ידוע); compare — רסטר סינתטי קבוע (‎-60 dBm RSSI) מול דגימות ‎-90 RSRP ו-`bw_mhz=10` → bias ≈ ‎-2.2 dB (‎-90 − (‎-60 − 27.8)).
- `tests/test_field_privacy.py`: `FIELD_TRACK_VIEW` נכתב לכל צפייה במסלול (ולא נכתב ב-403); ייצוא נרשם; מחיקה — אין דגימות, טוקן 401, אגרגציות קיימות; anonymize — עיגול ו-flag; retention מוחק > 90 יום ולא נוגע ב-89; שדה `imei` בדגימה → 422.
- `tests/test_field_alerts.py`: פתיחה/סגירה עם היסטרזיס; dedup (`occurrences`); cooldown; **מכשיר `paused_*` ללא heartbeat 3 ש' → אין `device_offline`, סטטוס `paused`; 6 ש' + דקה → `no_contact` info**; `active` ללא heartbeat 16 דק' → `device_offline`; PERSONAL paused → אין `monitoring_paused`; ORG paused → יש; `monitoring_degraded` עם חומרה לפי קוד; עלייה ב-`evicted_samples_total` → `data_loss`; דגימות stale לא מפעילות `dead_zone_entered`.
- `tests/test_field_agg.py`: אגרגציה אידמפוטנטית; נתון מאוחר מסמן dirty ומעדכן; אחוזון מהיסטוגרמה בטווח ±2 dB; **אף דלי לא מכיל גם LTE RSRP וגם SS-RSRP** (בדיקה על כל השורות); `n_stale_excluded` נכון; coarse נכנס רק ל-`field_agg_daily_coarse`.
- `tests/test_field_migrations.py`: `run_startup_migrations` פעמיים על SQLite; הטבלאות והאינדקסים קיימים; אינדקסי PG-only לא מורצים על SQLite.
- PostgreSQL: סוויטת אינטגרציה קטנה (מסומנת `@pytest.mark.pg`, רצה ב-CI עם קונטיינר PG) ל-partitioning, `ON CONFLICT`, advisory lock ו-DROP PARTITION — אלה לא נבדקים ב-SQLite.

### 10.2 עומס על הקליטה
- כלי: `locust` או סקריפט `httpx` אסינכרוני (`backend/scripts/field_loadtest.py`) שמשתמש בסימולטור.
- תרחישים: (א) יציב — 500 מכשירים, batch כל 60 ש' (≈ 8 req/s, ~22 דגימות לבקשה); (ב) ×5 שיא (40 req/s); (ג) "סערת חזרה" — 200 מכשירים שולחים תור offline של 10 batches × 500 דגימות בבת אחת; (ד) 30% כפולות.
- קריטריונים: (א) p95 < 150 ms, 0 שגיאות 5xx; (ב) p95 < 400 ms; (ג) הכל נקלט תוך 5 דק', אין deadlocks; (ד) ספירת השורות = ייחודיות. מדידת גודל טבלה ואינדקס בפועל כדי לתקף את 2.3.

### 10.3 סימולטור מכשירים (`app/scripts/field_simulator.py`)
- מסלולים: polylines מוטמעים (כביש 1 ת"א–ירושלים כולל שער הגיא, כביש 6 קטע מרכז, כביש 90 בערבה, נסיעה עירונית בחיפה) + מכשירים נייחים.
- שדה RSRP: לכל מפעיל — אתרים מ-`cell_sites` (או רשימה סינתטית בבדיקות), log-distance עם מקדם 3.5, **shadow fading לוג-נורמלי σ = 8 dB עם קורלציה מרחבית** (מודל Gudmundson, מרחק דה-קורלציה 50 מ'), בחירת שרת עם היסטרזיס 3 dB (מייצר cell_change ו-ping-pong), אזורי מת מוזרקים, NR רק בערים.
- התנהגות מכשיר: סוללה יורדת 4–8%/ש', טעינה ברכב, dual-SIM (אותו `ts`, `sample_id` נפרד), תור offline במנהרות, שליחות חוזרות (כפולות), הטיית שעון ±30 ש', heartbeat כל 5 דק' (ובמושהה כל 15 דק' עם השמטות), מעבר ל-`paused_schedule` בסוף משמרת, `degraded` אקראי עם קודים מהרשימה של האנדרואיד.
- שדות v2: `cell_info_age_ms` (רוב 0–3000, 10% מעל 10000 — "cell info קפוא"), `loc_age_ms`, NSA עם `nr_leg` בערים, `timing_advance_us` ב-NR, `altitude_msl_m`, `neighbors[].rat`; מכשירי PERSONAL ב-coarse (עיגול 0.01°, `loc_accuracy_m ≥ 1000`, שדות null); מכשיר אחד ששולח `out_of_range`; ack לפקודות (כולל `skipped_paused` כשמושהה); withdraw.
- פלט: ישירות ל-API (עם enroll) או לקובץ JSONL לבדיקות. Seed קבוע לשחזור.
- שימוש: פיתוח הדאשבורד לפני שהאפליקציה מוכנה, בדיקות עומס, וגם בדיקת **שחזור** פרמטרים: הסימולטור יודע את ה-EIRP ה"אמיתי" ← הכיול (5.6) חייב לשחזר אותו בתוך ±2 dB (בדיקת נכונות של צינור הכיול).

### 10.4 Frontend / E2E
- המשימה ציינה סוויטת Playwright קיימת — **היא לא זמינה; מבנה ה-fixtures וההתחברות לא אומתו**. (Playwright גם אינו ברשימת התלויות שנמסרה.) בדיקות מוצעות:
  - ניווט: כפתור "קליטה בשטח" ב-TopBar → `/field`; חזרה למפה.
  - תפקידים: VIEWER לא רואה טאבים מכשירים/התראות/ניהול; `?tab=devices` ישירות → הודעת ללא-הרשאה; שאילתת `/devices` לא נשלחת בכלל.
  - טבלה → מגירה → "הצג מסלול" → המסלול מוצג (ובצד השרת נרשם — נבדק ב-pytest).
  - צירוף: יצירת קוד → הקוד וה-QR מוצגים, סגירה ופתיחה מחדש → הקוד לא מוצג שוב.
  - שכבת "מדידות שטח" בעמוד המפה הראשי: הפעלה/כיבוי, מקרא RSRP.
  - מצבים: ריק / שגיאה (route mocking) / נתונים לא טריים.
  - סטטוסים (v2, route mocking): מכשיר מושהה מוצג "מושהה · נראה לאחרונה" ולא "לא מקוון"; אחרי 6 ש' "ללא קשר"; "לא מסווג" מוצג עם גליף ריק; NSA מוצג "עוגן LTE" + רגל NR בנפרד.
  - פקודות: "העלאה עכשיו" → רשומה "נשלחה · ממתינה"; לחיצה כפולה לא יוצרת שתיים (409 מוצג כהודעה); PERSONAL מושהה → כפתורים מושבתים; VIEWER לא רואה כפתורים.
  - רוחב 390px — `document.scrollingElement.scrollWidth <= innerWidth`.
- Unit — **אין test runner בפרונטאנד ברשימת התלויות**; Vitest תהיה תלות dev חדשה (לאישור). עד אז הבדיקות האלה רצות דרך Playwright (`page.evaluate` על המודול) או בצד השרת מול אותו קובץ וקטורים: `classifyTier` מול הוקטורים המשותפים, פורמט זמן יחסי, מיפוי `{fields, rows}`.

### 10.5 Definition of Done (לכל אבן דרך)
- הבדיקות הרלוונטיות ירוקות ב-SQLite **וגם** בסוויטת ה-PG.
- `docs/TECHNICAL.md` (טבלאות, API, משתני סביבה) ו-`docs/USER_GUIDE.md` (מסכים, תפקידים) מעודכנים — כמו שהמערכת מתעדת כל פיצ'ר קיים.
- מטריצת הרשאות נבדקה בצד שרת.
- אין שגיאות קונסול; RTL נבדק; 390px ללא גלילה אופקית.
- הסימולטור מריץ תרחיש מלא מול השרת.

---

## 11. אבני דרך, סיכונים ושאלות פתוחות

### 11.1 אבני דרך (ללא הערכות זמן)
| # | היקף | קריטריוני קבלה |
|---|---|---|
| **M0 — הקפאת חוזה** | חוזה v2 + הכרעה על הנקודות הפתוחות ב-12.2; `field_tier_vectors.json` v1 (5.1) עובר בשני הצדדים; סימולטור בסיסי (JSONL); **החלטת Go/No-Go לאירוח (11.4)** | שני הצוותים חתמו; G1–G3 בסטטוס Go |
| **M1 — קליטה וניהול** | מודלים, enroll/batch/heartbeat/config/**withdraw**, `get_current_device` (כולל 503-לא-401), rate limit, קבוצות ותצורה (v2), קודי צירוף, **תור פקודות** (`sample_now`, `upload_now`), retention בסיסי, יומן | כל בדיקות 10.1 לקליטה; עומס תרחיש (א); האפליקציה (או הסימולטור) מצטרפת, שולחת, מקבלת פקודה ומאשרת end-to-end; withdraw מוחק ≤ 5 דק' |
| **M2 — בקרה חיה** | `/field`: סקירה, מפה חיה (כולל coarse כעיגול), טבלה, מגירה + ציר זמן + מסלול (עם יומן) + **שליחת פקודה**, ניהול + QR, התראות v2 (offline/ללא קשר/סוללה/ניטור מוגבל/השהיה ב-ORG/אובדן נתונים), תג ב-TopBar | E2E של 10.4; offline נפתח ≤ 16 דק' אחרי heartbeat אחרון רק למכשיר פעיל; מושהה → "מושהה · נראה לאחרונה", ואחרי 6 ש' "ללא קשר" (info) |
| **M3 — ניתוח** | אגרגציות + dirty queue + סינון מיושנות, מפת חום, KPI (5.3) עם פיצול LTE/NR, השוואת מפעילים, אזורים מתים, ייצוא, k-אנונימיות ל-VIEWER, partitioning ב-PG | KPI תואמים ערכים ידועים מ-dataset סינתטי (כולל stale ו-coarse); אף KPI לא מערבב LTE ו-SS-RSRP (בדיקה); VIEWER לא מקבל תא עם < 3 מכשירים |
| **M4 — נמדד מול חזוי וכיול** | `/compare` (מסומן "אינדיקטיבי"), התאמת תא-לאתר, דוח כיול | הכיול משחזר EIRP של הסימולטור ±2 dB; כל תוצאה מציגה את ההנחות ואת "אינדיקטיבי עד כיול" |
| **M5 — חיזוי ובריאות** | P0 + P1 (+ P1b אם M4 בשל), תיקוף, טאב חיזוי, בריאות מכשיר | P0 מנצח את החציון הגלובלי ב-block CV, אחרת לא מוצג |
| **M6 — מותנה** | ML (אחרי שער 6.5; תלות חדשה לאישור), SSE, H3, פקודות `active_test_now`/`send_diagnostics`, החלפת טוקן | לפי שער הנתונים ו-v2 |

### 11.2 תלויות בצוות האנדרואיד
פורמט `acked_commands` (12.2), טבלת טווחים משותפת ל-`out_of_range` (3.3), סמנטיקה של `cell_info_age_ms = null`, ניסיון חוזר של enroll, שם קוד `consent_required`, עדכונים לרשימת `degraded_reasons` (כל קוד חדש = עדכון חוזה ותווית בדאשבורד), צריכת `field_tier_vectors.json`.

### 11.3 סיכונים
| סיכון | השפעה | מיתון |
|---|---|---|
| **אירוח (ראו 11.4 — פריט Go/No-Go)** | DB חינמי לא מכיל את נפח הגולמי; משימות תקופתיות לא רצות | החלטה לפני M1 |
| מגבלות אנדרואיד (throttling של cell info, Doze, יצרנים) | פערים, `cell_info_frozen`, `stale_cell` שכיח | סינון מיושנות (v2), מונה שקיפות, `degraded_reasons` |
| שיעור גבוה של `stale_cell` | KPI מבוססים על מעט נתונים | `stale_excluded_pct` מוצג; אם > 30% במכשיר — התראה ובדיקה מול צוות האנדרואיד |
| NCI/gNB-ID חסר או לא אמין ב-NR | התאמת תא-לאתר חלשה ב-5G | כיול ב-LTE תחילה |
| המרת RSSI→RSRP לא ידועה | השוואה מוטה ב-±6 dB ויותר | הנחה גלויה + "אינדיקטיבי עד כיול" (5.5) |
| 401 שגוי → האפליקציה מוחקת נתונים | אובדן נתונים בלתי הפיך | 503 בכל תקלה (3.2), בדיקה ייעודית |
| `cell_id` נשמר גם ב-coarse/off (v2) | שחזור מיקום משוער מ-cell_id | ראו 12.3 |
| rate limit בזיכרון עם כמה workers | הגבלה רופפת | Nginx `limit_req` |
| PostGIS/גרסת PG לא אומתו | partitioning/BRIN | בדיקה ב-M1; נפילה לטבלה רגילה + DELETE |
| פרטיות PERSONAL / יחסי עבודה | חשיפה משפטית | ייעוץ משפטי (Q4), k-אנונימיות, יומן, withdraw |
| הטיית מדגם | מסקנות שגויות על מפעילים | "השוואה הוגנת" |
| `cell_sites` מוחלפת בייבוא | שיוך ידני לאתר מתנתק | מפתח שחזור (operator, site_code, status) |

### 11.4 Go/No-Go — אירוח (חובה לפני פריסת M1)
בזיכרון הפרויקט מתועדת תוכנית לפריסה בשרת חינמי (Render/Vercel). המודול הזה משנה את דרישות האירוח מהותית. שלושה פריטים בינאריים:

| # | שאלה | הדרישה (מ-2.3) | שכבה חינמית טיפוסית **[לא אומת — לבדוק תנאים עדכניים אצל הספק]** | הכרעה מוצעת |
|---|---|---|---|---|
| **G1** | האם ה-DB מכיל 90 יום גולמי? | 50 מכשירים: ~3.5 GB גולמי + אגרגציות ≈ **6–8 GB**; 500 מכשירים: ≈ **40–60 GB** | בסדר גודל של 1 GB, ולעתים עם תפוגת מסד אחרי תקופה | **No-Go** לפרודקשן בשכבה חינמית. Go רק לדמו של ≤ 3 מכשירים, או אם בעל המוצר מקצר שמירה — **וזה דורש שינוי בחוזה** (90 יום מחייב) |
| **G2** | איפה רצות משימות תקופתיות? | התראות כל דקה, אגרגציה כל 5 דק', retention + מחיצות לילי, purge אחרי withdraw (≤ 5 דק' — מחויבות פרטיות) | שירותי web חינמיים נוטים "להירדם" ללא תעבורה; cron/worker הם לעתים בתשלום | **No-Go** אם אין תהליך שרץ תמיד. בלי זה: התראת offline לא נפתחת, withdraw לא מבוצע בזמן. אפשרויות: worker/cron בתשלום; VM קטן עם docker-compose (הקיים) + cron של המארח |
| **G3** | האם ה-API זמין תמיד? | heartbeat כל 5 דק', batch כל 60 ש' | שרת "נרדם" → cold start של שניות-עשרות שניות | Go בתנאי ש-G2 נפתר (תהליך תמידי ממילא מונע הירדמות); האפליקציה עומדת בעיכובים (תור offline) |

**המלצה:** לא לפרוס את המודול על שכבה חינמית. הפתרון הפשוט ביותר שתואם את הקיים: VM יחיד עם `docker-compose.prod.yml` (Nginx + Certbot כבר קיימים לפי TECHNICAL.md), PostgreSQL עם דיסק ≥ 20 GB ל-50 מכשירים, ו-cron של המארח שמריץ `python -m app.scripts.field_maintenance`. ההחלטה — של בעל המוצר (Q6).

### 11.5 שאלות לבעל המוצר
- **Q1** גודל צי צפוי ב-6 ו-12 חודשים?
- **Q2** מי צריך לקבל התראות מחוץ לדאשבורד ובאיזה שלב?
- **Q3** האם VIEWER צריך את טאב החיזוי? (k=3 נקבע ב-v2.)
- **Q4** האם "anonymize" (עיגול 100 מ'/שעה) מקובל משפטית? ייעוץ משפטי לפני PERSONAL — גם לנוסח ההסכמה שמחויב לציין שקריאת נתוני תא דורשת הרשאת מיקום מדויק (v2).
- **Q5** האם יש מקור לרוחב ערוץ/תדרים למפעילים?
- **Q6** **אירוח — Go/No-Go (11.4)**.
- **Q7** שמירת יומן הביקורת — כמה זמן?
- **Q8** האם OPERATOR רשאי לשנות תצורת קבוצה ולהנפיק פקודות? (באפיון: פקודות — A,O; תצורה — A.)
- **Q9** האם להציג roaming?
- **Q10** מי מאשר שיוך ידני תא-לאתר?
- **Q11** `allow_user_pause` ב-ORG — ברירת מחדל true או false? (v2 משאיר פתוח.)
- **Q12** withdraw ממכשיר ORG — אנונימיזציה (ההצעה) או מחיקה, או חסימה?
- **Q13** רישום מחדש יוצר מכשיר חדש (v2) — האם ADMIN צריך "קישור להיסטוריה של מכשיר קודם"?

## 12. סטטוס בקשות שינוי

### 12.1 בקשות הדאשבורד מול חוזה v2
| # | נושא | סטטוס | הערה |
|---|---|---|---|
| D-CR1 | גבולות חצי-פתוחים | התקבל | כלשונו |
| D-CR2 | 2G/3G | התקבל בשינוי | "לא מסווג" + `rssi_dbm` מלא; LAC/PSC/BSIC בשדות הקיימים |
| D-CR3 | OOS/emergency + NSA | התקבל בשינוי | ללא `rsrp_source`; NSA = עוגן LTE + `nr_leg` אופציונלי |
| D-CR4 | תשובת heartbeat + פקודות | התקבל | `sample_now`, `upload_now` ב-MVP; אין FCM |
| D-CR5 | heartbeat בהשהיה | התקבל בשינוי | best-effort ≥ כל 15 דק', ללא מיקום; 6 ש' → "ללא קשר" |
| D-CR6 | קודי HTTP | התקבל בשינוי | 401 רק לטוקן לא תקף/מבוטל (בלי `device_revoked` נפרד); 503 נוסף |
| D-CR7 | רשימת `reason` סגורה | התקבל בשינוי | נוסף `out_of_range:<field>`; כל הסיבות סופיות |
| D-CR8 | טווחי תצורה | התקבל | לפי 3.4 #28 |
| D-CR9 | שמירת אגרגציות | התקבל | שעתי 400 יום, יומי ללא הגבלה |
| D-CR10 | `location_mode` ב-PERSONAL | התקבל | רק החמרה; `effective_location_mode` ב-heartbeat |
| D-CR11 | מקראות נפרדים RSSI/RSRP | התקבל | |
| D-CR12 | גליף פסים | התקבל | גם בתגיות רמה, לא רק בסמנים |
| D-CR13 | פורמט QR | התקבל | `coverageops-enroll:v1?server=…&code=…` |
| D-CR14 | Dual-SIM | התקבל | |
| D-CR15 | `schedule` | התקבל | Asia/Jerusalem, 0=ראשון, חציית חצות |
| D-CR16 | החלפת טוקן | נדחה לשלב מאוחר | |

### 12.2 נקודות פתוחות שנמצאו ביישום v2 (לא עקפתי אותן בשקט)
1. **`acked_commands: [id]` מול "ack עם `skipped_paused`"** — רשימת מזהים לא יכולה לשאת סטטוס. גם צוות האנדרואיד זיהה זאת ומתכנן לשלוח `[{id, status}]`. השרת יקבל את שתי הצורות (3.3); מבקש לתקן את החוזה ל-`[{id, status: done|skipped_paused|unsupported|failed}]`.
2. **`out_of_range` כדחייה סופית של דגימה שלמה** — ערך RF חריג אחד (למשל `rsrq_db`) גורם לאובדן דגימה שלמה, כולל מיקום ו-`service_state` תקינים. הגרסה הקודמת שלי איפסה את השדה ושמרה את הדגימה. מיושם כפי ש-v2 קובע; בפועל האפליקציה מאפסת מראש (טבלה 3.2 שלהם), כך שהנזק קטן — **בתנאי** ששני הצדדים משתמשים באותה טבלת טווחים. מבקש לקבע טבלה אחת בחוזה.
3. **סינון מיושנות לפי `cell_info_age_ms` חל על הדגימה כולה** — גם `service_state` (שאינו מגיע מ-CellInfo) ונתוני סוללה/מיקום. מיושם כלשונו (גם OOS מסונן). אם `service_state` טרי תמיד, עדיף לסנן רק את שדות האות ולשמור את הדגימה ל-OOS — דורש הבהרה.
4. **`cell_info_age_ms = null`** — לא מוגדר; הנחתי "טרי". דורש הבהרה.
5. **withdraw ממכשיר ORG** — v2 מחייב ל-PERSONAL בלבד ולא מגדיר ORG. עובד יכול למחוק נתוני ארגון? הצעתי אנונימיזציה (Q12).
6. **`device_uuid` חדש בכל רישום** + קוד חד-פעמי — תשובת enroll שאבדה ברשת משאירה את המכשיר תקוע. הוספתי ניסיון חוזר אידמפוטנטי (3.3); דורש תיאום.
7. **שם קוד ל-PERSONAL בלי הסכמה** — v2 מחייב את השדות אך לא מגדיר `detail_code`; הצעתי 422 `consent_required`.
8. **`cell_id` נשמר ב-coarse/off** (v2 מסמן לבדיקה משפטית) — `cell_id` + מפעיל + זמן מאפשר לשחזר מיקום ברזולוציית תא (מאות מטרים בעיר), כלומר מחליש את משמעות ה-coarse. בצד הדאשבורד: לא משמש לאמידת מיקום/שיוך אתר בדגימות coarse/off, ולא נחשף ל-VIEWER. ההכרעה המשפטית חוסמת PERSONAL.

## נספח א — קבצים שנקראו
`docs/TECHNICAL.md`, `docs/USER_GUIDE.md`, `backend/app/main.py`, `core/security.py`, `core/migrations.py`, `core/database.py`, `core/config.py`, `models/user.py`, `models/cell_site.py`, `models/device_profile.py`, `api/cell_sites.py`, `api/auth.py`, `api/device_profiles.py`, `tests/test_cell_sites.py`, `frontend/src/main.tsx`, `index.css`, `pages/AdminPage.tsx` (+`.module.css`), `pages/MapPage.tsx`, `components/TopBar.tsx` (+`.module.css`), `Sidebar.tsx`, `LayersPanel.tsx`, `CellSitesInfo.tsx`, `lib/roles.ts`, `lib/cellSites.ts`, `services/api.ts`, `store/useStore.ts`; מסמך הפרויקט `cell-sites-data-source.md`; `/home/claude/work/android/SPEC.md` (סעיפים 4.3, 5.6, 8.6 — לתיאום בלבד). רשימות התלויות (package.json, requirements) — כפי שמסר המתאם בהודעה, לא מקבצים.

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
