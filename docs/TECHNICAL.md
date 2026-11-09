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
| rssi_min_all | FLOAT      | RSSI מינימלי על **כל** התאים ברדיוס (כולל ללא אות) — קלט לדירוג הסיכון; בשורות ישנות NULL → נעשה שימוש ב-rssi_min |
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
אוגר כניסות מוצלחות ונכשלות. לא נחשף ב-API נוכחי.

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

| רמה      | covered_pct | אזור מת ארוך (ק"מ) | weak_pct | rssi_min (dBm) |
|----------|-------------|--------------------|----------|----------------|
| CRITICAL | < 50        | > 3                | > 30     | < −115         |
| HIGH     | < 70        | > 2                | > 20     | < −110         |
| MEDIUM   | < 85        | > 1                | > 10     | < −105         |
| LOW      | אחרת        |                    |          |                |

### רזולוציית גריד

- אדפטיבית כברירת מחדל: יעד `TARGET_CELL_M` (40 מ') לתא, לא עדין מה-DEM,
  בטווח `MIN_GRID_RESOLUTION`–`MAX_GRID_RESOLUTION` (60–500).
- ערך מפורש מהלקוח: 20–500.

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
```

הבדיקות מכסות:
- חישוב FSPL, Haversine, דירוג RSSI, גובה אנטנה, Deygout ובליטת כדור הארץ
- ניתוח כיסוי שלם (DTM + DSM), קידוד/פענוח רסטר
- שכבת אריחי DEM (NaN, מטמון, תקציב אריחים)
- דירוג סיכון והמלצות
- API מקצה לקצה (`test_api_coverage.py`): SQLite בזיכרון + DEM סינתטי —
  שמירת רסטר, רזולוציה אדפטיבית, PNG/GeoJSON/דוח, אתר מחוץ ל-bbox (רכס בין האתר לשטח
  מוריד RSSI), DEM חסר → אזהרה, שורות legacy, מיגרציות

---

## נתוני טרן

המערכת משתמשת בנתוני גבהים אמיתיים (Terrarium, ~38 מ'/פיקסל בזום 12) ובמודל עקיפה
ITU-R P.526 Deygout. כשהנתונים אינם זמינים התוצאה מסומנת בבירור (`dem_source="none"`
+ אזהרה) ואין להסתמך עליה מבצעית.
