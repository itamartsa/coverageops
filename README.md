# COVIRADYX

> Mission-Critical Connectivity Intelligence — (שם קודם: CoverageOps; שם הריפו והשירותים נשאר `coverageops`)

**מערכת ניתוח כיסוי סלולרי מבצעי**  
Web SaaS לקציני קשר – ניתוח כיסוי אות מאתר אנטנה לתא שטח, עם תמיכה בטופוגרפיה ותכסית מבנים.

---

## 🗂 מבנה הפרויקט

```
coverageops/
├── backend/                   # Python FastAPI
│   ├── app/
│   │   ├── main.py            # Entry point
│   │   ├── core/
│   │   │   ├── config.py      # הגדרות + env
│   │   │   ├── database.py    # SQLAlchemy session
│   │   │   └── security.py    # JWT + bcrypt + RBAC
│   │   ├── models/user.py     # ORM: User, Site, CoverageResult, Log
│   │   ├── api/
│   │   │   ├── auth.py        # /api/auth/*
│   │   │   ├── sites.py       # /api/sites/*
│   │   │   ├── coverage.py    # /api/coverage/*
│   │   │   └── users.py       # /api/users/*
│   │   ├── services/
│   │   │   ├── coverage_engine.py  # מנוע הפצה: FSPL + Deygout (k=4/3) + רסטר RSSI
│   │   │   ├── terrain.py          # אריחי DEM (Terrarium) → גריד גבהים
│   │   │   ├── elevation.py        # גובה נקודה
│   │   │   ├── risk.py             # דירוג סיכון + המלצות
│   │   │   └── report_generator.py # דוח Word לחתך
│   │   └── scripts/seed.py    # משתמשי ברירת מחדל
│   ├── tests/                 # pytest: engine, terrain, risk, API
│   ├── requirements.txt
│   ├── Dockerfile
│   └── alembic.ini
│
├── frontend/                  # React + TypeScript + Vite
│   ├── src/
│   │   ├── main.tsx           # React entry + Router
│   │   ├── index.css          # CSS variables + globals
│   │   ├── pages/
│   │   │   ├── LoginPage.tsx  # מסך כניסה
│   │   │   └── MapPage.tsx    # מסך מפה ראשי
│   │   ├── components/
│   │   │   ├── TopBar.tsx     # סרגל עליון
│   │   │   ├── Sidebar.tsx    # סרגל צד + tabs
│   │   │   ├── SitesList.tsx  # רשימת אתרים
│   │   │   ├── SiteForm.tsx   # הוספת אתר חדש
│   │   │   ├── AnalysisPanel.tsx # ניתוח כיסוי
│   │   │   ├── MapView.tsx    # Leaflet map
│   │   │   └── Notification.tsx  # Toast notifications
│   │   ├── services/api.ts    # Axios + כל קריאות HTTP
│   │   └── store/useStore.ts  # Zustand global state
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── Dockerfile
│   └── nginx-spa.conf
│
├── infra/
│   └── nginx.conf             # Reverse proxy (production)
│
├── docker-compose.yml
├── Makefile
├── .env.example
└── README.md
```

---

## ⚡ הפעלה מהירה (Development)

### דרישות מקדימות
- Docker Desktop (או Docker Engine + Compose)
- Git

### שלבים

```bash
# 1. שכפל את הפרויקט
git clone https://github.com/your-org/coverageops.git
cd coverageops

# 2. צור קובץ .env
cp .env.example .env
# ערוך .env – שנה לפחות את SECRET_KEY ו-POSTGRES_PASSWORD

# 3. הפעל (build + run)
make dev
# או ידנית:
# docker compose up --build

# 4. פתח בדפדפן
# Frontend:  http://localhost:3000
# API Docs:  http://localhost:8000/api/docs
```

### משתמשי ברירת מחדל (נוצרים אוטומטית)

| משתמש     | סיסמה      | תפקיד    |
|-----------|-----------|---------|
| admin     | Admin1234! | ADMIN   |
| operator1 | Ops1234!  | OPERATOR|
| viewer1   | View1234! | VIEWER  |

---

## 🚀 פריסה לייצור (Production)

### 1. הכן שרת (Ubuntu 22.04 מומלץ)
```bash
# התקן Docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker $USER
```

### 2. הגדר DNS
```
A record: coverageops.yourdomain.com → <SERVER_IP>
```

### 3. SSL עם Let's Encrypt
```bash
apt install certbot
certbot certonly --standalone -d coverageops.yourdomain.com
mkdir -p infra/ssl
cp /etc/letsencrypt/live/coverageops.yourdomain.com/fullchain.pem infra/ssl/
cp /etc/letsencrypt/live/coverageops.yourdomain.com/privkey.pem   infra/ssl/
```

### 4. עדכן .env לייצור
```env
SECRET_KEY=<64+ random chars>
POSTGRES_PASSWORD=<strong password>
CORS_ORIGINS=["https://coverageops.yourdomain.com"]
VITE_API_URL=https://coverageops.yourdomain.com
```

### 5. הפעל
```bash
make prod
# או:
docker compose --profile production up --build -d
```

---

## 🗺 API Reference

### Auth
| Method | Path                  | תיאור              |
|--------|-----------------------|--------------------|
| POST   | `/api/auth/login`     | כניסה, מחזיר JWT   |
| POST   | `/api/auth/register`  | הוספת משתמש (ADMIN)|
| GET    | `/api/auth/me`        | פרטי המשתמש הנוכחי|

### Sites
| Method | Path                  | תיאור               |
|--------|-----------------------|---------------------|
| GET    | `/api/sites/`         | רשימת אתרים         |
| POST   | `/api/sites/`         | יצירת אתר חדש       |
| GET    | `/api/sites/{id}`     | פרטי אתר            |
| PUT    | `/api/sites/{id}`     | עדכון אתר           |
| DELETE | `/api/sites/{id}`     | מחיקת אתר           |

### Coverage
| Method | Path                                   | תיאור              |
|--------|----------------------------------------|--------------------|
| POST   | `/api/coverage/analyze`                | הרצת ניתוח כיסוי (שומר רסטר RSSI; `resolution` אופציונלי = אדפטיבי) |
| GET    | `/api/coverage/`                       | 100 ניתוחים אחרונים |
| GET    | `/api/coverage/history/{site_id}`      | היסטוריית ניתוחים לאתר |
| GET    | `/api/coverage/result/{id}`            | סטטיסטיקה + איכות נתונים + אזהרות (ללא GeoJSON) |
| GET    | `/api/coverage/result/{id}/png`        | שכבת PNG מהרסטר (`width`, `height`) על ה-bbox השמור |
| GET    | `/api/coverage/result/{id}/geojson`    | הורדת GeoJSON (נבנה לפי דרישה) |
| GET    | `/api/coverage/result/{id}/report`     | דוח: סיכון, אשכולות אפלה, `data_quality`, המלצות |
| DELETE | `/api/coverage/result/{id}`            | מחיקת תוצאה |
| POST   | `/api/coverage/cross-section`          | חתך רדיו לאורך ציר (JSON) |
| POST   | `/api/coverage/cross-section/docx`     | חתך רדיו – דוח Word |
| GET    | `/api/coverage/point-elevation`        | גובה נקודה (`null` אם אין נתונים) |
| GET    | `/api/coverage/topo-overlay`           | שכבת טופוגרפיה PNG |

שדות עיקריים בתוצאת ניתוח: `covered_pct` (% מעל סף הקבלה), `covered_pct_any_signal`
(% עם אות כלשהו > −110 dBm), `threshold_dbm`, `weak_pct`, `level_distribution`,
`dem_source` (`terrarium`/`none`), `dem_valid_pct`, `dem_m_per_px`, `resolution`, `model`,
`diffraction_method`, `warnings` (עברית). פירוט מלא: `docs/TECHNICAL.md`.

**דוגמה – הרצת ניתוח:**
```bash
curl -X POST http://localhost:8000/api/coverage/analyze \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "site_id": 1,
    "mode": "DTM",
    "sw_lat": 31.72, "sw_lon": 35.15,
    "ne_lat": 31.82, "ne_lon": 35.30
  }'
# ללא "resolution" → רזולוציה אדפטיבית (~40 מ' לתא, 60–500 תאים לציר)
```

---

## 🧪 בדיקות

```bash
# הרץ tests בתוך container
make test

# או locally (עם Python env):
cd backend
pip install -r requirements.txt
pytest tests/ -v
```

---

## 📡 מנוע חישוב כיסוי

```
RSSI = Tx − FSPL(d, f) − DiffractionLoss − Clutter(DSM) + HeightGain
```

- **נתוני גבהים אמיתיים** – אריחי Terrarium (זום 8–12 לפי גודל השטח), מטמון בזיכרון,
  הורדה מקבילית. אריח חסר → NaN (שטח לא ידוע, לא "אפס"); אין נתונים כלל →
  סימולציה המסומנת `dem_source="none"` עם אזהרה.
- **עקיפה** – ITU-R P.526 Deygout (עד 3 שפות) על כדור ארץ מעוקל (k = 4/3).
- **אחוז כיסוי** – לפי סף הקבלה של האתר (`rx_threshold`), לא "כל אות".
- **רזולוציה אדפטיבית** – יעד 40 מ' לתא, לא עדין מה-DEM, 60–500 תאים לציר.
- **אתר מחוץ לתא השטח** – הטופוגרפיה שבין האתר לשטח נכללת (החישוב רץ על שטח מורחב
  ונחתך חזרה).
- **אחסון** – רסטר RSSI דחוס ב-DB; PNG / GeoJSON / דוח נגזרים ממנו לפי דרישה.
- **סיכון והמלצות** – `services/risk.py`, מקור אמת יחיד לניתוח שטחי ולחתך.

---

## 🔐 אבטחה

- JWT HS256 עם תפוגה של 8 שעות
- bcrypt לסיסמאות (cost factor 12)
- RBAC: ADMIN / OPERATOR / VIEWER
- לוג פעילות לכל כניסה ופעולה
- HTTPS בסביבת ייצור (Let's Encrypt)
- Security headers דרך Nginx

---

## 🗓 מפת דרכים

| פאזה | תכולה                              | סטטוס    |
|------|------------------------------------|----------|
| 1    | Login, Sites CRUD, Coverage FSPL   | ✅ הושלם  |
| 2    | DEM אמיתי (Terrarium) + Deygout, רסטר, ייצוא PNG/GeoJSON | ✅ הושלם  |
| 3    | Signal-Server, השוואת אתרים       | 📋 מתוכנן|
| 4    | ניהול ארגוני, API חיצוני          | 📋 מתוכנן|

---

## 📞 תמיכה

לשאלות ובאגים – פתח Issue בGitHub.
