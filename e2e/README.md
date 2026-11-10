# בדיקות קצה-לקצה (E2E) — CoverageOps

חבילת בדיקות [Playwright](https://playwright.dev) שמפעילה את המערכת המלאה — שרת FastAPI
(עם מסד SQLite זמני) ושרת הפיתוח של Vite — ומריצה את ממשק המשתמש האמיתי בדפדפן Chromium.

## הרצה מקומית ב-Windows

דרישות: Node.js 18 ומעלה, Python עם תלויות ה-backend מותקנות
(`cd backend && python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`),
ותלויות ה-frontend מותקנות (`cd frontend && npm install`).

```bat
cd e2e
npm install
npx playwright install chromium
npx playwright test
```

לצפייה בדוח ה-HTML לאחר ההרצה:

```bat
npx playwright show-report
```

פקודות נוספות:

| פקודה | מה היא עושה |
|---|---|
| `npm test` | הרצת כל הבדיקות (זהה ל-`npx playwright test`) |
| `npm run test:headed` | הרצה עם חלון דפדפן גלוי |
| `npm run report` | פתיחת דוח ה-HTML האחרון |
| `npx playwright test tests/analysis.spec.ts` | הרצת קובץ בדיקות אחד |
| `npx playwright test -g "נקה תוצאות"` | הרצת בדיקות לפי שם |
| `npx playwright test --ui` | מצב אינטראקטיבי (צפייה, הרצה חוזרת, trace) |

## מה קורה בהרצה

- `playwright.config.ts` מפעיל שני שרתים (`webServer`):
  1. **backend** — `scripts/start-backend.cjs`: מוחק את התיקייה הזמנית
     (`%TEMP%\coverageops-e2e` ב-Windows), יוצר מסד SQLite חדש, מריץ את `app.scripts.seed`,
     זורע את צי הדמו של "קליטה בשטח" (`python -m app.scripts.field_simulator --seed-demo` — כ-23 מכשירים
     סינתטיים "דמו · …" עם 8 שעות היסטוריה, התראות ואגרגציות) ומפעיל `uvicorn` על פורט 8000. ב-Windows נעשה שימוש ב-`backend\.venv\Scripts\python.exe`
     אם הוא קיים, אחרת ב-`python` שב-PATH (ניתן לעקוף עם משתנה הסביבה `E2E_PYTHON`).
  2. **frontend** — `npx vite --port 3000 --strictPort` עם `VITE_API_URL=http://localhost:8000`.
- אם השרתים כבר רצים (מחוץ ל-CI) — Playwright משתמש בהם (`reuseExistingServer`), ו-`global-setup.ts`
  מנקה שאריות של הרצות קודמות (אתרים ששמם מתחיל ב-`E2E`, משתמשים ופרופילים שמתחילים ב-`e2e_`)
  ומוודא שמשתמשי הבדיקה קיימים.
- עובד אחד (`workers: 1`) — כל הבדיקות חולקות מסד נתונים אחד. כל בדיקה יוצרת את הנתונים שלה
  (בעיקר דרך ה-API) עם שמות ייחודיים, ולכן כל בדיקה עצמאית.
- צילום מסך ו-trace נשמרים רק לבדיקה שנכשלה (`test-results/`); הדוח נשמר ב-`playwright-report/`.

## משתמשי בדיקה

| משתמש | סיסמה | תפקיד |
|---|---|---|
| adminuser | 12345678 | ADMIN |
| operator1 | Ops1234! | OPERATOR |
| viewer1 | View1234! | VIEWER |

## מבנה

```
e2e/
├── playwright.config.ts      # שרתים, דפדפן, דוחות, timeouts
├── global-setup.ts           # משתמשי בדיקה + ניקוי שאריות
├── scripts/
│   ├── env.cjs               # נתיבים, פורטים, בחירת python
│   └── start-backend.cjs     # מסד זמני + seed + uvicorn
├── support/
│   ├── fixtures.ts           # guard (שגיאות דף/קונסול/5xx), api, loginAs, ui
│   ├── api.ts                # לקוח REST להזנת נתונים מהירה
│   ├── map.ts                # המרת lat/lon ↔ נקודה על המסך, שרטוט מלבן, ספירת שכבות
│   ├── field.ts              # /field: פתיחת לשונית, מכשירי דמו, יומן ביקורת, בדיקת גלילה אופקית
│   ├── analysis.ts / history.ts / cellsites.ts / form.ts
└── tests/
    ├── auth.spec.ts          # כניסה/יציאה, נתיבים מוגנים, הרשאות לפי תפקיד
    ├── sites.spec.ts         # הקמה (כולל אימות), עריכה, מחיקה, תגיות
    ├── analysis.spec.ts      # אשף הניתוח, ריבוי אתרים, רגרסיית "נקה תוצאות" / "אפס ובחר חדש"
    ├── layers.spec.ts        # שכבות כיסוי, DEM, אתרי סלולר, סינון, פאנל מידע
    ├── cellsite-popup.spec.ts# חלון אתר סלולרי: גובה, RF, "נתח כיסוי", "קישור למכשיר"
    ├── cross-section.spec.ts # חתך רדיו + דוח Word
    ├── history.spec.ts       # היסטוריה: טעינה למפה, דוח, GeoJSON, מחיקה
    ├── link.spec.ts          # קישור נקודתי + "מי משרת את המכשיר"
    ├── admin.spec.ts         # פרופילי מכשיר ומשתמשים
    ├── smoke.spec.ts         # כל הלשוניות לכל תפקיד + בדיקת גלאי השגיאות
    ├── field.spec.ts         # קליטה בשטח: מכשיר + פקודה + מסלול, קוד צירוף + QR, דרישות מכשיר,
    │                         #   התראות, צופה (מצרפי בלבד), 390px, שכבת "מדידות שטח" במפה הראשית
    └── field-tiers.spec.ts   # lib/fieldTiers.ts מול /api/field/meta/tiers ו-field_tier_vectors.json
```

## הערות

- **ללא אינטרנט** — הבדיקות לא תלויות באריחי מפה או בנתוני גובה (DEM) חיצוניים. כשאין גישה
  לאריחי Terrarium התוצאות מחושבות על טרן מדומה (`dem_source: "none"`), והבדיקות מוודאות
  שמוצגת אזהרת "טרן מדומה". חסימת אריחי מפת הרקע מותרת ברשימת ההיתרים של ה-guard.
- **גישה למפה** — בבניית פיתוח בלבד (`import.meta.env.DEV`) הרכיב `MapView` חושף את מופע ה-Leaflet
  כ-`window.__coverageopsMap`, כדי שהבדיקות יוכלו לחשב היכן ללחוץ (סמני אתרי הסלולר מצוירים
  על canvas ואין להם אלמנטים ב-DOM). בבניית ייצור המשתנה אינו קיים.
- **שגיאות** — ה-fixture `guard` מכשיל בדיקה על כל שגיאת JavaScript לא מטופלת, `console.error`
  (פרט לבקשות חיצוניות חסומות) או תשובת API עם סטטוס 500 ומעלה. בדיקה שמצפה לשגיאה
  (למשל 401 בכניסה כושלת) מצהירה על כך עם `guard.allow(/.../)`.
- **פורטים** — ניתן לשנות עם `E2E_API_PORT` / `E2E_WEB_PORT`. שימו לב שה-frontend פונה ל-API
  לפי `VITE_API_URL`, וה-backend מתיר CORS רק מ-`localhost:3000` כברירת מחדל.
