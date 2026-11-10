/**
 * Device table (spec 8.3 "מכשירים") — ADMIN/OPERATOR only. Sort, search, status filter;
 * on phones the rows become cards. Selecting a row opens the device panel.
 */
import { useMemo, useState } from "react";
import type { FieldDeviceRow, FieldOperator } from "../../services/api";
import { fmtNum, relTime } from "../../lib/fieldFormat";
import { networkLabel } from "../../lib/fieldStatus";
import { operatorName } from "../../lib/fieldQueries";
import { Battery, Num, Ownership, StatusText, TierBadge } from "./primitives";
import s from "./field.module.css";

type SortKey = "label" | "status" | "tier" | "rsrp" | "battery_pct" | "last_seen_at" | "open_alerts";
const STATUS_ORDER = ["offline", "degraded", "no_contact", "paused", "active", "never_seen", "revoked", "withdrawn"];
const STATUS_FILTERS: [string, string][] = [
  ["", "הכל"], ["active", "פעיל"], ["degraded", "ניטור מוגבל"], ["offline", "לא מקוון"], ["paused", "מושהה"],
  ["no_contact", "ללא קשר"], ["never_seen", "טרם התחבר"], ["revoked", "בוטל"],
];

export default function DeviceTable({ rows, operators, selectedId, onSelect, compact, now }: {
  rows: FieldDeviceRow[]; operators?: FieldOperator[]; selectedId: string | null;
  onSelect: (id: string) => void; compact?: boolean; now: number;
}) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState<{ k: SortKey; desc: boolean }>({ k: "open_alerts", desc: true });

  const view = useMemo(() => {
    const ql = q.trim().toLowerCase();
    const out = rows.filter((r) => (!status || r.status === status)
      && (!ql || (r.label ?? "").toLowerCase().includes(ql) || r.id.includes(ql) || (r.group ?? "").toLowerCase().includes(ql)));
    const val = (r: FieldDeviceRow): number | string => {
      switch (sort.k) {
        case "label": return r.label ?? "";
        case "status": return STATUS_ORDER.indexOf(r.status);
        case "tier": return r.tier ?? -1;
        case "rsrp": return r.rsrp ?? r.nr_ss_rsrp ?? -999;
        case "battery_pct": return r.battery_pct ?? -1;
        case "last_seen_at": return r.last_seen_at ? Date.parse(r.last_seen_at) : 0;
        case "open_alerts": return r.open_alerts * 1000 - STATUS_ORDER.indexOf(r.status);
      }
    };
    return out.sort((a, b) => {
      const x = val(a), y = val(b);
      const c = typeof x === "string" ? x.localeCompare(String(y), "he") : (x as number) - (y as number);
      return sort.desc ? -c : c;
    });
  }, [rows, q, status, sort]);

  const th = (k: SortKey, label: string, num = false) => (
    <th scope="col" className={num ? s.numCell : undefined}
      aria-sort={sort.k === k ? (sort.desc ? "descending" : "ascending") : "none"}>
      <button className={s.thBtn} onClick={() => setSort({ k, desc: sort.k === k ? !sort.desc : k !== "label" })}>
        {label}{sort.k === k ? (sort.desc ? " ▾" : " ▴") : ""}
      </button>
    </th>
  );

  const rsrpOf = (r: FieldDeviceRow) => (r.network_type === "NR_SA" ? r.nr_ss_rsrp : r.rsrp);
  const noTier = (r: FieldDeviceRow) => ["paused", "no_contact", "never_seen", "revoked", "withdrawn"].includes(r.status);

  return (
    <section className={s.card} aria-label="מכשירים" data-testid="device-table">
      <div className={s.cardH}>
        <h2>מכשירים</h2>
        <span className={s.sub}>{view.length} מתוך {rows.length} · ממוין לפי {sortName(sort.k)}</span>
        <div className={s.tools}>
          <input className={s.input} type="search" placeholder="🔍 חיפוש שם / קבוצה…" value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="חיפוש מכשיר" style={{ width: 170 }} />
          <select className={s.select} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="סינון לפי סטטוס">
            {STATUS_FILTERS.map(([v, l]) => <option key={v} value={v}>סטטוס: {l}</option>)}
          </select>
        </div>
      </div>

      <div className={`${s.tw} ${s.onlyWide}`} style={compact ? { maxHeight: 420, overflowY: "auto" } : undefined}>
        <table className={s.t}>
          <thead>
            <tr>
              {th("label", "מכשיר")}
              <th scope="col">בעלות</th>
              {th("status", "סטטוס")}
              {th("tier", "רמת קליטה")}
              {th("rsrp", "RSRP (dBm)", true)}
              <th scope="col">מפעיל</th>
              <th scope="col">רשת</th>
              {th("battery_pct", "סוללה")}
              {th("last_seen_at", "נראה לאחרונה")}
              {!compact && <th scope="col">גרסה</th>}
              {th("open_alerts", "התראות")}
              <th scope="col">פקודות</th>
            </tr>
          </thead>
          <tbody>
            {view.map((r) => (
              <tr key={r.id} className={r.id === selectedId ? s.sel : undefined} data-device-row={r.id}>
                <td>
                  <button className={s.rowBtn} onClick={() => onSelect(r.id)} aria-pressed={r.id === selectedId}>
                    {r.label ?? r.id.slice(0, 8)}
                  </button>
                  {r.effective_location_mode === "coarse" && <span className={s.chip} style={{ marginInlineStart: 6 }}>מיקום משוער</span>}
                  {r.flags?.config_pending && <span className={s.chip} style={{ marginInlineStart: 6 }} title="התצורה טרם הוחלה במכשיר">תצורה ממתינה</span>}
                </td>
                <td><Ownership value={r.ownership} /></td>
                <td><StatusText row={r} /></td>
                <td>{noTier(r)
                  ? <span className={s.muted}>— (אין מדידה כשמושהה)</span>
                  : <TierBadge tier={r.tier} stale={r.tier_stale} networkType={r.network_type} compact />}
                  {r.status === "offline" && r.tier != null && <span className={s.muted} style={{ fontSize: 10 }}> (אחרון)</span>}
                </td>
                <td className={s.numCell}><Num>{fmtNum(rsrpOf(r))}</Num></td>
                <td>{operatorName(r.operator_key, operators)}</td>
                <td>{networkLabel(r.network_type)}</td>
                <td><Battery pct={r.battery_pct} charging={r.is_charging} /></td>
                <td>{relTime(r.last_seen_at, now)}</td>
                {!compact && <td><Num>{r.app_version ?? "—"}</Num></td>}
                <td>{r.open_alerts ? <span className={`${s.al} ${s["al-warn"]}`}>{r.open_alerts}</span> : <span className={s.muted}>—</span>}</td>
                <td>{r.open_commands ? <span className={s.chip}>⏳ {r.open_commands}</span> : <span className={s.muted}>—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!view.length && <div className={s.state}>אין מכשירים התואמים לסינון.</div>}
      </div>

      {/* Phones: cards */}
      <ul className={s.onlyNarrow} style={{ listStyle: "none" }}>
        {view.map((r) => (
          <li key={r.id} style={{ borderBottom: "1px solid var(--border)", padding: "10px 14px" }}
            className={r.id === selectedId ? s.sel : undefined}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button className={s.rowBtn} onClick={() => onSelect(r.id)}>{r.label ?? r.id.slice(0, 8)}</button>
              <Ownership value={r.ownership} />
              <span style={{ marginInlineStart: "auto" }}>{noTier(r) ? null : <TierBadge tier={r.tier} stale={r.tier_stale} networkType={r.network_type} dbm={rsrpOf(r)} />}</span>
            </div>
            <div style={{ fontSize: 12, marginTop: 4, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <StatusText row={r} />
              <span className={s.muted}>{operatorName(r.operator_key, operators)} · {networkLabel(r.network_type)}</span>
              <Battery pct={r.battery_pct} charging={r.is_charging} />
              <span className={s.muted}>{relTime(r.last_seen_at, now)}</span>
              {r.open_alerts > 0 && <span className={`${s.al} ${s["al-warn"]}`}>{r.open_alerts} התראות</span>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function sortName(k: SortKey): string {
  return ({ label: "שם", status: "סטטוס", tier: "רמה", rsrp: "RSRP", battery_pct: "סוללה",
    last_seen_at: "נראה לאחרונה", open_alerts: "התראות" } as const)[k];
}

