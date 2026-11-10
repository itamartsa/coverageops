# תיעוד טכני — CoverageOps

## סקירה כללית

CoverageOps היא מערכת ניתוח כיסוי רדיו מבצעי המורכבת מ:

- **Backend** — FastAPI + PostgreSQL + SQLAlchemy 2
- **Frontend** — React 18 + TypeScript + Zustand + React Query + Leaflet
- **תשתית** — Docker Compose (פיתוח ופרודקשן)

---

## ארכיטקטורה

```
┌─────────────────────────────────────────────────────────┐
│                      Docker Network                      │
│                                                         │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │  React   │───▶│   FastAPI    │───▶│  PostgreSQL   │  │
│  │ :3000    │    │   :8000      │    │  :5432        │  │
│  └──────────┘    └──────┬───────┘    └───────────────┘  │
│                         │                               │
│                         ▼                               │
│              Terrarium DEM tiles (S3, HTTPS)            │
│              LRU cache בזיכרון (TILE_CACHE_SIZE)         │
└─────────────────────────────────────────────────────────┘
```

תוצאות ניתוח נשמרות כ**רסטר RSSI מקודד** בעמודה `coverage_results.rssi_raster`
(ולא כקובצי GeoJSON). PNG, GeoJSON ודוח נגזרים מהרסטר לפי דרישה.
`/data/results` משמש רק לשורות ישנות (legacy) שנשמרו כקובץ GeoJSON.

---

## מבנה תיקיות

```
coverageops/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, middleware, router registration
│   │   ├── core/
│   │   │   ├── config.py        # Pydantic settings (env-driven)
│   │   │   ├── database.py      # SQLAlchemy engine + session
│   │   │   ├── migrations.py    # מיגרציות הוספת עמודות (idempotent) באתחול
│   │   │   └── security.py      # JWT, bcrypt, role-based deps
│   │   ├── models/
│   │   │   └── user.py          # ORM: User, Site, CoverageResult, ActivityLog
│   │   ├── api/
│   │   │   ├── auth.py          # /api/auth/* — login, me
│   │   │   ├── users.py         # /api/users/* — admin CRUD
│   │   │   ├── sites.py         # /api/sites/* — site management
│   │   │   └── coverage.py      # /api/coverage/* — analysis engine
│   │   └── services/
│   │       ├── coverage_engine.py   # RF propagation model (Deygout, k=4/3), raster helpers
│   │       ├── terrain.py           # Terrarium tiles → elevation grid (NaN = missing)
│   │       ├── elevation.py         # Point elevation lookup (None on failure)
│   │       ├── risk.py              # דירוג סיכון + המלצות מבצעיות (מקור אמת יחיד)
│   │       └── report_generator.py  # Word (.docx) report generation
│   ├── scripts/
│   │   └── seed.py              # Idempotent DB seeder (dev users)
│   ├── tests/
│   │   ├── test_coverage.py     # מנוע הפצה
│   │   ├── test_terrain.py      # שכבת אריחי DEM
│   │   ├── test_risk.py         # דירוג סיכון והמלצות
│   │   └── test_api_coverage.py # API end-to-end (SQLite + DEM סינתטי)
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── main.tsx             # Router: /login, / (private), /admin
│   │   ├── services/
│   │   │   └── api.ts           # Axios client + all API calls + TS types
│   │   ├── store/
│   │   │   └── useStore.ts      # Zustand global state
│   │   ├── pages/
│   │   │   ├── LoginPage.tsx
│   │   │   ├── MapPage.tsx      # Main app layout
│   │   │   └── AdminPage.tsx    # User management (ADMIN only)
│   │   └── components/
│   │       ├── MapView.tsx      # Leaflet map + draw tools + overlays
│   │       ├── Sidebar.tsx      # Tab navigation
│   │       ├── SitesList.tsx    # Sites list with edit/delete
│   │       ├── SiteForm.tsx     # Create / edit site form
│   │       ├── AnalysisPanel.tsx    # Coverage analysis wizard
│   │       ├── CrossSectionPanel.tsx # Cross-section analysis
│   │       ├── HistoryPanel.tsx     # Analysis history + report viewer
│   │       ├── TopBar.tsx
│   │       ├── Notification.tsx # Toast event bus
│   │       └── InfoTooltip.tsx  # Hover tooltip
│   ├── Dockerfile
│   └── package.json
├── docs/
│   ├── USER_GUIDE.md
│   └── TECHNICAL.md             ← this file
├── docker-compose.yml           # Development stack
└── docker-compose.prod.yml      # Production stack (Nginx + Certbot)
```

---

## מודל נתונים (DB)

### users
| עמודה      | סוג         | הערה                      |
|-----------|-------------|---------------------------|
| id        | INTEGER PK  |                           |
| username  | VARCHAR(64) | unique, indexed           |
| full_name | VARCHAR(128)|                           |
| hashed_pw | VARCHAR(256)| bcrypt                    |
| role      | ENUM        | ADMIN / OPERATOR / VIEWER |
| is_active | BOOLEAN     | default true              |
| last_login| DATETIME    | nullable                  |
| created_at| DATETIME    |                           |

### sites
| עמודה        | סוג         | הערה                          |
|-------------|-------------|-------------------------------|
| id          | INTEGER PK  |                               |
| name        | VARCHAR(128)|                               |
| lat, lon    | FLOAT       | WGS-84                        |
| ant_height  | FLOAT       | metres AGL, default 6.0       |
| elevation_m | FLOAT       | metres ASL, auto-fetched; NULL = לא ידוע (DEM לא זמין) |
| frequency   | INTEGER     | MHz: 700/850/900/1800/2100/2600/3500 |
| tx_power    | FLOAT       | dBm EIRP, default 43.0        |
| rx_threshold| FLOAT       | dBm, default −90.0            |
| max_radius  | FLOAT       | km, default 350.0             |
| notes       | TEXT        | nullable                      |
| owner_id    | FK→users    |                               |
| created_at  | DATETIME    |                               |
| updated_at  | DATETIME    | auto-updated                  |

### coverage_results
| עמודה         | סוג        | הערה                         |
|--------------|------------|------------------------------|
| id           | INTEGER PK |                              |
| site_id      | FK→sites   | cascade delete               |
| mode         | ENUM       | DTM / DSM                    |
| poly_sw_lat/lon | FLOAT   | bounding box SW corner       |
| poly_ne_lat/lon | FLOAT   | bounding box NE corner       |
| covered_pct  | FLOAT      | % תאים ברדיוס עם RSSI ≥ threshold_dbm (סף הקבלה). בשורות ישנות: % עם "כל אות" |
| covered_pct_any_signal | FLOAT | % תאים עם RSSI > −110 dBm (המשמעות הישנה) |
| weak_pct     | FLOAT      | % תאים ברמות weak + marginal |
| threshold_dbm| FLOAT      | סף הקבלה ששימש בחישוב (rx_threshold של האתר) |
| rssi_avg/max/min | FLOAT  | סטטיסטיקת RSSI על תאים עם אות (> −110 dBm) |
| rssi_min_all | FLOAT      | אחוזון 5 של ה-RSSI על **כל** התאים ברדיוס (כולל ללא אות; מינימום רובסטי) — קלט לדירוג הסיכון; בשורות ישנות NULL → נעשה שימוש ב-rssi_min |
| rssi_raster  | BYTEA      | (deferred — לא נטען ברשימות; `has_raster` מחושב ב-SQL) רסטר RSSI מקודד (`encode_rssi_grid`): int16 ברזולוציית 0.1 dB, zlib; NaN = מחוץ לרדיוס; שורה 0 = צפון |
| resolution   | INTEGER    | תאים לציר של הרסטר |
| dem_source   | VARCHAR(32)| `terrarium` (DEM אמיתי) / `none` (סימולציה) |
| dem_valid_pct| FLOAT      | % תאי DEM תקינים (לא NaN) **בתא השטח של המשתמש** (לא בשטח המורחב) |
| dem_m_per_px | FLOAT      | רזולוציית ה-DEM המקורית (מ'/פיקסל) |
| model        | VARCHAR(128)| תיאור מודל ההפצה כפי שהמנוע החזיר |
| geojson_path | VARCHAR    | legacy בלבד – קובץ GeoJSON של שורות ישנות |
| duration_sec | FLOAT      | זמן חישוב כולל (כולל שליפת DEM) |
| created_at   | DATETIME   |                              |

**מיגרציה:** `Base.metadata.create_all` אינו מוסיף עמודות לטבלה קיימת.
`app/core/migrations.py → run_startup_migrations(engine)` רץ באתחול האפליקציה (lifespan)
ומוסיף כל עמודה חסרה (`ALTER TABLE … ADD COLUMN IF NOT EXISTS` ב-PostgreSQL;
בדיקת inspector + `ADD COLUMN` ב-SQLite). שורות ישנות נשארות עם NULL בעמודות החדשות.
האתחול (create_all + מיגרציות) רץ ב-startup ולא בזמן import, כך שניתן לייבא את
`app.main` גם ללא מסד נתונים זמין.

### activity_log
אוגר כניסות מוצלחות ונכשלות, ופעולות מודול הקליטה בשטח (`FIELD_*`, detail = JSON). רשומות `FIELD_*` נחשפות ל-ADMIN
ב-`GET /api/field/audit`; שאר הרשומות אינן נחשפות ב-API.

---

### cell_sites / cell_sites_sync
שכבת אתרי סלולר אזרחיים — מיובאת מ-data.gov.il (המשרד להגנת הסביבה).

| שדה | הערה |
|---|---|
| status | `active` / `construction` |
| operator | `PHI` / `PELEPHONE` / `CELLCOM` / `OTHER` (+ `operator_he` מקורי) |
| site_code, city, address, authority, jurisdiction | כמו במקור |
| site_type / site_type_he | מנורמל (ground_mast, rooftop_mast, small_indoor, sting, wireless_access, tilted) + מקור |
| technologies | למשל `2G,3G,4G,5G` |
| lat, lon | WGS84 — הומר מ-ITM (EPSG:2039) ב-`services/itm.py` (פייתון טהור, ללא pyproj; דיוק ~3 מ') |
| x_itm, y_itm | קואורדינטות המקור |
| *_permit_date, last_periodic_check, max_pct_of_threshold, *_permit_url | אתרים פעילים |
| request_date, permit_type, reference_no | אתרים בהקמה |
| ant_height_m | גובה אנטנה (מ' מעל הקרקע). **לא קיים במאגר הממשלתי** — משוער לפי `site_type`, או הוזן ידנית |
| height_source | `estimated` (ברירת מחדל, NOT NULL) / `manual` |
| height_note, height_updated_at, height_updated_by | הערה / מועד (UTC) / שם משתמש של העדכון האחרון לגובה |

`cell_site_type_defaults` — גובה משוער לכל סוג אתר (ניתן לעריכה ע"י ADMIN):
`site_type` (PK), `default_height_m`, `label_he`, `note_he`. נזרע באתחול (`seed_type_defaults`, אידמפוטנטי — לא דורס
עריכות) מתוך `HEIGHT_DEFAULTS` ב-`services/cell_sites_import.py`:
ground_mast 25 · rooftop_mast 18 · sting 15 · tilted 15 · wireless_access 6 · small_outdoor 6 · small_indoor 3 · other 15.
`estimated_height_for(site_type, db)` מחזיר את הערך מהטבלה (נפילה למילון).

גבהים בייבוא/רענון: `import_data` מחליף את כל הטבלה; לפני המחיקה הוא שומר את כל השורות `manual` לפי
(operator, site_code, status) — או `source_id` כשאין site_code — ומשחזר אותן על השורות החדשות; כל שאר השורות מקבלות
את ההערכה לפי סוג. באתחול (`ensure_loaded`) שורות עם `ant_height_m IS NULL` מקבלות הערכה (backfill חד-פעמי אחרי הוספת
העמודות). העמודות נוספות ב-`core/migrations.py` (`ADD COLUMN IF NOT EXISTS`; `height_source` עם `NOT NULL DEFAULT 'estimated'`).

`cell_sites_sync` — שורה לכל ייבוא (מקור live/snapshot, מועד משיכה, ספירות).

מקורות (CKAN datastore API):
- `antennaactive` — resource `8935c8e5-ec77-421f-af86-d970583195f8`
- `antenna_hakama` — resource `ff398c7e-c522-4ee8-a53a-312b188a573d`

עותק שמור: `backend/app/seed_data/cell_sites_snapshot.json.gz` — נטען אוטומטית באתחול (`init_db`) אם הטבלה ריקה.
ייבוא ידני: `python -m app.scripts.import_cell_sites` (חי, עם נפילה לעותק) או `--snapshot`.

## API Endpoints

### Authentication — `/api/auth`

| Method | Path        | Auth       | תיאור                    |
|--------|-------------|------------|--------------------------|
| POST   | /login      | ללא        | OAuth2 password grant    |
| GET    | /me         | JWT        | פרטי המשתמש המחובר      |

### Users — `/api/users`

| Method | Path        | Auth       | תיאור                    |
|--------|-------------|------------|--------------------------|
| GET    | /           | ADMIN      | רשימת כל המשתמשים       |
| POST   | /           | ADMIN      | יצירת משתמש חדש         |
| PUT    | /{id}       | ADMIN      | עדכון משתמש             |
| DELETE | /{id}       | ADMIN      | מחיקת משתמש             |

### Sites — `/api/sites`

| Method | Path        | Auth             | תיאור                      |
|--------|-------------|------------------|----------------------------|
| GET    | /           | JWT              | רשימת אתרים                |
| POST   | /           | ADMIN/OPERATOR   | יצירת אתר + שליפת elevation|
| GET    | /{id}       | JWT              | אתר בודד                   |
| PUT    | /{id}       | ADMIN/OPERATOR   | עדכון אתר                  |
| DELETE | /{id}       | ADMIN/OPERATOR   | מחיקת אתר + תוצאות        |

### Coverage — `/api/coverage`

| Method | Path                        | Auth           | תיאור                            |
|--------|-----------------------------|----------------|----------------------------------|
| POST   | /analyze                    | ADMIN/OPERATOR | הרצת ניתוח כיסוי שטחי           |
| GET    | /                           | JWT            | 100 ניתוחים אחרונים (היסטוריה)  |
| GET    | /history/{site_id}          | JWT            | 20 ניתוחים אחרונים לאתר         |
| GET    | /result/{id}                | JWT            | סטטיסטיקה + איכות נתונים (ללא GeoJSON) |
| GET    | /result/{id}/png            | JWT            | שכבת PNG מהרסטר (`width`,`height`) – תמיד על ה-bbox השמור |
| GET    | /result/{id}/geojson        | JWT            | הורדת GeoJSON (נבנה לפי דרישה, `Content-Disposition`) |
| DELETE | /result/{id}                | ADMIN/OPERATOR | מחיקת תוצאה                     |
| GET    | /result/{id}/report         | JWT            | דוח מלא: סיכון, אשכולות אפלה, איכות נתונים, המלצות |
| POST   | /cross-section              | ADMIN/OPERATOR | ניתוח חתך רדיו (JSON)            |
| POST   | /cross-section/docx         | ADMIN/OPERATOR | ניתוח חתך רדיו (Word)            |
| GET    | /point-elevation            | JWT            | גובה נקודה (`elevation_m: null` אם אין נתונים) |
| GET    | /topo-overlay               | JWT            | שכבת גבהים PNG (שקוף היכן שאין DEM) |

**מגבלות שטח:** צלע bbox מעל `MAX_BBOX_SPAN_KM` (400 ק"מ) → HTTP 400 (`/analyze`, `/topo-overlay`).
כאשר שכבת ה-DEM חורגת מתקציב האריחים/פיקסלים (`DemAreaTooLarge`) → HTTP 413 עם הודעה בעברית
(`/analyze`, `/cross-section`, `/cross-section/docx`, `/topo-overlay`).

**גובה האתר:** אם `sites.elevation_m` הוא NULL, הגובה נדגם מגריד ה-DEM של הניתוח (בילינארי,
מרכזי תאים). אם אין DEM במיקום האתר — המשדר מוצב ב-0 מ' ומתווספת אזהרה בעברית לתגובת
`/analyze` / `/cross-section` (האזהרה אינה נשמרת ב-DB).

### `POST /analyze` — פרטים

- `resolution` אופציונלי. ללא ערך → **רזולוציה אדפטיבית**:
  `R = clamp(round(max_span_m / max(TARGET_CELL_M, dem_m_per_px)), MIN_GRID_RESOLUTION, MAX_GRID_RESOLUTION)`
  (תא לא יהיה עדין מרזולוציית ה-DEM — אין "פירוט מזויף"). ערך מפורש נשמר (20–500).
- **אתר מחוץ לתא השטח:** החישוב רץ על bbox מורחב שמכיל את האתר + 0.5 ק"מ, באותו גודל תא
  ובהתאמה מדויקת לתאי ה-bbox של המשתמש; לאחר מכן הרסטר נחתך חזרה ל-bbox של המשתמש.
  כך חסימות טופוגרפיות בין האתר לתא השטח נכללות. ההרחבה מוגבלת ל-`MAX_GRID_RESOLUTION`
  תאים לציר; כשההגבלה חלה מוחזרת אזהרה. `dem_extended` בתגובה מציין אם בוצעה הרחבה.
- הסטטיסטיקה מחושבת על הרסטר החתוך (שטח המשתמש בלבד).
- `risk_level`/`risk_score` מחושבים ב-`risk.assess_risk` עם `rssi_min_all`.
- `warnings` (עברית): ללא DEM (`dem_source != "terrarium"`), DEM חלקי (`dem_valid_pct < 95`),
  הרחבה חלקית, ושורה ישנה (legacy).

### `POST /cross-section`

`covered_pct` = % נקודות לאורך הציר עם RSSI ≥ `threshold_dbm` (סף הקבלה); אזורים מתים מוגדרים
לפי אותו כלל. `covered_pct_any_signal` = % נקודות עם אות > −110 dBm. בנוסף: `risk_score`,
`dem_source`, `dem_valid_pct`, `dem_m_per_px`, `warnings`. בדוח ה-Word סימון ✓/✗ לכל נקודה
מבוסס על הדגל `covered` של המנוע (אותו כלל של אזורים מתים). `rssi_min_all` מוחזר גם כאן.

### `GET /result/{id}/png`

הפרמטרים `sw_lat…ne_lon` נשמרו לתאימות לאחור אך **מתעלמים מהם** עבור שורות עם רסטר —
התמונה תמיד מכסה את ה-bbox השמור של התוצאה. בשורות ישנות (GeoJSON בלבד) נעשה שימוש
ברנדרר הישן, עם ה-bbox שהועבר או ה-bbox השמור.

### `GET /result/{id}/report`

- התפלגות רמות מ-`level_distribution_from_grid` (כולל `none`, אחוזים מתאים ברדיוס).
- אשכולות אפלה: רכיבים קשירים (8-שכנים, BFS ב-O(תאים)) של תאים מתחת לסף; 10 הגדולים
  עם `centroid_lat/lon`, `cell_count`, `area_km2`, `rssi_avg`, `rssi_min`, `severity`
  (`none` = יש תא ללא אות כלל, `marginal` = אות קיים אך מתחת לסף).
- סיכון/סיכום/המלצות (קלט RSSI מינימלי = `rssi_min_all`, ובשורות ישנות `rssi_min`): `risk.assess_risk`, `risk.operational_summary`,
  `risk.build_area_recommendations` — ללא נוסחאות כפולות ב-API.
- `propagation.model` מהעמודה `model`; `height_gain_db` = `effective_height_gain_db(ant_height, has_dem)`.
- `data_quality`: `dem_source, dem_valid_pct, dem_m_per_px, resolution, diffraction_method`.
- **שורות ישנות:** קובץ ה-GeoJSON מומר חזרה לגריד (תאי `none` לא נשמרו בעבר ולכן
  לא ידועים), `covered_pct` הוא הערך הישן השמור, ואזהרת legacy מופיעה ראשונה בהמלצות.

---

### Cell sites — `/api/cell-sites`
| Method | Path | הרשאה | תיאור |
|--------|------|-------|-------|
| GET  | /       | כל משתמש | רשימה דחוסה למפה: `{fields, rows}`; פילטרים `status`, `operator` (מופרד בפסיקים). fields: `id, lat, lon, operator, status, site_type, techs, ant_height_m, height_source, height_note, height_updated_at` |
| GET  | /meta   | כל משתמש | מקור, ספירות לפי סטטוס/מפעיל/סוג/טכנולוגיה, ייבוא אחרון, ו-`height_estimation: {note_he, defaults[], manual_count, estimated_count}` |
| GET  | /type-defaults | כל משתמש | `[{site_type, default_height_m, label_he, note_he}]` |
| PUT  | /type-defaults/{site_type} | ADMIN | גוף `{default_height_m}` (0.5–300). מעדכן גם את כל האתרים מהסוג שעדיין `estimated`; מחזיר את השורה + `updated_sites`. 404 לסוג לא מוכר |
| GET  | /{id}   | כל משתמש | כל השדות של אתר (כולל שדות הגובה; `height_updated_at` ב-ISO עם `Z`) |
| PATCH | /{id}/height | ADMIN, OPERATOR | גוף `{ant_height_m: 0.5–300, note?: str ≤256}` → `height_source="manual"`, רושם משתמש ומועד; מחזיר את האתר |
| DELETE | /{id}/height | ADMIN, OPERATOR | חזרה להערכה לפי סוג האתר (`estimated`, ההערה נמחקת); מחזיר את האתר |
| POST | /sync   | ADMIN    | רענון מ-data.gov.il (נפילה לעותק השמור). גבהים ידניים נשמרים |

נתיבים סטטיים (`/meta`, `/type-defaults`) מוגדרים לפני `/{site_id}`.

Frontend: עריכת גובה בחלון האתר — `cellSitePopupHtml(detail, canEdit)` ב-`lib/cellSites.ts` מייצר HTML עם
`data-cellsite-id` וכפתורי `data-cs-action` (edit/save/cancel/reset); ב-`MapView.tsx` מאזין `popupopen` יחיד על המפה
מחבר click/keydown מואצל לכל אלמנט popup (פעם אחת), קורא ל-`cellSitesApi.setHeight/resetHeight`, מעדכן את השורה
בזיכרון (`cellSitesDataRef`) ואת סגנון הסמן (טבעת כהה לגובה ידני), ומרנדר מחדש את החלון.

### Point-to-point link — `/api/link`

נתב נפרד (`app/api/link.py`, נרשם ב-`main.py` תחת `/api/link`) — לא משנה את `/api/coverage`.
הפיזיקה ב-`services/link_engine.py` (`calculate_link`, `best_servers`, `link_bbox`).

| Method | Path | Auth | תיאור |
|--------|------|------|-------|
| POST   | /               | ADMIN/OPERATOR | תקציב קישור משדר → נקודה; נשמר ב-`link_results` כאשר `save=true` (ברירת מחדל) |
| POST   | /best-servers   | ADMIN/OPERATOR | דירוג אתרי סלולר אזרחיים סביב נקודה ("מי משרת"); לא נשמר |
| GET    | /               | JWT            | 100 הקישורים האחרונים (כל המשתמשים), שדות סיכום בלבד |
| GET    | /{id}           | JWT            | התגובה המלאה השמורה (זהה ל-POST /, כולל `profile`) |
| GET    | /{id}/docx      | JWT            | דוח Word (שם קובץ ASCII + `filename*` RFC 5987) |
| DELETE | /{id}           | ADMIN/OPERATOR | מחיקה — הבעלים או ADMIN (אחרת 403) |

**`POST /` — גוף:**
```json
{ "transmitter": {"kind": "site" | "cell_site", "id": 1, "frequency_mhz": 900},
  "receiver": {"lat": 32.0, "lon": 35.0, "device_profile_id": 2, "height_agl": 1.5,
               "sensitivity_dbm": -105, "antenna_gain_dbi": 0, "name": "נקודה"},
  "mode": "DTM" | "DSM", "save": true }
```
- `frequency_mhz` אופציונלי (100–6000, אחרת 422).
- **משדר `site`:** פרמטרי האתר; גובה קרקע = `sites.elevation_m`, אחרת דגימה מה-DEM, אחרת 0 מ' + אזהרה.
  `estimated_fields` = כל שלושת השדות אם `params_source="estimated"`, אחרת `ant_height_m` אם `ant_height_is_default`.
- **משדר `cell_site`:** `ant_height = ant_height_m or 15`, `tx_power = tx_power_dbm or 40`,
  תדר = מפורש → ערך ידני (`rf_source="manual"`) → `default_band(technologies)` (הפס הנמוך).
  גובה קרקע תמיד מדגימת DEM (בילינארי, מרכזי תאים), אחרת 0 מ' + אזהרה. `max_radius = 60`.
  כל שדה שאינו ידני נכנס ל-`estimated_fields` (`ant_height_m`, `tx_power_dbm`, `frequency_mhz`).
- **מקלט:** פרופיל מפורש (404 אם לא קיים) → פרופיל ברירת המחדל (`is_default`) → ערכים מובנים
  (1.5 מ', −105 dBm, 0 dBi); ערכים מפורשים גוברים על הפרופיל. `rx_threshold` של המשדר = רגישות המקלט.
- מרחק > 150 ק"מ → 400. DEM: קריאה אחת ל-`link_bbox(..., 0.01°)` ברזולוציה אדפטיבית לכל ציר:
  `clamp(round(side_m / max(TARGET_CELL_M, dem_m_per_px)), 20, 500)`. `DemAreaTooLarge` → 413.
  DEM חסר → `dem_source="none"` + אזהרה.

**תגובה:** כל שדות `LinkResult` (dataclass) ועוד:
```
distance_km, bearing_deg, tx_h_asl, rx_h_asl, tx_ground_asl, rx_ground_asl,
fspl_db, diffraction_db, clutter_db, height_gain_db, rx_gain_db,
rssi_dbm, margin_db, level, is_los, fresnel_clearance_pct,
worst_obstacle: {dist_km, lat, lon, terrain_m, los_m, nu} | null,
profile: [{dist_km, lat, lon, terrain_m|null, los_m, fresnel_m}]   (81 נקודות),
required_rx_height_m: float|null, dem_source, dem_valid_pct, mode,
recommendations: [str], duration_sec, rx_ground_fallback, profile_valid_pct,
id: int|null, created_at: ISO-UTC "…Z", dem_m_per_px: float|null,
transmitter: {kind, id, name, lat, lon, ant_height_m, frequency_mhz, tx_power_dbm,
              params_source: "manual"|"estimated", estimated_fields: [str], ground_asl,
              (cell_site בלבד:) operator, operator_he, site_type, site_type_he, technologies, bands},
receiver: {lat, lon, height_agl, sensitivity_dbm, antenna_gain_dbi, name,
           profile_id, profile_key, profile_name, overridden: [str], ground_asl},
warnings: [str]   (עברית: פרמטרים משוערים, ללא DEM / DEM חלקי, גובה קרקע משדר לא ידוע, גובה קרקע מקלט לא ידוע)
```
שרטוט החתך: `los_m ± fresnel_m` מעל `terrain_m` (גבהים מעל פני הים, על כדור ארץ שטוח — בליטת
כדור הארץ כבר מוחסרת מ-`los_m`).

**`POST /best-servers` — גוף:**
`{receiver: {...כנ"ל}, mode, radius_km: 1–30 (10), operator?: "PHI"|"PELEPHONE"|"CELLCOM",
statuses: ["active"] (ברירת מחדל; אפשר גם "construction"), top_n: 1–10 (5), band_policy: "lowest"|"highest"}`.
סינון: ריבוע lat/lon סביב הנקודה → haversine ≤ radius, ללא `site_type="small_indoor"`, עד 400 הקרובים.
קריאת DEM אחת לריבוע (נקודה ± רדיוס + 0.01°). לכל מועמד: תדר ידני אם `rf_source="manual"`, אחרת לפי
`band_policy` מתוך `bands_for(technologies)` (900 אם אין), `tx_power_dbm or 40`, `ant_height_m or 15`,
גובה קרקע מה-DEM (אחרת גובה הקרקע במקלט). `best_servers(..., profile_points=40)` — ~0.3 ש' ל-100 מועמדים.

תגובה:
```
{ receiver: {...כנ"ל, ground_asl}, mode, radius_km, band_policy, candidates_evaluated,
  dem_source: "terrarium"|"none", dem_valid_pct,
  results: [{ rank,
     cell_site: {id, operator, operator_he, site_type, site_type_he, city, address, lat, lon,
                 ant_height_m, height_source, frequency_mhz, tx_power_dbm, rf_source, bands,
                 status, site_code},
     link: {distance_km, bearing_deg, rssi_dbm, margin_db, level, is_los, diffraction_db,
            fresnel_clearance_pct} }],
  warnings: [str] }
```

**`GET /` — שורת סיכום:** `id, owner_id, tx_kind, tx_id, tx_name, tx_lat, tx_lon, tx_frequency,
tx_power_dbm, tx_ant_height_m, tx_params_source, rx_lat, rx_lon, rx_height_agl, rx_sensitivity_dbm,
rx_gain_dbi, rx_profile_key, rx_name, mode, distance_km, bearing_deg, rssi_dbm, margin_db, level, is_los,
diffraction_db, fspl_db, fresnel_clearance_pct, required_rx_height_m, dem_source, dem_valid_pct, created_at`.

**טבלה `link_results`** (`app/models/link_result.py`): כל עמודות הסיכום לעיל + `result_json` (Text, deferred —
התגובה המלאה כולל הפרופיל). נוצרת ע"י `create_all`; אין עמודות להוספה ב-`migrations.py`.

**דוח Word** (`report_generator.generate_link_docx(stored_response)`): כותרת, טבלאות משדר/מקלט (ערכים
משוערים מסומנים "(משוער)"), טבלת תוצאות (מרחק, אזימוט, FSPL, עקיפה, RSSI, מרווח, LOS, פרנל %),
מכשול עיקרי, תרשים חתך PNG (Pillow — `render_link_profile_png`: טרן, קו ראייה ירוק/אדום, מעטפת פרנל כחולה),
המלצות, אזהרות ושורת איכות נתונים.

## מנוע הפצה (coverage_engine.py)

### מודל הפצה

```
RSSI (dBm) = Tx_power − FSPL(d, f) − DiffractionLoss − ClutterLoss + HeightGain
```

**FSPL** (Free Space Path Loss, ITU-R P.525):
```
FSPL = 20·log₁₀(d_m) + 20·log₁₀(f_Hz) − 147.55
```

**DiffractionLoss** — ITU-R P.526-15 §4.5, **Deygout** רב-שפות (עד 3 שפות: השפה הראשית +
שפה דומיננטית בכל צד), על פני פרופיל של 24 נקודות. כל נקודה מוגבהת בבליטת כדור הארץ
`d1·d2 / (2·k·R_e)` עם **k = 4/3**. ניתן לבחור `diffraction_method="single"` (שפה אחת).

**ClutterLoss** — במצב DSM בלבד: 9 dB סטטיסטי.

**HeightGain** — עם DEM אמיתי: `min(20·log₁₀(h/1.5), 6 dB)` (גובה האנטנה כבר נכלל בגיאומטריית
קו הראייה); ללא DEM: `20·log₁₀(h/1.5)` מלא.

### נתוני גבהים (terrain.py)

- אריחי Terrarium מפוענחים למטרים **לפני** דגימה מחדש; זום נבחר אוטומטית (8–12) לפי תקציב
  `MAX_DEM_TILES`; הורדה מקבילית עם מטמון LRU.
- אריח שנכשל → **NaN** בגריד (לא −32768, לא אפסים). במנוע, NaN = שטח לא ידוע ומוחלף
  באינטרפולציה לינארית של הקרקע בין המשדר למקלט.
- אם אף אריח לא זמין → `None`, והמנוע עובר לסימולציה פסאודו-אקראית המסומנת
  `dem_source="none"` (ומלווה באזהרה בכל התגובות).

### אחוז כיסוי (covered_pct)

`covered_pct` = אחוז התאים בתוך רדיוס הניתוח שבהם **RSSI ≥ סף הקבלה של האתר** (`rx_threshold`).
`covered_pct_any_signal` = אחוז התאים עם אות כלשהו (> −110 dBm) — המשמעות הישנה של covered_pct.

### דירוג RSSI

| רמה      | טווח (dBm)        |
|---------|-------------------|
| excellent| > −70            |
| good    | −70 עד −80        |
| medium  | −80 עד −90        |
| weak    | −90 עד −100       |
| marginal| −100 עד −110      |
| none    | ≤ −110           |

### דירוג סיכון (risk.py)

מקור אמת יחיד לניתוח שטחי ולחתך. רמה עולה ברגע שקריטריון אחד מתקיים:

| רמה      | covered_pct | אזור מת ארוך (ק"מ) | weak_pct | RSSI מינ' מתחת לסף (חתך בלבד) |
|----------|-------------|--------------------|----------|-------------------------------|
| CRITICAL | < 50        | > 3                | > 30     | > 15 dB                       |
| HIGH     | < 70        | > 2                | > 20     | > 10 dB                       |
| MEDIUM   | < 85        | > 1                | > 10     | > 5 dB                        |
| LOW      | אחרת        |                    |          |                               |

קריטריון ה-RSSI נמדד **ביחס לסף הקבלה** של האתר (`rx_threshold − rssi_min`) וחל רק על חתך ציר,
שבו נקודה אחת עמוק מתחת לסף היא נקודה מתה אמיתית במסלול. בניתוח שטחי הוא לא מופעל: החלק מהשטח
שמתחת לסף כבר נמדד ב-covered_pct, והספים הקבועים הקודמים (−115/−110/−105 dBm) דירגו שטחים
מכוסים היטב כ-CRITICAL בגלל כמה תאים מוצללים, בלי קשר לסף הקבלה בפועל. `rssi_min_all` נשמר להצגה.

### רזולוציית גריד

- אדפטיבית כברירת מחדל: יעד `TARGET_CELL_M` (40 מ') לתא, לא עדין מה-DEM,
  בטווח `MIN_GRID_RESOLUTION`–`MAX_GRID_RESOLUTION` (60–500).
- ערך מפורש מהלקוח: 20–500.

---

## קליטה בשטח (Field reception) — `/api/field`

מודול ניטור קליטה ממכשירי שטח (אפליקציית CoverageOps Field Probe). מסמכים מחייבים:
`docs/field/API_CONTRACT.md` (כולל "גרסה 2" ו-"גרסה 2.1" שגוברות), `docs/field/FIELD_DASHBOARD_SPEC.md`,
`docs/field/ANDROID_APP_SPEC.md`. ממומש בצד שרת: אבני דרך M1, צד השרת של M2, ו-M3.
**לא ממומש (שלבים מאוחרים):** נמדד-מול-חזוי וכיול (M4: `/compare`, `/calibration`, `/cell-site-map`),
חיזוי ובריאות מכשיר (M5: `/predict/*`, `/device-health`), ML/SSE (M6). ה-endpoints האלה אינם קיימים.

### קבצים
| קובץ | תפקיד |
|---|---|
| `app/models/field.py` | כל טבלאות `field_*` + ביטי `flags` |
| `app/core/device_auth.py` | `get_current_device` (טוקן מכשיר `fdt_…`), `FieldHTTPError` → גוף `{detail, detail_code}` |
| `app/core/ratelimit.py` | token bucket בזיכרון התהליך (לכל worker) |
| `app/core/audit.py` | `log_activity()` → `activity_log` (detail = JSON) |
| `app/api/field_ingest.py` | ממשקי המכשיר (נרשם ב-`main.py` **לפני** `field.py`) |
| `app/api/field.py` | ממשקי הדאשבורד (JWT משתמש) |
| `app/services/field_quality.py` | סיווג רמות (מקור אמת יחיד), `meas_tech`, טבלת טווחים, אימות ונרמול דגימה, גריד |
| `app/services/field_ingest.py` | קליטת batch/heartbeat (משמש גם את זריעת הדמו) |
| `app/services/field_agg.py` · `field_kpi.py` | אגרגציה מתור dirty, מפת חום, אזורים מתים · KPI מהגולמי |
| `app/services/field_status.py` · `field_alerts.py` | סטטוס חי (מחושב) · הערכת התראות |
| `app/services/field_privacy.py` · `field_maintenance.py` | מחיקה/אנונימיזציה · משימת תחזוקה |
| `app/services/field_config.py` | תצורה (ברירות מחדל, אימות טווחים, מיזוג) + זריעות אידמפוטנטיות |
| `app/services/field_demo.py` · `app/scripts/field_simulator.py` | נתוני דמו סינתטיים + סימולטור HTTP |
| `app/services/field_requirements.py` | תוכן `GET /meta/device-requirements` |
| `tests/fixtures/field_tier_vectors.json` | וקטורי הרמות + טבלת הטווחים, משותף עם צוות האנדרואיד |

### טבלאות
כל הטבלאות חדשות ונוצרות שלמות ב-`create_all` (אין עמודות ב-`_ADDITIVE_COLUMNS`). זמנים: UTC נאיבי; בפלט ISO עם `Z`.

| טבלה | תוכן |
|---|---|
| `field_device_groups` | קבוצות + `config` (חלקי, ממוזג על ברירות המחדל). `is_default` = הקבוצה "כללי" (נזרעת באתחול) |
| `field_enrollment_codes` | `code_hash` (sha256 + pepper), `code_hint`, `ownership` (ORG/PERSONAL, חובה), `max_uses`, `use_count`, `expires_at`, `revoked_at` |
| `field_devices` | `public_id` (= `device_id` בחוזה), `device_uuid`, `ownership`, הסכמה, קבוצה, `config_override`, `config_version` לכל מכשיר, `token_hash`, `status` (`active`/`revoked`/`withdrawn`/`deleted`), `purge_mode`/`purged_at`, ועמודות `last_*` (מצב אחרון מ-batch/heartbeat). `is_anon_sink` = מכשיר-מצבה לשורות מאונונמות |
| `field_measurements` | דגימות גולמיות, PK `(device_pk, ts, sample_id)`; עמודות מחושבות `tier`, `meas_tech` (LTE/NR/LEGACY/NONE), `gx/gy` (G100), `gx25/gy25` (2.5 ק"מ), `flags` |
| `field_heartbeats` | כל heartbeat (שמירה 30 יום) |
| `field_device_commands` | תור פקודות (`sample_now`, `upload_now`); אינדקס ייחודי חלקי `uq_cmd_open` = פקודה פתוחה אחת מכל סוג |
| `field_agg_hourly` / `field_agg_daily` / `field_agg_daily_coarse` | תא G100 × שעה UTC / תא G100 × יום ישראל / תא 2.5 ק"מ × יום ישראל, × מפעיל × `meas_tech`; היסטוגרמת RSRP של 2 dB, ספירות ומשקלי זמן לפי רמה, OOS, `device_set` (פנימי — לא נחשף לעולם) |
| `field_agg_dirty` | תור עבודה לאגרגציה; `level` = `g100` / `g2500` |
| `field_alert_rules` / `field_alerts` | כללים (נזרעים) / התראות; `uq_field_alert_open` = התראה פתוחה אחת ל-`dedup_key` |
| `field_operator_map` | `425-01` פרטנר, `425-02` סלקום, `425-03` פלאפון, `425-07` הוט מובייל, `425-08` (לא ודאי) |

ביטי `flags`: 2 מחוץ לישראל · 4 `ts` עתידי קל (≤ 5 דק', נשמר כמות שהוא) · 8 מאונונם · 16 `stale_cell` (`cell_info_age_ms > 10000`) ·
32 coarse · 64 מיקום ישן (`loc_age_ms > 30000`) · 128 sentinel אנדרואיד → null · 256 `age_unknown` (`cell_info_age_ms = null`) · 512 ערך רדיו מחוץ לטווח → null.

**PostgreSQL:** אינדקס `ix_fm_ts` נוצר כ-BRIN (`postgresql_using`), ב-SQLite B-tree. **partitioning לא מומש** —
`create_all` אינו יכול להמיר טבלה קיימת לטבלה מחולקת, ומשימות יצירת/מחיקת מחיצות לא נבדקו; שמירה = `DELETE` ברצועות יום.

### אימות מכשיר
- טוקן: `fdt_` + 43 תווי base64url; נשמר רק `sha256(FIELD_TOKEN_PEPPER + token)`. ביטול = `status != active` או `token_hash = NULL`.
- **401 רק לטוקן חסר/לא תקף/מבוטל** (`detail_code: token_invalid`). תקלת DB בתוך האימות → **503 + `Retry-After: 30`**.
- טוקן מכשיר נדחה בכל endpoint של משתמש (אינו JWT), ו-JWT נדחה בממשקי המכשיר.
- שגיאות בגוף `{detail, detail_code}`; 429 עם `Retry-After`.

### ממשקי מכשיר (טוקן מכשיר; `enroll` ללא אימות)
| Method | Path | הערות |
|---|---|---|
| POST | `/devices/enroll` | 201 `{device_id, device_token, ownership, config}`; 400 `code_invalid` · 410 `code_expired` · 409 `code_used` / `ownership_mismatch` · 422 `consent_required`. ניסיון חוזר עם אותו (קוד, `device_uuid`) תוך 15 דק' כשהטוקן טרם שימש → אותו `device_id`, טוקן חדש, בלי שימוש נוסף בקוד. 10/דקה ל-IP; 5 כשלונות ב-10 דק' → 429 ל-15 דק' |
| POST | `/measurements/batch` | `Content-Encoding: gzip` (או JSON רגיל); דחוס > 1MB / פרוס > `FIELD_MAX_BATCH_BYTES` → 413; > 500 דגימות → 413; מעטפה/שדה לא מוכר → 422; `device_id` זר → 403 `device_mismatch`. תשובה `{accepted, duplicates, rejected:[{sample_id, reason}], normalized, config_version}` — `normalized` = דגימות שערך רדיו בהן אופס. 30/דקה (burst 10) |
| POST | `/devices/heartbeat` | תשובה `{config_version, server_time, commands:[{id,type,issued_at}]}`; `acked_commands: [{id, status ∈ done/skipped_paused/failed/expired}]`; `permissions.location` = precise/approximate/none. 6/דקה |
| GET | `/devices/me/config` | תצורה אפקטיבית; `ETag: "<config_version>"`, `If-None-Match` → 304 |
| POST | `/devices/me/withdraw` | PERSONAL בלבד (ORG → 403 `withdraw_not_allowed`); הטוקן מבוטל מיד, הנתונים נמחקים בריצת התחזוקה הבאה. `{status, data_action, completes_by}` |

סיבות דחייה (סגור): `invalid_sample_id`, `invalid_ts`, `ts_in_future`, `ts_too_old`, `invalid_enum:<field>`, `out_of_range:lat|lon`.
כל ערך מספרי אחר מחוץ לטווח מאופס ל-null (טבלת הטווחים ב-`field_quality.RANGES` = `field_tier_vectors.json`).

### ממשקי דאשבורד (JWT) — A=ADMIN, O=OPERATOR, V=VIEWER
| Method | Path | תפקיד | תמצית |
|---|---|---|---|
| GET | `/meta/device-requirements` | כל משתמש | דרישות מכשיר הקצה (Android 11 / API 30 ומעלה), פריטי "לאימות" מסומנים |
| GET | `/meta/tiers` | כל משתמש | רמות, ספים, תוויות סטטוס |
| GET | `/summary` | A,O,V | `fleet` + `kpis` + `freshness`. V: בלי `group_id` (403) |
| GET | `/devices` | A,O | `{fields, rows}`; פילטרים `group_id,status,ownership,q,sort` |
| GET | `/devices/live` | A,O | `{server_time, fields, rows}`; רק active/degraded/offline עם מיקום ≤ 60 דק'; `precision=coarse` → עיגול ~1 ק"מ |
| GET/PATCH | `/devices/{id}` | GET A,O · PATCH A | פרטים מלאים / `{label?, group_id?, notes?, config_override?}` |
| POST | `/devices/{id}/revoke` | A | `{status}` |
| DELETE | `/devices/{id}?mode=delete\|anonymize` | A | 202 `{data_action, completes_by}`; ORG — אנונימיזציה בלבד (`mode=delete` → 422 `org_anonymize_only`) |
| GET | `/devices/{id}/track` | A,O | GeoJSON; נרשם `FIELD_TRACK_VIEW`; ≤ 7 ימים |
| GET | `/devices/{id}/timeseries` | A,O | `metric=rsrp\|nr_ss_rsrp\|nr_leg_ss_rsrp\|rsrq\|sinr\|battery`; סדרה נפרדת לכל `meas_tech` |
| POST/GET | `/devices/{id}/commands` | A,O | הנפקה (201; 409 `command_pending`/`device_inactive`; 10/שעה) / רשימה; נרשם `FIELD_COMMAND_ISSUED` |
| DELETE | `/commands/{id}` | A,O | ביטול `queued` בלבד (409 `command_not_cancellable`) |
| GET | `/heatmap` | A,O,V | `bbox` + `measure=lte_rsrp\|nr_ss_rsrp` חובה; `metric`, `level=100\|500\|2500`; V: יומי, k ≥ 3, בלי `n_devices` |
| GET | `/timeseries` | A,O,V | סדרות מצרפיות; V: `bucket=1d` בלבד, נקודות עם < k מכשירים מושמטות |
| GET | `/breakdown` | A,O,V | `by=operator\|tech\|band\|cell\|group` (group — A,O), `fair=true` = תאים משותפים לכל המפעילים |
| GET | `/dead-zones` | A,O,V | אשכולות 8-שכנים (5.4); V: תאים עם ≥ k מכשירים בלבד |
| GET | `/alerts` · POST `/alerts/{id}/ack` · `/alerts/{id}/resolve` | A,O | |
| GET · PUT | `/alert-rules` · `/alert-rules/{id}` | GET A,O · PUT A | |
| GET/POST · DELETE | `/enrollment-codes` · `/enrollment-codes/{id}` | A | ב-POST בלבד: `code` (מוצג פעם אחת), `qr_payload`, `qr_svg` (segno) |
| GET · POST/PUT/DELETE | `/groups` · `/groups/{id}` | GET A,O · שינוי A | שינוי תצורה מעלה `config_version` לכל מכשירי הקבוצה |
| GET | `/export/measurements.csv` · `.geojson` | A,O | ≤ 31 ימים, ≤ 2M שורות; נרשם `FIELD_EXPORT` |
| GET | `/export/heatmap.geojson` · `/export/kpis.csv` | A,O,V | מצרפי (אותם כללי V) |
| GET | `/audit` | A | רשומות `activity_log` עם `FIELD_*` |
| GET | `/operators` | A,O,V | |
| POST | `/maintenance/run?task=…` | A | הרצה ידנית של משימת התחזוקה |

### KPI ואגרגציות — כללים
- רמות: חצי-פתוח על RSRP (LTE + עוגן NSA) / SS-RSRP (NR SA); OOS/emergency → 0; power_off ו-in_service בלי RSRP → "לא מסווג".
- משקל זמן: `dt = min(הדגימה הבאה של אותו מכשיר+SIM − ts, 600 ש')`; לדגימה האחרונה `min(600, סוף החלון − ts)`; עודף מעל 600 ש' = "לא ידוע".
- טריות (חוזה v2.1 #3): `stale_cell` לא נכנס ל-RSRP/SINR/RSRQ/רמות שירות ולשינויי תא; זמן OOS נספר תמיד.
- `pct_time_by_tier[i]` = רמה i (0 שולי … 4 מצוין), מכנה = זמן מסווג בלבד. RSRP מוחזר תמיד מפוצל `lte` / `nr_sa` / `nsa_nr_leg`.
- KPI (`/summary`, `/breakdown`, `/timeseries`) מחושבים מהגולמי — חלון עד `FIELD_RAW_RETENTION_DAYS`.
  מפת חום ואזורים מתים — מהאגרגציות (שעתי לחלון ≤ 7 ימים ל-A/O, אחרת יומי; V תמיד יומי).
- שכבת LTE במפה ובאזורים מתים כוללת גם דגימות "ללא שירות" (`meas_tech=NONE`) של אותו מפעיל.
- דגימות coarse: רק בגריד 2.5 ק"מ (`level=2500`), לא ב-G100.

### משימת תחזוקה
`run_maintenance()` — `commands` (פקודה שלא נמסרה עד `expires_at` → expired; נמסרה ולא אושרה 24 ש' → expired),
`purge` (withdraw/delete), `aggregate` (תור dirty), `alerts`, `retention` (גולמי 90 יום, heartbeats 30, שעתי 400, פקודות 90).
אידמפוטנטית; נעילת תהליך + `pg_try_advisory_lock` ב-PostgreSQL.
- בתוך ה-API: לולאת רקע ב-lifespan כל `FIELD_MAINTENANCE_INTERVAL_S` (ברירת מחדל 60 ש'; retention כל 6 ש'); לא רצה תחת pytest; 0 = כבוי.
- CLI / cron: `python -m app.scripts.field_maintenance [--task aggregate --task alerts …]`.
- הפעלה ידנית מה-UI: `POST /api/field/maintenance/run` (ADMIN).

### משתני סביבה (field)
| משתנה | ברירת מחדל | תיאור |
|---|---|---|
| FIELD_TOKEN_PEPPER | `CHANGE-ME-FIELD-PEPPER` | **חובה לשנות בפרודקשן** (אזהרה באתחול). שינוי מבטל את כל טוקני המכשירים והקודים |
| FIELD_PUBLIC_BASE_URL | `http://localhost:8000` | `server=` בתוכן ה-QR (https בפרודקשן) |
| FIELD_RAW_RETENTION_DAYS / FIELD_HEARTBEAT_RETENTION_DAYS / FIELD_HOURLY_AGG_RETENTION_DAYS / FIELD_COMMAND_RETENTION_DAYS | 90 / 30 / 400 / 90 | שמירה |
| FIELD_MAX_BATCH_BYTES / FIELD_MAX_BATCH_COMPRESSED_BYTES | 5000000 / 1000000 | תקרות batch (→ 413) |
| FIELD_VIEWER_K_MIN | 3 | k-אנונימיות לצופה |
| FIELD_STALE_CELL_MS / FIELD_MAX_LOC_AGE_MS | 10000 / 30000 | ספי טריות |
| FIELD_COMMAND_TTL_MIN / FIELD_OFFLINE_MIN / FIELD_NO_CONTACT_H | 20 / 15 / 6 | |
| FIELD_RATE_LIMITS_ENABLED | true | |
| FIELD_MAINTENANCE_INTERVAL_S | 60 | לולאת התחזוקה בתוך ה-API (0 = כבוי, אז להריץ את ה-CLI מ-cron) |

### דמו וסימולטור — הרצה מקומית ב-Windows (SQLite של run-local.bat)
`run-local.bat` מגדיר `DATABASE_URL=sqlite:///./local.db` ומריץ את ה-API מתוך `backend`, כך שהמסד הוא `backend\local.db`.
אחרי שהחלון "CoverageOps API :8000" עלה, בחלון `cmd` נוסף:
```bat
cd /d <תיקיית הפרויקט>\backend
set "DATABASE_URL=sqlite:///./local.db"
.venv\Scripts\python -m app.scripts.field_simulator --seed-demo
```
- ממלא ~23 מכשירי דמו (קבוצה **"דמו"**, תוויות "דמו · …"): ORG ו-PERSONAL, שלושת המפעילים (425-01/02/03), LTE + NR_NSA + NR_SA +
  מכשיר 3G, מסלולים על כביש 1, כביש 6, כביש 90 (ערבה — **אזור מת**), חיפה ות"א; מכשיר מושהה, לא מקוון, ניטור מוגבל, ללא קשר,
  טרם התחבר, מיקום משוער (coarse), dual SIM, סוללה חלשה, אובדן נתונים, תצורה ממתינה ופקודה פתוחה; 8 שעות היסטוריה שמסתיימות "עכשיו"
  (`--hours N` לשינוי). **כל הנתונים סינתטיים.**
- אידמפוטנטי: הרצה חוזרת מוחקת את הדמו הקודם ובונה אותו מחדש (ההיסטוריה שוב מסתיימת "עכשיו"). מכשירים אמיתיים לא נפגעים.
- הסרה: `.venv\Scripts\python -m app.scripts.field_simulator --remove-demo` (מוחק את כל המכשירים בקבוצה "דמו" ומחשב מחדש את האגרגציות).
- סימולטור HTTP מול שרת רץ (נרשם דרך קודי צירוף שהוא יוצר כ-ADMIN, שולח batches ב-gzip, heartbeats ו-acks לפקודות; המכשירים
  נכנסים לקבוצה "דמו"):
```bat
.venv\Scripts\python -m app.scripts.field_simulator --server http://localhost:8000 --user adminuser --password <סיסמה> --devices 3 --duration 120 --issue-command
```
  (או `FIELD_SIM_USER` / `FIELD_SIM_PASSWORD`). הסימולטור משתמש בספריה הסטנדרטית בלבד.

---

## משתני סביבה

| משתנה                    | ברירת מחדל                           | תיאור                    |
|--------------------------|--------------------------------------|--------------------------|
| DATABASE_URL             | postgresql://...@db:5432/coverageops | חיבור PostgreSQL          |
| SECRET_KEY               | CHANGE-ME-IN-PRODUCTION              | מפתח JWT                  |
| ALGORITHM                | HS256                                | אלגוריתם JWT              |
| ACCESS_TOKEN_EXPIRE_MINUTES | 480                               | תוקף טוקן (8 שעות)        |
| CORS_ORIGINS             | localhost:3000, localhost:5173       | מקורות CORS מורשים        |
| MAX_ANALYSIS_RADIUS_KM   | 350.0                                | גבול רדיוס ניתוח          |
| COVERAGE_GRID_RESOLUTION | 300                                  | ברירת מחדל של המנוע (קריאה ישירה) |
| TARGET_CELL_M            | 40.0                                 | גודל תא יעד לרזולוציה אדפטיבית (מ') |
| MIN_GRID_RESOLUTION      | 60                                   | מינימום תאים לציר (אדפטיבי) |
| MAX_GRID_RESOLUTION      | 500                                  | מקסימום תאים לציר (גם להרחבת bbox לאתר) |
| TILE_CACHE_SIZE          | 512                                  | גודל מטמון אריחי DEM (~256KB לאריח) |
| MAX_DEM_TILES            | 64                                   | תקציב אריחים לגריד גבהים   |
| TILE_FETCH_WORKERS       | 8                                    | הורדות מקביליות            |
| TILE_FETCH_TIMEOUT_S     | 4.0                                  | timeout לאריח (שניות)      |
| TILE_FETCH_DEADLINE_S    | 20.0                                 | זמן כולל מרבי לשליפת גריד  |
| MAX_DEM_PIXELS           | 100000000                            | תקרת פיקסלים לפסיפס/פלט DEM (→ 413) |
| MAX_BBOX_SPAN_KM         | 400.0                                | אורך צלע מרבי של תא שטח (→ 400) |
| RESULTS_DIR              | /data/results                        | קובצי GeoJSON ישנים (legacy) |

---

## הפעלה — פיתוח

```bash
cd coverageops
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- Swagger docs: http://localhost:8000/api/docs

### Seed מסד נתונים

מתבצע אוטומטית בהפעלה הראשונה. ליצירה ידנית:

```bash
docker compose exec backend python scripts/seed.py
```

---

## הפעלה — פרודקשן

```bash
cp .env.prod.example .env.prod
# ערוך DB_PASSWORD, SECRET_KEY, DOMAIN
docker compose -f docker-compose.prod.yml up -d
```

הפרודקשן כולל Nginx + Certbot (Let's Encrypt) לתעודת SSL אוטומטית.

---

## בדיקות

```bash
docker compose exec backend pytest tests/ -v
# מקומית: pip install -r requirements.txt -r requirements-dev.txt && python -m pytest tests -q
```

הבדיקות מכסות:
- חישוב FSPL, Haversine, דירוג RSSI, גובה אנטנה, Deygout ובליטת כדור הארץ
- ניתוח כיסוי שלם (DTM + DSM), קידוד/פענוח רסטר
- שכבת אריחי DEM (NaN, מטמון, תקציב אריחים)
- דירוג סיכון והמלצות
- API מקצה לקצה (`test_api_coverage.py`): SQLite בזיכרון + DEM סינתטי —
  שמירת רסטר, רזולוציה אדפטיבית, PNG/GeoJSON/דוח, אתר מחוץ ל-bbox (רכס בין האתר לשטח
  מוריד RSSI), DEM חסר → אזהרה, שורות legacy, מיגרציות
- קליטה בשטח (`test_field_*.py`): וקטורי רמות משותפים, כל ממשקי המכשיר וקודי השגיאה, אידמפוטנטיות, סינון טריות,
  סטטוס חי עם שעון מוזרק, KPI על נתונים סינתטיים עם תשובה ידועה, מגבלות VIEWER, מחזור חיי פקודה, withdraw/מחיקה/אנונימיזציה,
  יומן ביקורת, זריעת הדמו (פעמיים בלי כפילות) ומיגרציות
- קישור נקודתי (`test_api_link.py`): משדר מאתר/אתר סלולר, פרופיל מקלט ודריסות, הרשאות, 400/413,
  שמירה/רשימה/מחיקה, דוח Word, "מי משרת" (דירוג, סינון מפעיל/רדיוס/small_indoor, ביצועים < 5 ש')

---

## נתוני טרן

המערכת משתמשת בנתוני גבהים אמיתיים (Terrarium, ~38 מ'/פיקסל בזום 12) ובמודל עקיפה
ITU-R P.526 Deygout. כשהנתונים אינם זמינים התוצאה מסומנת בבירור (`dem_source="none"`
+ אזהרה) ואין להסתמך עליה מבצעית.
