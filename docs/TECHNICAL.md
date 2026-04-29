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
│                    ┌────┴──────────────────────┐        │
│                    │  /data/results  (GeoJSON) │        │
│                    └───────────────────────────┘        │
└─────────────────────────────────────────────────────────┘
```

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
│   │   │   └── security.py      # JWT, bcrypt, role-based deps
│   │   ├── models/
│   │   │   └── user.py          # ORM: User, Site, CoverageResult, ActivityLog
│   │   ├── api/
│   │   │   ├── auth.py          # /api/auth/* — login, me
│   │   │   ├── users.py         # /api/users/* — admin CRUD
│   │   │   ├── sites.py         # /api/sites/* — site management
│   │   │   └── coverage.py      # /api/coverage/* — analysis engine
│   │   └── services/
│   │       ├── coverage_engine.py   # RF propagation model
│   │       ├── elevation.py         # Terrarium tile elevation lookup
│   │       └── report_generator.py  # Word (.docx) report generation
│   ├── scripts/
│   │   └── seed.py              # Idempotent DB seeder (dev users)
│   ├── tests/
│   │   └── test_coverage.py     # Unit + integration tests
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
| elevation_m | FLOAT       | metres ASL, auto-fetched       |
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
| covered_pct  | FLOAT      | % of bbox with signal        |
| rssi_avg/max/min | FLOAT  | aggregate RSSI stats (dBm)   |
| geojson_path | VARCHAR    | path to GeoJSON file on disk |
| duration_sec | FLOAT      | computation time             |
| created_at   | DATETIME   |                              |

### activity_log
אוגר כניסות מוצלחות ונכשלות. לא נחשף ב-API נוכחי.

---

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
| GET    | /result/{id}                | JWT            | תוצאה + GeoJSON מוטמע           |
| DELETE | /result/{id}                | ADMIN/OPERATOR | מחיקת תוצאה                     |
| GET    | /result/{id}/report         | JWT            | דוח מלא עם סיכון + המלצות       |
| POST   | /cross-section              | ADMIN/OPERATOR | ניתוח חתך רדיו (JSON)            |
| POST   | /cross-section/docx         | ADMIN/OPERATOR | ניתוח חתך רדיו (Word)            |
| GET    | /topo-overlay               | ללא            | שכבת גבהים PNG דינמית           |

---

## מנוע הפצה (coverage_engine.py)

### מודל הפצה

```
RSSI (dBm) = Tx_power − FSPL(d, f) − TerrainLoss + HeightGain
```

**FSPL** (Free Space Path Loss):
```
FSPL = 20·log₁₀(d_m) + 20·log₁₀(f_Hz) − 147.55
```

**HeightGain**:
```
HeightGain = 20·log₁₀(h_ant / 1.5)
```

**TerrainLoss** (סימולציה נוכחית):
- DTM: 0–15 dB אקראי דטרמיניסטי (seed לפי מיקום האתר)
- DSM: DTM + 0–20 dB נוסף לחסימות עירוניות

> לדיוק מבצעי: יש להחליף ב-SRTM DEM + מודל Longley-Rice/ITM

### דירוג RSSI

| רמה      | טווח (dBm)        |
|---------|-------------------|
| excellent| > −70            |
| good    | −70 עד −80        |
| medium  | −80 עד −90        |
| weak    | −90 עד −100       |
| marginal| −100 עד −110      |
| none    | < −110            |

### רזולוציית גריד

- ברירת מחדל: 300×300 = 90,000 תאים
- מקסימום: 500×500 = 250,000 תאים
- ניתן לשינוי ב-`config.py` → `COVERAGE_GRID_RESOLUTION`

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
| COVERAGE_GRID_RESOLUTION | 300                                  | נקודות גריד לציר          |
| RESULTS_DIR              | /data/results                        | תיקיית GeoJSON            |

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
- חישוב FSPL
- מרחק Haversine
- דירוג RSSI
- גובה אנטנה
- ניתוח כיסוי שלם (DTM + DSM)

---

## שדרוג לנתוני טרן אמיתיים

כרגע המערכת משתמשת בסימולציה דטרמיניסטית (pseudo-terrain).
להחלפה בנתוני SRTM אמיתיים:

1. הורד tiles של SRTM ל-`/data/srtm`
2. ב-`coverage_engine.py` החלף את `terrain_loss_db()` בחישוב LOS אמיתי
   (Knife-Edge Diffraction / ITM Longley-Rice)
3. הגדר `SRTM_DATA_DIR` בקובץ הסביבה
