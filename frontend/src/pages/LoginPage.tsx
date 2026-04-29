import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { authApi } from "../services/api";
import { useStore } from "../store/useStore";
import styles from "./LoginPage.module.css";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError]       = useState("");
  const [loading, setLoading]   = useState(false);
  const { setAuth } = useStore();
  const navigate = useNavigate();

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(""); setLoading(true);
    try {
      const data = await authApi.login(username, password);
      setAuth(data.access_token, {
        id: data.user_id,
        username: data.username,
        full_name: data.full_name,
        role: data.role,
      });
      navigate("/");
    } catch (err: any) {
      setError(err.response?.data?.detail || "שגיאת חיבור לשרת");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.screen}>
      <div className={styles.box}>
        <div className={styles.logo}>Coverage<span>Ops</span></div>
        <div className={styles.sub}>מערכת ניתוח כיסוי סלולרי מבצעי</div>

        {error && <div className={styles.error}>{error}</div>}

        <form onSubmit={handleLogin}>
          <div className={styles.fieldGroup}>
            <label className={styles.label}>שם משתמש</label>
            <input
              className={styles.input}
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="הקלד שם משתמש"
              autoFocus
              required
            />
          </div>
          <div className={styles.fieldGroup}>
            <label className={styles.label}>סיסמה</label>
            <input
              className={styles.input}
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••"
              required
            />
          </div>
          <button className={styles.btn} type="submit" disabled={loading}>
            {loading ? "מתחבר..." : "כניסה למערכת"}
          </button>
        </form>

        <div className={styles.hint}>
          ברירת מחדל: admin / Admin1234!
        </div>
      </div>
    </div>
  );
}
