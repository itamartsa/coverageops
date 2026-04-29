import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { usersApi, UserOut } from "../services/api";
import { useStore } from "../store/useStore";
import styles from "./AdminPage.module.css";

type Role = "ADMIN" | "OPERATOR" | "VIEWER";

const ROLE_LABELS: Record<Role, string> = {
  ADMIN: "מנהל",
  OPERATOR: "מפעיל",
  VIEWER: "צופה",
};

const ROLE_COLORS: Record<Role, string> = {
  ADMIN: "#f59e0b",
  OPERATOR: "#3b82f6",
  VIEWER: "#6b7280",
};

// ── Modal: Create User ───────────────────────────────────────────────────────
function CreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({ username: "", full_name: "", password: "", role: "OPERATOR" as Role });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!form.username.trim() || !form.full_name.trim() || !form.password.trim()) {
      setError("יש למלא את כל השדות");
      return;
    }
    setLoading(true);
    try {
      await usersApi.create(form);
      onCreated();
      onClose();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "שגיאה ביצירת המשתמש");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={e => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <h2>➕ הוספת משתמש חדש</h2>
          <button className={styles.closeBtn} onClick={onClose}>✕</button>
        </div>
        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.field}>
            <label>שם מלא</label>
            <input
              type="text"
              placeholder="ישראל ישראלי"
              value={form.full_name}
              onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))}
              autoFocus
            />
          </div>
          <div className={styles.field}>
            <label>שם משתמש</label>
            <input
              type="text"
              placeholder="israel_i"
              value={form.username}
              onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
            />
          </div>
          <div className={styles.field}>
            <label>סיסמה</label>
            <input
              type="password"
              placeholder="לפחות 6 תווים"
              value={form.password}
              onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
            />
          </div>
          <div className={styles.field}>
            <label>תפקיד</label>
            <select value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value as Role }))}>
              <option value="OPERATOR">מפעיל</option>
              <option value="VIEWER">צופה</option>
              <option value="ADMIN">מנהל</option>
            </select>
          </div>
          {error && <div className={styles.errorMsg}>{error}</div>}
          <div className={styles.modalActions}>
            <button type="button" className={styles.cancelBtn} onClick={onClose}>ביטול</button>
            <button type="submit" className={styles.submitBtn} disabled={loading}>
              {loading ? "שומר..." : "הוסף משתמש"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Modal: Edit User ─────────────────────────────────────────────────────────
function EditModal({ user, onClose, onSaved }: { user: UserOut; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    full_name: user.full_name,
    role: user.role as Role,
    is_active: user.is_active,
    password: "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const payload: any = {
        full_name: form.full_name,
        role: form.role,
        is_active: form.is_active,
      };
      if (form.password.trim()) payload.password = form.password;
      await usersApi.update(user.id, payload);
      onSaved();
      onClose();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "שגיאה בעדכון המשתמש");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={e => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <h2>✏️ עריכת משתמש – {user.username}</h2>
          <button className={styles.closeBtn} onClick={onClose}>✕</button>
        </div>
        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.field}>
            <label>שם מלא</label>
            <input
              type="text"
              value={form.full_name}
              onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))}
            />
          </div>
          <div className={styles.field}>
            <label>תפקיד</label>
            <select value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value as Role }))}>
              <option value="OPERATOR">מפעיל</option>
              <option value="VIEWER">צופה</option>
              <option value="ADMIN">מנהל</option>
            </select>
          </div>
          <div className={styles.field}>
            <label>סטטוס</label>
            <div className={styles.toggle}>
              <button
                type="button"
                className={`${styles.toggleBtn} ${form.is_active ? styles.active : ""}`}
                onClick={() => setForm(f => ({ ...f, is_active: true }))}
              >✅ פעיל</button>
              <button
                type="button"
                className={`${styles.toggleBtn} ${!form.is_active ? styles.inactive : ""}`}
                onClick={() => setForm(f => ({ ...f, is_active: false }))}
              >🔒 מושהה</button>
            </div>
          </div>
          <div className={styles.field}>
            <label>סיסמה חדשה <span className={styles.optional}>(אופציונלי)</span></label>
            <input
              type="password"
              placeholder="השאר ריק לשמירת הסיסמה הנוכחית"
              value={form.password}
              onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
            />
          </div>
          {error && <div className={styles.errorMsg}>{error}</div>}
          <div className={styles.modalActions}>
            <button type="button" className={styles.cancelBtn} onClick={onClose}>ביטול</button>
            <button type="submit" className={styles.submitBtn} disabled={loading}>
              {loading ? "שומר..." : "שמור שינויים"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Main AdminPage Component ─────────────────────────────────────────────────
export default function AdminPage() {
  const navigate = useNavigate();
  const { user: currentUser } = useStore();
  const [users, setUsers] = useState<UserOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editingUser, setEditingUser] = useState<UserOut | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterRole, setFilterRole] = useState<string>("ALL");

  async function fetchUsers() {
    setLoading(true);
    try {
      const data = await usersApi.list();
      setUsers(data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchUsers(); }, []);

  async function handleDelete(id: number) {
    try {
      await usersApi.remove(id);
      await fetchUsers();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "שגיאה במחיקה");
    } finally {
      setDeleteConfirm(null);
    }
  }

  async function handleToggleActive(user: UserOut) {
    try {
      await usersApi.update(user.id, { is_active: !user.is_active });
      await fetchUsers();
    } catch {}
  }

  const filtered = users.filter(u => {
    const matchSearch =
      u.username.toLowerCase().includes(searchQuery.toLowerCase()) ||
      u.full_name.toLowerCase().includes(searchQuery.toLowerCase());
    const matchRole = filterRole === "ALL" || u.role === filterRole;
    return matchSearch && matchRole;
  });

  const stats = {
    total:    users.length,
    active:   users.filter(u => u.is_active).length,
    admins:   users.filter(u => u.role === "ADMIN").length,
    operators: users.filter(u => u.role === "OPERATOR").length,
  };

  return (
    <div className={styles.page} dir="rtl">
      {/* Header */}
      <header className={styles.header}>
        <div className={styles.headerLeft}>
          <button className={styles.backBtn} onClick={() => navigate("/")}>
            ← חזרה לאפליקציה
          </button>
          <div className={styles.title}>
            <span className={styles.logo}>Coverage<span>Ops</span></span>
            <span className={styles.pageName}>ניהול משתמשים</span>
          </div>
        </div>
        <div className={styles.currentUser}>
          <div className={styles.avatar}>{currentUser?.full_name?.[0] ?? "מ"}</div>
          <div>
            <div className={styles.userName}>{currentUser?.full_name}</div>
            <div className={styles.userRole}>ADMIN</div>
          </div>
        </div>
      </header>

      <main className={styles.main}>
        {/* Stats row */}
        <div className={styles.statsRow}>
          <div className={styles.statCard}>
            <div className={styles.statNum}>{stats.total}</div>
            <div className={styles.statLabel}>סה"כ משתמשים</div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statNum} style={{ color: "#00ff88" }}>{stats.active}</div>
            <div className={styles.statLabel}>פעילים</div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statNum} style={{ color: "#f59e0b" }}>{stats.admins}</div>
            <div className={styles.statLabel}>מנהלים</div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statNum} style={{ color: "#3b82f6" }}>{stats.operators}</div>
            <div className={styles.statLabel}>מפעילים</div>
          </div>
        </div>

        {/* Toolbar */}
        <div className={styles.toolbar}>
          <input
            className={styles.searchInput}
            type="text"
            placeholder="🔍 חיפוש לפי שם או שם משתמש..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
          <select
            className={styles.roleFilter}
            value={filterRole}
            onChange={e => setFilterRole(e.target.value)}
          >
            <option value="ALL">כל התפקידים</option>
            <option value="ADMIN">מנהל</option>
            <option value="OPERATOR">מפעיל</option>
            <option value="VIEWER">צופה</option>
          </select>
          <button className={styles.addBtn} onClick={() => setShowCreate(true)}>
            ➕ משתמש חדש
          </button>
        </div>

        {/* Table */}
        {loading ? (
          <div className={styles.loading}>
            <div className={styles.spinner} />
            <span>טוען משתמשים...</span>
          </div>
        ) : (
          <div className={styles.tableWrapper}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>#</th>
                  <th>שם מלא</th>
                  <th>שם משתמש</th>
                  <th>תפקיד</th>
                  <th>סטטוס</th>
                  <th>כניסה אחרונה</th>
                  <th>נוצר בתאריך</th>
                  <th>פעולות</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={8} className={styles.emptyRow}>לא נמצאו משתמשים</td></tr>
                ) : filtered.map(u => (
                  <tr key={u.id} className={`${styles.row} ${!u.is_active ? styles.inactive : ""}`}>
                    <td className={styles.idCell}>{u.id}</td>
                    <td className={styles.fullName}>
                      <div className={styles.userAvatar}>{u.full_name[0]}</div>
                      {u.full_name}
                    </td>
                    <td className={styles.username}>@{u.username}</td>
                    <td>
                      <span
                        className={styles.roleBadge}
                        style={{ background: `${ROLE_COLORS[u.role as Role]}22`, color: ROLE_COLORS[u.role as Role], borderColor: `${ROLE_COLORS[u.role as Role]}44` }}
                      >
                        {ROLE_LABELS[u.role as Role] ?? u.role}
                      </span>
                    </td>
                    <td>
                      <button
                        className={`${styles.statusBadge} ${u.is_active ? styles.statusActive : styles.statusSuspended}`}
                        onClick={() => handleToggleActive(u)}
                        title="לחץ לשינוי סטטוס"
                      >
                        {u.is_active ? "✅ פעיל" : "🔒 מושהה"}
                      </button>
                    </td>
                    <td className={styles.meta}>
                      {u.last_login ? new Date(u.last_login).toLocaleDateString("he-IL") : "—"}
                    </td>
                    <td className={styles.meta}>
                      {new Date(u.created_at).toLocaleDateString("he-IL")}
                    </td>
                    <td className={styles.actions}>
                      <button
                        className={styles.editBtn}
                        onClick={() => setEditingUser(u)}
                        title="ערוך"
                      >✏️</button>
                      {u.id !== currentUser?.id && (
                        deleteConfirm === u.id ? (
                          <span className={styles.confirmDelete}>
                            <button className={styles.confirmYes} onClick={() => handleDelete(u.id)}>מחק</button>
                            <button className={styles.confirmNo} onClick={() => setDeleteConfirm(null)}>ביטול</button>
                          </span>
                        ) : (
                          <button
                            className={styles.deleteBtn}
                            onClick={() => setDeleteConfirm(u.id)}
                            title="מחק"
                          >🗑️</button>
                        )
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>

      {showCreate && (
        <CreateModal
          onClose={() => setShowCreate(false)}
          onCreated={fetchUsers}
        />
      )}
      {editingUser && (
        <EditModal
          user={editingUser}
          onClose={() => setEditingUser(null)}
          onSaved={fetchUsers}
        />
      )}
    </div>
  );
}
