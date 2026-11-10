import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { authApi } from "../services/api";
import { useStore } from "../store/useStore";
import styles from "./LoginPage.module.css";
import { BrandLogo } from "../components/Brand";

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
        <h1 className={styles.logo}><BrandLogo width={332} /></h1>
        <p className={styles.sub}>מערכת ניתוח כיסוי סלולרי מבצעי</p>

        {error && <div className={styles.error} role="alert">{error}</div>}

        <form onSubmit={handleLogin}>
          <div className={styles.fieldGroup}>
            <label className={styles.label} htmlFor="login-user">שם משתמש</label>
            <input
              id="login-user"
              className={styles.input}
              type="text"
              autoComplete="username"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="הקלד שם משתמש"
              autoFocus
              required
            />
          </div>
          <div className={styles.fieldGroup}>
            <label className={styles.label} htmlFor="login-pass">סיסמה</label>
            <input
              id="login-pass"
              className={styles.input}
              type="password"
              autoComplete="current-password"
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
      </div>
    </div>
  );
}
