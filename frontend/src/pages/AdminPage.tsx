import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { usersApi, UserOut, deviceProfilesApi, apiErrorText } from "../services/api";
import type { DeviceProfile, DeviceProfileIn } from "../services/api";
import { useStore } from "../store/useStore";
import styles from "./AdminPage.module.css";
import { BrandMark } from "../components/Brand";

type Role = "ADMIN" | "OPERATOR" | "VIEWER";

const ROLE_LABELS: Record<Role, string> = {
  ADMIN: "מנהל",
  OPERATOR: "מפעיל",
  VIEWER: "צופה",
};

const ROLE_COLORS: Record<Role, string> = {
  ADMIN: "#ffa30a",     // --brand
  OPERATOR: "#00d4ff",  // --accent
  VIEWER: "#7b97b4",    // --text-dim
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

// ── Device (receiver) profiles ───────────────────────────────────────────────
type ProfileForm = {
  key: string; name_he: string; rx_height_m: string; rx_sensitivity_dbm: string;
  antenna_gain_dbi: string; description_he: string; is_default: boolean;
};
const EMPTY_PROFILE: ProfileForm = {
  key: "", name_he: "", rx_height_m: "1.5", rx_sensitivity_dbm: "-100",
  antenna_gain_dbi: "0", description_he: "", is_default: false,
};

function ProfileModal({ profile, onClose, onSaved }: {
  profile: DeviceProfile | null; onClose: () => void; onSaved: () => void;
}) {
  const [form, setForm] = useState<ProfileForm>(profile ? {
    key: profile.key, name_he: profile.name_he,
    rx_height_m: String(profile.rx_height_m), rx_sensitivity_dbm: String(profile.rx_sensitivity_dbm),
    antenna_gain_dbi: String(profile.antenna_gain_dbi ?? 0), description_he: profile.description_he ?? "",
    is_default: profile.is_default,
  } : EMPTY_PROFILE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const set = (k: keyof ProfileForm, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }));

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const h = Number(form.rx_height_m), sens = Number(form.rx_sensitivity_dbm), gain = Number(form.antenna_gain_dbi || 0);
    if (!/^[a-z0-9_\-]+$/.test(form.key.trim())) { setError("מפתח: אותיות לטיניות קטנות, ספרות, _ או - בלבד"); return; }
    if (!form.name_he.trim()) { setError("יש להזין שם"); return; }
    if (!Number.isFinite(h) || h < 0.1 || h > 300) { setError("גובה מקלט: 0.1–300 מ'"); return; }
    if (!Number.isFinite(sens) || sens < -150 || sens > -30) { setError("רגישות: −150 עד −30 dBm"); return; }
    if (!Number.isFinite(gain) || gain < -20 || gain > 40) { setError("רווח אנטנה: −20 עד 40 dBi"); return; }
    const payload: DeviceProfileIn = {
      key: form.key.trim(), name_he: form.name_he.trim(),
      rx_height_m: h, rx_sensitivity_dbm: sens, antenna_gain_dbi: gain,
      description_he: form.description_he.trim() || null, is_default: form.is_default,
    };
    setLoading(true);
    try {
      if (profile) await deviceProfilesApi.update(profile.id, payload);
      else await deviceProfilesApi.create(payload);
      onSaved();
      onClose();
    } catch (err) {
      setError(apiErrorText(err, "שגיאה בשמירת הפרופיל"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <h2>{profile ? `✏️ עריכת פרופיל – ${profile.name_he}` : "➕ פרופיל מכשיר חדש"}</h2>
          <button className={styles.closeBtn} onClick={onClose}>✕</button>
        </div>
        <form onSubmit={handleSubmit} className={styles.form}>
          <div className={styles.fieldRow}>
            <div className={styles.field}>
              <label>שם</label>
              <input type="text" value={form.name_he} maxLength={64} autoFocus placeholder="טלפון נייד"
                     onChange={(e) => set("name_he", e.target.value)} />
            </div>
            <div className={styles.field}>
              <label>מפתח</label>
              <input type="text" value={form.key} maxLength={32} placeholder="handheld" dir="ltr"
                     onChange={(e) => set("key", e.target.value)} />
            </div>
          </div>
          <div className={styles.fieldRow3}>
            <div className={styles.field}>
              <label>גובה (מ' AGL)</label>
              <input type="number" step="0.1" value={form.rx_height_m} onChange={(e) => set("rx_height_m", e.target.value)} />
            </div>
            <div className={styles.field}>
              <label>רגישות (dBm)</label>
              <input type="number" step="1" value={form.rx_sensitivity_dbm} onChange={(e) => set("rx_sensitivity_dbm", e.target.value)} />
            </div>
            <div className={styles.field}>
              <label>רווח (dBi)</label>
              <input type="number" step="0.5" value={form.antenna_gain_dbi} onChange={(e) => set("antenna_gain_dbi", e.target.value)} />
            </div>
          </div>
          <div className={styles.field}>
            <label>תיאור <span className={styles.optional}>(אופציונלי)</span></label>
            <input type="text" value={form.description_he} maxLength={256}
                   onChange={(e) => set("description_he", e.target.value)} />
          </div>
          <label className={styles.checkLine}>
            <input type="checkbox" checked={form.is_default} onChange={(e) => set("is_default", e.target.checked)} />
            פרופיל ברירת מחדל (יחליף את ברירת המחדל הנוכחית)
          </label>
          {error && <div className={styles.errorMsg}>{error}</div>}
          <div className={styles.modalActions}>
            <button type="button" className={styles.cancelBtn} onClick={onClose}>ביטול</button>
            <button type="submit" className={styles.submitBtn} disabled={loading}>
              {loading ? "שומר..." : profile ? "שמור שינויים" : "הוסף פרופיל"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function DeviceProfilesCard({ isAdmin }: { isAdmin: boolean }) {
  const [profiles, setProfiles] = useState<DeviceProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState("");
  const [editing, setEditing] = useState<DeviceProfile | null | "new">(null);
  const [confirmDel, setConfirmDel] = useState<number | null>(null);
  const [actionErr, setActionErr] = useState("");

  async function fetchProfiles() {
    setLoading(true);
    try {
      setProfiles(await deviceProfilesApi.list());
      setLoadErr("");
    } catch (err) {
      setLoadErr(apiErrorText(err, "שגיאה בטעינת פרופילי מכשיר"));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { fetchProfiles(); }, []);

  // After any write: reload this table and invalidate the link panel's cached list
  const qc = useQueryClient();
  async function refresh() {
    qc.invalidateQueries({ queryKey: ["device-profiles"] });
    await fetchProfiles();
  }

  async function handleDelete(id: number) {
    setActionErr("");
    try {
      await deviceProfilesApi.remove(id);
      await refresh();
    } catch (err) {
      setActionErr(apiErrorText(err, "שגיאה במחיקת הפרופיל"));   // e.g. "cannot delete the default profile"
    } finally {
      setConfirmDel(null);
    }
  }

  async function makeDefault(p: DeviceProfile) {
    setActionErr("");
    try {
      await deviceProfilesApi.update(p.id, { is_default: true });
      await refresh();
    } catch (err) {
      setActionErr(apiErrorText(err, "שגיאה בעדכון ברירת המחדל"));
    }
  }

  return (
    <section className={styles.sectionCard}>
      <div className={styles.sectionHead}>
        <div>
          <h2 className={styles.sectionTitle}>📱 פרופילי מכשיר</h2>
          <div className={styles.sectionSub}>פרופילי מקלט לחישובי קישור נקודתי וחיפוש אתרים משרתים (גובה, רגישות, רווח אנטנה)</div>
        </div>
        {isAdmin && <button className={styles.addBtn} onClick={() => setEditing("new")}>➕ פרופיל חדש</button>}
      </div>

      {actionErr && <div className={styles.errorMsg} style={{ marginBottom: 12 }}>{actionErr}</div>}

      {loading ? (
        <div className={styles.loading}><div className={styles.spinner} /><span>טוען פרופילים...</span></div>
      ) : loadErr ? (
        <div className={styles.errorMsg}>{loadErr}</div>
      ) : (
        <div className={styles.tableWrapper}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>שם</th><th>מפתח</th><th>גובה (מ')</th><th>רגישות (dBm)</th><th>רווח (dBi)</th>
                <th>תיאור</th><th>ברירת מחדל</th>{isAdmin && <th>פעולות</th>}
              </tr>
            </thead>
            <tbody>
              {profiles.length === 0 ? (
                <tr><td colSpan={isAdmin ? 8 : 7} className={styles.emptyRow}>אין פרופילים</td></tr>
              ) : profiles.map((p) => (
                <tr key={p.id} className={styles.row}>
                  <td className={styles.fullName}>{p.name_he}</td>
                  <td className={styles.username}>{p.key}</td>
                  <td className={styles.num}>{p.rx_height_m}</td>
                  <td className={styles.num}>{p.rx_sensitivity_dbm}</td>
                  <td className={styles.num}>{p.antenna_gain_dbi ?? 0}</td>
                  <td className={styles.meta}>{p.description_he || "—"}</td>
                  <td>
                    {p.is_default ? (
                      <span className={`${styles.statusBadge} ${styles.statusActive}`}>★ ברירת מחדל</span>
                    ) : isAdmin ? (
                      <button className={styles.makeDefaultBtn} onClick={() => makeDefault(p)}>קבע כברירת מחדל</button>
                    ) : "—"}
                  </td>
                  {isAdmin && (
                    <td className={styles.actions}>
                      <button className={styles.editBtn} onClick={() => setEditing(p)} title="ערוך">✏️</button>
                      {confirmDel === p.id ? (
                        <span className={styles.confirmDelete}>
                          <button className={styles.confirmYes} onClick={() => handleDelete(p.id)}>מחק</button>
                          <button className={styles.confirmNo} onClick={() => setConfirmDel(null)}>ביטול</button>
                        </span>
                      ) : (
                        <button className={styles.deleteBtn} onClick={() => setConfirmDel(p.id)} title="מחק">🗑️</button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <ProfileModal
          profile={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      )}
    </section>
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
            <BrandMark size={28} className={styles.logo} />
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
            <div className={styles.statNum} style={{ color: "var(--accent2)" }}>{stats.active}</div>
            <div className={styles.statLabel}>פעילים</div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statNum} style={{ color: "var(--brand)" }}>{stats.admins}</div>
            <div className={styles.statLabel}>מנהלים</div>
          </div>
          <div className={styles.statCard}>
            <div className={styles.statNum} style={{ color: "var(--accent)" }}>{stats.operators}</div>
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

        <DeviceProfilesCard isAdmin={currentUser?.role === "ADMIN"} />
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
