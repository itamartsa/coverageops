"""
End-device system requirements for the CoverageOps Field Probe app, as served by
GET /api/field/meta/device-requirements.

Derived ONLY from docs/field/ANDROID_APP_SPEC.md (section references in
`source`). Items the Android spec itself marks [לאימות] are returned with
status "לאימות" — they are not confirmed requirements.
"""

VERIFIED = "מאומת באפיון"
TO_VERIFY = "לאימות"

DEVICE_REQUIREMENTS = {
    "title_he": "דרישות מערכת למכשיר קצה — אפליקציית CoverageOps Field Probe",
    "headline_he": "Android 11 (API 30) ומעלה",
    "spec_document": "docs/field/ANDROID_APP_SPEC.md",
    "status_legend_he": {
        VERIFIED: "דרישה שנקבעה באפיון האנדרואיד",
        TO_VERIFY: "האפיון מסמן את הפריט [לאימות] — טרם נבדק במכשיר/בתיעוד (ייבדק בספייק M0)",
    },
    "sections": [
        {
            "key": "os",
            "title_he": "מערכת הפעלה",
            "items": [
                {"text_he": "Android 11 (API 30) ומעלה — זו גרסת המינימום (minSdk 30).",
                 "status": VERIFIED, "source": "2.1",
                 "note_he": "נדרש לזיהוי 5G NSA (TelephonyDisplayInfo), לפסי תדר ולזמן מדידת התא (API 30)."},
                {"text_he": "Android 10 ומטה אינו נתמך.", "status": VERIFIED, "source": "2.1"},
                {"text_he": "האפליקציה נבנית מול Android 16 (targetSdk 36); מעבר ל-API 37 ייבחן בהמשך.",
                 "status": TO_VERIFY, "source": "2.1"},
                {"text_he": "גובה מעל פני הים (altitude_msl_m) ו-Timing Advance של NR זמינים רק מ-Android 14 "
                            "(API 34); ב-Android 11–13 הם ריקים.",
                 "status": VERIFIED, "source": "3.2, 13.2 #4"},
            ],
        },
        {
            "key": "hardware",
            "title_he": "חומרה ושירותים",
            "items": [
                {"text_he": "רכיב טלפוניה (מודם סלולרי) עם SIM פעיל — חובה (android.hardware.telephony).",
                 "status": VERIFIED, "source": "5.1"},
                {"text_he": "Dual SIM נתמך: נאספת דגימה לכל SIM פעיל.", "status": VERIFIED, "source": "3.1"},
                {"text_he": "שיוך נתוני תא ל-SIM במכשירי dual SIM (Qualcomm / MediaTek).",
                 "status": TO_VERIFY, "source": "3.1"},
                {"text_he": "GPS אינו חובה (android.hardware.location.gps — required=false); נדרש שירות מיקום פעיל.",
                 "status": VERIFIED, "source": "5.1, 5.2"},
                {"text_he": "מצלמה אינה חובה — משמשת רק לסריקת QR; אפשר להזין קוד צירוף ידנית.",
                 "status": VERIFIED, "source": "5.1, 5.2"},
                {"text_he": "Google Play services אינם חובה: בהיעדרם המיקום נלקח מ-LocationManager "
                            "(למשל Huawei ללא GMS).",
                 "status": VERIFIED, "source": "2.3, 3.5"},
                {"text_he": "התנהגות במכשירי Huawei ללא GMS ובשבבי MediaTek/Unisoc (נתוני תא חסרים ל-SIM שני).",
                 "status": TO_VERIFY, "source": "3.4, 3.5"},
            ],
        },
        {
            "key": "permissions",
            "title_he": "הרשאות",
            "items": [
                {"text_he": "מיקום מדויק (FINE + COARSE) — חובה ב-ORG וב-PERSONAL; אנדרואיד דורש אותו לקריאת "
                            "זהות התא גם כשמצב המיקום בפועל משוער/כבוי.",
                 "status": VERIFIED, "source": "3.1, 5.2 #1"},
                {"text_he": "מצב טלפון (READ_PHONE_STATE) — חובה.", "status": VERIFIED, "source": "5.2 #2"},
                {"text_he": "התראות (POST_NOTIFICATIONS, Android 13+) — חובה; ב-PERSONAL בלעדיה הניטור לא מתחיל.",
                 "status": VERIFIED, "source": "5.2 #3"},
                {"text_he": "מיקום ברקע (\"לאפשר כל הזמן\") — חובה ב-ORG, אופציונלי ב-PERSONAL.",
                 "status": VERIFIED, "source": "5.2 #4"},
                {"text_he": "מצלמה — רק בעת סריקת QR.", "status": VERIFIED, "source": "5.2"},
                {"text_he": "האם TelephonyDisplayInfo דורש READ_PHONE_STATE בכל הגרסאות.",
                 "status": TO_VERIFY, "source": "3.1"},
            ],
        },
        {
            "key": "battery",
            "title_he": "סוללה ורקע",
            "items": [
                {"text_he": "פטור מאופטימיזציית סוללה — חובה ב-ORG (בלעדיו: ניטור מוגבל), אופציונלי ב-PERSONAL.",
                 "status": VERIFIED, "source": "5.2 #5"},
                {"text_he": "הגדרות יצרן (Autostart / Sleeping apps וכו') ב-Xiaomi, Samsung, Huawei, Oppo, Vivo — "
                            "לפי מדריך באפליקציה.",
                 "status": VERIFIED, "source": "5.2 #6, 5.5"},
                {"text_he": "הפעלת השירות מהרקע (Android 12+) מותרת רק עם פטור סוללה.",
                 "status": TO_VERIFY, "source": "5.3"},
                {"text_he": "תקציב סוללה: יעד ≤ 2.5%/שעה בתנועה ו-≤ 0.5%/שעה בעמידה (ORG) — הערכה, לא מדידה.",
                 "status": TO_VERIFY, "source": "4.5"},
            ],
        },
        {
            "key": "connectivity",
            "title_he": "קישוריות",
            "items": [
                {"text_he": "חיבור HTTPS (TLS 1.2+) לשרת CoverageOps; כתובות שאינן https נדחות בגרסת production.",
                 "status": VERIFIED, "source": "7.1, 9.1"},
                {"text_he": "שירותי המיקום של המערכת דלוקים.", "status": VERIFIED, "source": "3.1, 5.2"},
                {"text_he": "האם בדיקת \"שירותי מיקום דלוקים\" מתנהגת זהה בכל הגרסאות.",
                 "status": TO_VERIFY, "source": "3.1"},
                {"text_he": "אין צורך ב-Google Play / FCM: פקודות מגיעות בתשובת ה-heartbeat (עד ~5 דק' במצב פעיל).",
                 "status": VERIFIED, "source": "1.3, 4.3"},
                {"text_he": "העבודה offline-first: ללא רשת הדגימות נשמרות במכשיר ומועלות כשהרשת חוזרת "
                            "(ברשת סלולרית עד 20 מנות ברצף).",
                 "status": VERIFIED, "source": "6.2, 6.6"},
                {"text_he": "אחסון פנוי: התור מוגבל ל-150,000 דגימות או 120MB; פחות מ-500MB פנויים → התקרה יורדת ל-20MB.",
                 "status": VERIFIED, "source": "6.5"},
            ],
        },
        {
            "key": "distribution",
            "title_he": "הפצה",
            "items": [
                {"text_he": "התקנה כ-APK ישיר או דרך MDM — לא דרך Google Play בשלב ראשון.",
                 "status": VERIFIED, "source": "חוזה, 1.3"},
            ],
        },
        {
            "key": "unsupported",
            "title_he": "מקרים שאינם נתמכים",
            "items": [
                {"text_he": "Android 10 ומטה.", "status": VERIFIED, "source": "2.1"},
                {"text_he": "מכשיר ללא רכיב טלפוניה / ללא SIM (למשל טאבלט Wi-Fi בלבד).", "status": VERIFIED,
                 "source": "5.1"},
                {"text_he": "iOS.", "status": VERIFIED, "source": "1.4"},
                {"text_he": "מצב טיסה אינו תקלה — הדגימות מסומנות \"לא מסווג\".", "status": VERIFIED,
                 "source": "5.6"},
                {"text_he": "רשתות 2G/3G בישראל ייתכן שכבר כבויות — מסלול הדגימה הזה כמעט לא ירוץ.",
                 "status": TO_VERIFY, "source": "3.2"},
            ],
        },
    ],
}
