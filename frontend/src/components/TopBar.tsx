import { useNavigate } from "react-router-dom";
import { useStore } from "../store/useStore";
import styles from "./TopBar.module.css";

export default function TopBar() {
  const { user, logout } = useStore();
  const navigate = useNavigate();

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

      {user?.role === "ADMIN" && (
        <button className={styles.adminBtn} onClick={() => navigate("/admin")}>
          ⚙️ ניהול משתמשים
        </button>
      )}

      <div className={styles.userInfo}>
        <div>
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
