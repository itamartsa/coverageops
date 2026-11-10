/**
 * Device detail panel (spec 8.3 "מגירת מכשיר") — ADMIN/OPERATOR. Current signal (NSA: tier by
 * the LTE anchor; the NR leg shown separately, without a tier), serving cell, permissions and
 * degraded reasons, config version, RSRP time series, commands, track (audit-logged), CSV export.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiErrorText, fieldApi, type DeviceTrack, type DeviceTsMetric, type FieldOperator } from "../../services/api";
import { fmtDateTime, fmtNum, relTime, durationText } from "../../lib/fieldFormat";
import { LOCATION_MODE_HE, networkLabel, permissionChips } from "../../lib/fieldStatus";
import { operatorName, windowRange } from "../../lib/fieldQueries";
import TimeSeriesChart from "./charts/TimeSeriesChart";
import DeviceCommands from "./DeviceCommands";
import DeviceAdminActions from "./DeviceAdminActions";
import { Battery, ErrorState, Loading, Num, Ownership, StatusText, TierBadge } from "./primitives";
import s from "./field.module.css";
import d from "./DeviceDrawer.module.css";

const METRICS: { v: DeviceTsMetric; label: string; dbm: boolean }[] = [
  { v: "rsrp", label: "RSRP LTE (כולל עוגן NSA)", dbm: true },
  { v: "nr_ss_rsrp", label: "SS-RSRP NR (5G SA)", dbm: true },
  { v: "nr_leg_ss_rsrp", label: "SS-RSRP רגל NR (NSA)", dbm: true },
  { v: "sinr", label: "SINR", dbm: false },
  { v: "rsrq", label: "RSRQ", dbm: false },
  { v: "battery", label: "סוללה", dbm: false },
];

export default function DeviceDrawer({ id, window: win, operators, isAdmin, onClose, track, onTrack, now }: {
  id: string; window: string; operators?: FieldOperator[]; isAdmin: boolean; onClose: () => void;
  track: DeviceTrack | null; onTrack: (t: DeviceTrack | null) => void; now: number;
}) {
  const devQ = useQuery({
    queryKey: ["field", "device", id],
    queryFn: () => fieldApi.device(id),
    refetchInterval: 30_000, refetchIntervalInBackground: false,
  });
  const dev = devQ.data;
  const defaultMetric: DeviceTsMetric = dev?.signal.network_type === "NR_SA" ? "nr_ss_rsrp" : "rsrp";
  const [metricSel, setMetric] = useState<DeviceTsMetric | null>(null);
  const metric = metricSel ?? defaultMetric;
  // Device time series: ≤ 31 days on the server
  const tsWindow = win === "30d" ? "30d" : win;
  const tsQ = useQuery({
    queryKey: ["field", "device-ts", id, metric, tsWindow],
    queryFn: () => fieldApi.deviceTimeseries(id, { ...windowRange(tsWindow), metric }),
    enabled: !!dev, refetchInterval: 60_000, refetchIntervalInBackground: false,
  });
  const [trackBusy, setTrackBusy] = useState(false);
  const [trackErr, setTrackErr] = useState<string | null>(null);
  const [exportMsg, setExportMsg] = useState<string | null>(null);

  async function showTrack() {
    if (track) { onTrack(null); return; }
    setTrackBusy(true); setTrackErr(null);
    try {
      // ≤ 7 days on the server
      onTrack(await fieldApi.track(id, { ...windowRange(win, 7 * 86400_000), max_points: 2000 }));
    } catch (e) {
      setTrackErr(apiErrorText(e, "טעינת המסלול נכשלה"));
    } finally {
      setTrackBusy(false);
    }
  }

  async function exportCsv() {
    setExportMsg(null);
    try {
      await fieldApi.exportMeasurementsCsv({ ...windowRange(win, 31 * 86400_000), device_id: id });
      setExportMsg("הקובץ הורד. הייצוא נרשם ביומן הפעילות (FIELD_EXPORT).");
    } catch (e) {
      setExportMsg(`⚠ ${apiErrorText(e, "הייצוא נכשל")}`);
    }
  }

  if (devQ.isLoading) return <aside className={`${s.card} ${d.drawer}`} aria-label="פרטי מכשיר"><Loading lines={6} /></aside>;
  if (devQ.error || !dev) {
    return (
      <aside className={`${s.card} ${d.drawer}`} aria-label="פרטי מכשיר">
        <div className={s.cardH}><h2>פרטי מכשיר</h2><div className={s.tools}><button className={s.btn} onClick={onClose} aria-label="סגור">✕</button></div></div>
        <ErrorState error={devQ.error} onRetry={() => devQ.refetch()} what="פרטי המכשיר" />
      </aside>
    );
  }

  const sig = dev.signal;
  const cell = (dev.current_cell ?? {}) as Record<string, number | null>;
  const leg = dev.nr_leg as Record<string, number | null> | null;
  const isNsa = sig.network_type === "NR_NSA";
  const shownDbm = sig.network_type === "NR_SA" ? sig.nr_ss_rsrp_dbm ?? sig.rsrp_dbm : sig.rsrp_dbm;
  const cfgPending = dev.applied_config_version != null && dev.config_version != null && dev.applied_config_version < dev.config_version;
  const perms = permissionChips(dev.permissions);
  const configuredLoc = dev.config.location_mode;
  const name = dev.label ?? dev.id.slice(0, 8);
  const paused = dev.status === "paused" || dev.status === "no_contact";
  const mdef = METRICS.find((x) => x.v === metric)!;

  // Stats under the chart
  const pts = tsQ.data?.series[0]?.points ?? [];
  const oosSec = (tsQ.data?.gaps ?? []).filter((g) => g[2] === "oos").reduce((a, g) => a + (Date.parse(g[1]) - Date.parse(g[0])) / 1000, 0);

  return (
    <aside className={`${s.card} ${d.drawer}`} aria-label={`פרטי מכשיר ${name}`} data-testid="device-drawer">
      <div className={s.cardH}>
        <h2 className={d.title}>{name}</h2>
        <Ownership value={dev.ownership} />
        <StatusText row={{ ...dev, last_seen_at: dev.last_heartbeat_at ?? dev.last_sample_received_at,
          degraded_reasons: dev.degraded_reasons }} />
        <div className={s.tools}><button className={s.btn} onClick={onClose} aria-label="סגור את פרטי המכשיר">✕</button></div>
      </div>

      <div className={d.sec}>
        <div className={d.chips}>
          {paused || dev.status === "never_seen"
            ? <span className={s.muted}>אין מדידה עדכנית (המכשיר {dev.status_label_he})</span>
            : <TierBadge tier={sig.tier} dbm={shownDbm} stale={sig.tier_stale} networkType={sig.network_type}
                serviceState={sig.service_state} basis={sig.tier_basis_he} />}
          {sig.tier_basis_he && <span className={s.chip}>{sig.tier_basis_he}</span>}
          <span className={s.chip}>{operatorName(sig.operator_key, operators)} · {networkLabel(sig.network_type)}</span>
          {sig.tier_stale && <span className={s.chip}>נתוני תא ישנים</span>}
          <Battery pct={dev.battery_pct} charging={dev.is_charging} />
        </div>
        <dl className={d.meta}>
          <div><dt>{isNsa ? "עוגן LTE (תא משרת)" : "תא משרת"}</dt>
            <dd><Num>{`PCI ${cell.pci ?? "—"} · B${cell.band ?? "—"} · ECI ${cell.cell_id ?? "—"}`}</Num></dd></div>
          <div><dt>RSRQ / SINR</dt><dd><Num>{`${fmtNum(cell.rsrq_db, 1)} dB / ${fmtNum(cell.sinr_db, 1)} dB`}</Num></dd></div>
          {isNsa && (
            <div><dt>רגל NR (ללא רמה)</dt>
              <dd>{leg ? <Num>{`SS-RSRP ${fmtNum(leg.ss_rsrp_dbm)} dBm · n${leg.band ?? "—"} · PCI ${leg.pci ?? "—"}`}</Num> : <span className={s.muted}>לא דווחה</span>}</dd></div>
          )}
          <div><dt>מיקום</dt>
            <dd>{dev.location
              ? <>{dev.location.precision === "coarse" ? "משוער (~1 ק״מ)" : <>מדויק · ±<Num>{fmtNum(dev.location.acc)}</Num> מ׳</>} · {relTime(dev.location.ts, now)}</>
              : <span className={s.muted}>אין</span>}</dd></div>
          <div><dt>מצב מיקום</dt>
            <dd>הוגדר: {LOCATION_MODE_HE[configuredLoc] ?? configuredLoc} · בפועל: {dev.effective_location_mode ? LOCATION_MODE_HE[dev.effective_location_mode] ?? dev.effective_location_mode : "—"}</dd></div>
          <div><dt>נראה לאחרונה</dt><dd>{relTime(dev.last_heartbeat_at ?? dev.last_sample_received_at, now)}{dev.queue_size ? <> · תור: <Num>{dev.queue_size}</Num></> : null}</dd></div>
          <div><dt>קבוצה / תצורה</dt>
            <dd>{dev.group ?? "—"} · <Num>{`v${dev.config_version ?? "—"}`}</Num>
              {cfgPending ? <> (הוחלה <Num>{`v${dev.applied_config_version}`}</Num> — ממתינה)</> : dev.applied_config_version != null ? " (הוחלה)" : ""}</dd></div>
          <div><dt>דגם / גרסה</dt><dd>{[dev.manufacturer, dev.model].filter(Boolean).join(" ") || "—"} · Android <Num>{dev.os_version ?? "—"}</Num> · app <Num>{dev.app_version ?? "—"}</Num></dd></div>
        </dl>
      </div>

      <div className={d.sec}>
        <div className={d.secH}>
          <h3>{mdef.label} לאורך זמן</h3>
          <select className={s.select} value={metric} onChange={(e) => setMetric(e.target.value as DeviceTsMetric)} aria-label="מדד בגרף">
            {METRICS.map((x) => <option key={x.v} value={x.v}>{x.label}</option>)}
          </select>
        </div>
        {isNsa && metric === "rsrp" && (
          <p className={s.note} style={{ marginBottom: 6 }}>ב-5G NSA הגרף מציג את עוגן ה-LTE. SS-RSRP של רגל ה-NR מוצג בגרף נפרד —&nbsp;
            <button className={s.lnk} onClick={() => setMetric("nr_leg_ss_rsrp")}>הצג רגל NR</button>.</p>
        )}
        {tsQ.isLoading ? <Loading lines={4} /> : tsQ.error ? <ErrorState error={tsQ.error} onRetry={() => tsQ.refetch()} /> : tsQ.data && (
          <>
            <TimeSeriesChart data={tsQ.data} from={tsQ.data.from} to={tsQ.data.to} dbm={mdef.dbm} />
            <p className={s.note}>
              {pts.length} נקודות · ללא שירות: {durationText(oosSec)} · פערים מסומנים בגרף (ללא שירות / אין נתונים / מושהה / נתוני תא ישנים)
            </p>
          </>
        )}
      </div>

      <DeviceCommands device={dev} now={now} />

      <div className={d.sec}>
        <h3>הרשאות וניטור</h3>
        {dev.degraded_reasons.length > 0 && (
          <ul className={d.reasons} aria-label="סיבות לניטור מוגבל">
            {dev.degraded_reasons.map((r) => <li key={r.code}><span aria-hidden="true">◐</span> {r.label_he} <code className={s.muted}>{r.code}</code></li>)}
          </ul>
        )}
        <div className={d.chips}>
          {perms.length === 0 && <span className={s.muted}>המכשיר טרם דיווח הרשאות.</span>}
          {perms.map((p) => (
            <span key={p.label} className={s.chip} style={{ color: p.ok ? "var(--cov-excellent)" : "#ff8fa3" }}>
              <span aria-hidden="true">{p.ok ? "✓" : "✕"}</span>{p.label}{p.text ? `: ${p.text}` : ""}
              <span className={s.srOnly}>{p.ok ? " — מאושר" : " — חסר"}</span>
            </span>
          ))}
          {dev.location_services_enabled === false && <span className={s.chip} style={{ color: "#ff8fa3" }}>✕ שירותי מיקום כבויים</span>}
        </div>
        {dev.open_alerts.length > 0 && (
          <ul className={d.reasons} style={{ marginTop: 8 }} aria-label="התראות פתוחות">
            {dev.open_alerts.map((a) => <li key={a.id}>⚠ {a.type_label_he} — {a.message_he}</li>)}
          </ul>
        )}
      </div>

      <div className={d.sec}>
        <div className={d.act}>
          <button className={s.btnPrimary} onClick={showTrack} disabled={trackBusy || dev.admin_status === "withdrawn"} aria-pressed={!!track}>
            {track ? "הסתר מסלול" : trackBusy ? "טוען מסלול…" : `הצג מסלול (${win === "30d" || win === "7d" ? "7 ימים" : win === "1h" ? "שעה" : "24 ש׳"})`}
          </button>
          <button className={s.btn} onClick={exportCsv}>ייצוא CSV</button>
        </div>
        <p className={s.noteAudit} style={{ marginTop: 8 }} data-testid="track-audit-note">
          צפייה במסלול וייצוא של מכשיר בודד נרשמים ביומן הפעילות (FIELD_TRACK_VIEW / FIELD_EXPORT).
        </p>
        {track && <p className={s.note} style={{ marginTop: 6 }}>
          מסלול: <Num>{track.properties.points_returned}</Num> נקודות{track.properties.thinned ? <> (דולל מ-<Num>{track.properties.points_total}</Num>)</> : ""} · {fmtDateTime(track.properties.from)}–{fmtDateTime(track.properties.to)}
        </p>}
        {trackErr && <p className={s.errbox} role="alert" style={{ marginTop: 6 }}>{trackErr}</p>}
        {exportMsg && <p className={s.note} role="status" style={{ marginTop: 6 }}>{exportMsg}</p>}
        {isAdmin && dev.admin_status !== "deleted" && (
          <div style={{ marginTop: 10 }}>
            <DeviceAdminActions id={dev.id} label={name} ownership={dev.ownership} adminStatus={dev.admin_status} onDone={onClose} />
          </div>
        )}
      </div>
    </aside>
  );
}
