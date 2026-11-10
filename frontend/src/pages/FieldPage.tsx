/**
 * /field — field-reception dashboard (FIELD_DASHBOARD_SPEC section 8, M2 + M3 UI).
 * Every logged-in role; content depends on the role (VIEWER: aggregates only — spec 9.4).
 * The active tab and the selected device live in the URL (?tab=…&device=…) so links are
 * shareable and Back works; filters live in the store and apply to every tab.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useStore, FIELD_WINDOWS, type FieldWindowKey } from "../store/useStore";
import type { DeviceTrack } from "../services/api";
import { canSeeDevices, isAdmin as isAdminRole, FIELD_AGGREGATE_ONLY_NOTE } from "../lib/roles";
import {
  useDevices, useGroups, useLive, useOpenAlerts, useOperators, useSummary, WINDOW_LABEL_HE,
} from "../lib/fieldQueries";
import { fmtTime, olderThan, relTime } from "../lib/fieldFormat";
import TopBar from "../components/TopBar";
import KpiTiles, { KpiSkeleton } from "../components/field/KpiTiles";
import FleetMap from "../components/field/FleetMap";
import DeviceTable from "../components/field/DeviceTable";
import DeviceDrawer from "../components/field/DeviceDrawer";
import AlertsList from "../components/field/AlertsList";
import AnalysisTab from "../components/field/AnalysisTab";
import PredictTab from "../components/field/PredictTab";
import AdminTab from "../components/field/AdminTab";
import DemoControls from "../components/field/DemoControls";
import RequirementsPanel from "../components/field/RequirementsPanel";
import { ErrorState, Loading } from "../components/field/primitives";
import s from "../components/field/field.module.css";
import t from "../components/field/FieldTabs.module.css";
import p from "./FieldPage.module.css";
import "../components/field/field-global.css";

type TabId = "overview" | "live" | "devices" | "analysis" | "predict" | "alerts" | "admin";
const TABS: { id: TabId; label: string; ao?: boolean }[] = [
  { id: "overview", label: "סקירה" },
  { id: "live", label: "מפה חיה" },
  { id: "devices", label: "מכשירים", ao: true },
  { id: "analysis", label: "ניתוח" },
  { id: "predict", label: "חיזוי" },
  { id: "alerts", label: "התראות", ao: true },
  { id: "admin", label: "ניהול מכשירים", ao: true },
];

export default function FieldPage() {
  const user = useStore((st) => st.user);
  const filters = useStore((st) => st.fieldFilters);
  const setFilters = useStore((st) => st.setFieldFilters);
  const role = user?.role;
  const ao = canSeeDevices(role);
  const admin = isAdminRole(role);
  const isViewer = !ao;

  const [params, setParams] = useSearchParams();
  const requested = (params.get("tab") as TabId) || "overview";
  const tab: TabId = TABS.some((x) => x.id === requested) ? requested : "overview";
  const forbiddenTab = isViewer && !!TABS.find((x) => x.id === tab)?.ao;
  const deviceId = ao ? params.get("device") : null;

  const [reqOpen, setReqOpen] = useState(false);
  const [enrollOpen, setEnrollOpen] = useState(false);
  const [track, setTrack] = useState<DeviceTrack | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(id); }, []);
  useEffect(() => { setTrack(null); }, [deviceId]);

  // ── data (VIEWER never requests device-level endpoints) ───────────────────
  const summaryQ = useSummary(filters, isViewer);
  const operatorsQ = useOperators();
  const groupsQ = useGroups(ao);
  const devicesQ = useDevices(ao, filters.groupId);
  const liveQ = useLive(ao && (tab === "overview" || tab === "live"));
  const alertsQ = useOpenAlerts(ao);
  const operators = operatorsQ.data;

  const setTab = (id: TabId) => {
    const next = new URLSearchParams(params);
    next.set("tab", id);
    if (id === "analysis" || id === "predict" || id === "alerts" || id === "admin") next.delete("device");
    setParams(next);
  };
  const selectDevice = useCallback((id: string | null) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (id) next.set("device", id); else next.delete("device");
      if (id && !["overview", "live", "devices"].includes(next.get("tab") ?? "overview")) next.set("tab", "devices");
      return next;
    });
  }, [setParams]);
  const onSelect = useCallback((id: string) => selectDevice(id), [selectDevice]);

  const criticalIds = useMemo(() => new Set((alertsQ.data ?? [])
    .filter((a) => a.severity === "critical" && a.device_id).map((a) => a.device_id!)), [alertsQ.data]);
  const devices = devicesQ.data?.rows ?? [];
  const live = liveQ.data?.rows ?? [];
  const notOnMap = useMemo(() => {
    const c = (st: string) => devices.filter((d) => d.status === st).length;
    const parts = [
      c("paused") && `${c("paused")} מושהים`, c("no_contact") && `${c("no_contact")} ללא קשר`,
      c("never_seen") && `${c("never_seen")} טרם התחברו`,
    ].filter(Boolean);
    return parts.length ? `${parts.join(", ")} — ללא מיקום עדכני, לא במפה` : "";
  }, [devices]);

  const summary = summaryQ.data;
  const fleetEmpty = summary && summary.fleet.total === 0;
  const lastRx = summary?.freshness.last_sample_received_at ?? null;
  const staleData = !!summary && !fleetEmpty && (lastRx == null || olderThan(lastRx, 15, now));
  const updatedAt = summaryQ.dataUpdatedAt;
  const nAlerts = (alertsQ.data ?? []).length;
  const tech = filters.techs.length === 1 ? filters.techs[0] : null;
  const opFilter = filters.operators.join(",") || null;

  const drawer = deviceId ? (
    <DeviceDrawer key={deviceId} id={deviceId} window={filters.window} operators={operators} isAdmin={admin}
      onClose={() => selectDevice(null)} track={track} onTrack={setTrack} now={now} />
  ) : null;

  const map = (height: number, title?: string) => (
    <FleetMap live={ao ? live : null} operators={operators} selectedId={deviceId} onSelect={onSelect}
      track={track} criticalIds={criticalIds} notOnMapText={notOnMap} height={height} title={title}
      window={filters.window} operator={opFilter} tech={tech} />
  );

  function emptyFleet() {
    return (
      <section className={s.card} data-testid="field-empty">
        <div className={s.state}>
          <h3>אין מכשירים רשומים</h3>
          <p>עדיין לא צורפו מכשירי שטח, ולכן אין מדידות להצגה.</p>
          {admin ? (
            <button className={s.btnPrimary} onClick={() => { setTab("admin"); setEnrollOpen(true); }}>צור קוד צירוף</button>
          ) : <p className={s.muted}>לצירוף מכשירים פנו למנהל המערכת.</p>}
          {admin && <DemoControls compact />}
          <button className={s.lnk} onClick={() => setReqOpen(true)}>דרישות מערכת ליחידות הקצה</button>
        </div>
      </section>
    );
  }

  function content() {
    if (forbiddenTab) {
      return <section className={s.card}><div className={s.state} data-testid="viewer-forbidden">{FIELD_AGGREGATE_ONLY_NOTE}</div></section>;
    }
    if (tab === "admin") {
      return <AdminTab isAdmin={admin} devices={devices} onShowRequirements={() => setReqOpen(true)} now={now}
        enrollOpen={enrollOpen} setEnrollOpen={setEnrollOpen} />;
    }
    if (summaryQ.isLoading && tab !== "predict") return <KpiSkeleton />;
    if (summaryQ.error && tab !== "predict") return <section className={s.card}><ErrorState error={summaryQ.error} onRetry={() => summaryQ.refetch()} what="סיכום" /></section>;
    if (fleetEmpty && tab !== "predict") return emptyFleet();

    switch (tab) {
      case "overview":
        return (
          <div className={t.stack}>
            {summary && <KpiTiles summary={summary} alerts={alertsQ.data} showAlerts={ao} />}
            <div className={drawer ? t.live : t.liveSolo}>
              {map(460)}
              {drawer}
            </div>
            {ao && (devicesQ.isLoading ? <Loading /> : devicesQ.error
              ? <section className={s.card}><ErrorState error={devicesQ.error} onRetry={() => devicesQ.refetch()} what="מכשירים" /></section>
              : <DeviceTable rows={devices} operators={operators} selectedId={deviceId} onSelect={onSelect} compact now={now} />)}
            {ao && <AlertsList preview={5} onOpenDevice={onSelect} onShowAll={() => setTab("alerts")} now={now} />}
          </div>
        );
      case "live":
        return <div className={drawer ? t.live : t.liveSolo}>{map(620)}{drawer}</div>;
      case "devices":
        return (
          <div className={drawer ? t.live : t.liveSolo}>
            {devicesQ.isLoading ? <Loading /> : devicesQ.error
              ? <section className={s.card}><ErrorState error={devicesQ.error} onRetry={() => devicesQ.refetch()} what="מכשירים" /></section>
              : <DeviceTable rows={devices} operators={operators} selectedId={deviceId} onSelect={onSelect} now={now} />}
            {drawer}
          </div>
        );
      case "analysis":
        return <AnalysisTab filters={filters} isViewer={isViewer} operators={operators} summary={summary} />;
      case "predict":
        return <PredictTab summary={summary} />;
      case "alerts":
        return <AlertsList onOpenDevice={onSelect} now={now} />;
    }
  }

  return (
    <div className={p.page}>
      <TopBar />
      <div className={p.scroll}>
        <header className={p.fh}>
          <h1>קליטה בשטח <small>FIELD RECEPTION</small></h1>
          <button className={p.infoBtn} onClick={() => setReqOpen(true)} aria-label="דרישות מערכת ליחידות הקצה"
            title="דרישות מערכת ליחידות הקצה" data-testid="requirements-btn">ⓘ</button>
          <span className={p.fresh} aria-live="polite" data-testid="freshness">
            <span className={summaryQ.isFetching ? p.dotBusy : p.dot} aria-hidden="true" />
            {updatedAt ? `עודכן ${relTime(new Date(updatedAt), now)}` : "טוען…"} · רענון אוטומטי
          </span>
          <div className={p.filters} role="group" aria-label="סינון">
            <label className={p.fsel}><span className={p.k}>חלון זמן</span>
              <select value={filters.window} onChange={(e) => setFilters({ window: e.target.value as FieldWindowKey })}>
                {FIELD_WINDOWS.map((w) => <option key={w} value={w}>{WINDOW_LABEL_HE[w]}</option>)}
              </select></label>
            {ao && (
              <label className={p.fsel}><span className={p.k}>קבוצה</span>
                <select value={filters.groupId ?? ""} onChange={(e) => setFilters({ groupId: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">כל הקבוצות</option>
                  {(groupsQ.data ?? []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                </select></label>
            )}
            <label className={p.fsel}><span className={p.k}>מפעיל</span>
              <select value={filters.operators[0] ?? ""} onChange={(e) => setFilters({ operators: e.target.value ? [e.target.value] : [] })}>
                <option value="">הכל</option>
                {(operators ?? []).map((o) => <option key={o.operator_key} value={o.operator_key}>{o.name_he}</option>)}
              </select></label>
            <label className={p.fsel} title="משפיע על מפת החום, האזורים המתים והמגמה; אריחי ה-KPI מציגים תמיד LTE ו-NR בנפרד">
              <span className={p.k}>טכנולוגיה</span>
              <select value={tech ?? ""} onChange={(e) => setFilters({ techs: e.target.value ? [e.target.value as "LTE" | "NR"] : [] })}>
                <option value="">LTE + 5G</option><option value="LTE">LTE (כולל עוגן NSA)</option><option value="NR">5G NR SA</option>
              </select></label>
          </div>
        </header>

        <nav className={p.tabs} role="tablist" aria-label="לשוניות קליטה בשטח">
          {TABS.filter((x) => !x.ao || ao).map((x) => (
            <button key={x.id} role="tab" className={p.tab} aria-selected={tab === x.id} onClick={() => setTab(x.id)}
              data-tab={x.id}>
              {x.label}
              {x.id === "alerts" && nAlerts > 0 && <span className={p.cnt}>{nAlerts}</span>}
              {x.id === "predict" && <span className={p.soon}>בפיתוח</span>}
            </button>
          ))}
        </nav>

        <main className={p.main} role="tabpanel" aria-label={TABS.find((x) => x.id === tab)?.label}>
          {isViewer && <p className={p.viewerNote} data-testid="viewer-note">{FIELD_AGGREGATE_ONLY_NOTE}</p>}
          {staleData && (
            <p className={p.staleBanner} role="status" data-testid="stale-banner">
              ⚠ לא התקבלו מדידות {lastRx ? `מאז ${fmtTime(lastRx)} (${relTime(lastRx, now)})` : "בחלון הזמן"}.
            </p>
          )}
          {content()}
        </main>
      </div>
      {reqOpen && <RequirementsPanel onClose={() => setReqOpen(false)} />}
    </div>
  );
}
