import { useLocation, useNavigate } from "react-router-dom";
import { useStore } from "../store/useStore";
import { canSeeDevices } from "../lib/roles";
import { badgeCount, useOpenAlerts } from "../lib/fieldQueries";
import styles from "./TopBar.module.css";

export default function TopBar() {
  const { user, logout } = useStore();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const onField = pathname.startsWith("/field");
  // Open-alerts badge: ADMIN/OPERATOR only — alerts identify devices, VIEWER has no access (spec 7.3, 9.4)
  const showAlerts = canSeeDevices(user?.role);
  const alerts = useOpenAlerts(showAlerts);
  const nAlerts = badgeCount(alerts.data);

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <header className={styles.bar}>
      <div className={styles.logo}>Coverage<span>Ops</span></div>
      <div className={styles.sep} />
      <div className={styles.status}>
        <span className={styles.dot} />
        מערכת פעילה
      </div>

      <div className={styles.spacer} />

      {onField && (
        <button className={styles.navBtn} onClick={() => navigate("/")} title="חזרה למפת הכיסוי">
          <span aria-hidden="true">🗺</span><span className={styles.lbl}>מפת כיסוי</span>
        </button>
      )}
      <button
        className={`${styles.navBtn} ${onField ? styles.navBtnActive : ""}`}
        onClick={() => navigate("/field")}
        aria-current={onField ? "page" : undefined}
        title="קליטה בשטח — דאשבורד מכשירי שטח"
        data-testid="nav-field"
      >
        <span aria-hidden="true">📶</span><span className={styles.lbl}>קליטה בשטח</span>
        {showAlerts && nAlerts > 0 && (
          <span className={styles.alertBadge} aria-label={`${nAlerts} התראות פתוחות (אזהרה/קריטי)`}
            data-testid="field-alert-badge">{nAlerts}</span>
        )}
      </button>

      {user?.role === "ADMIN" && (
        <button className={styles.adminBtn} onClick={() => navigate("/admin")} title="ניהול משתמשים">
          <span aria-hidden="true">⚙️</span><span className={styles.lbl}> ניהול משתמשים</span>
        </button>
      )}

      <div className={styles.userInfo}>
        <div className={styles.userText}>
          <div className={styles.userName}>{user?.full_name}</div>
          <div className={styles.userRole}>{user?.role}</div>
        </div>
        <div className={styles.avatar}>{user?.full_name?.[0] ?? "מ"}</div>
      </div>

      <button className={styles.logoutBtn} onClick={handleLogout}>
        יציאה
      </button>
    </header>
  );
}
