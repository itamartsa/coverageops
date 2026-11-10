/**
 * Field reception live map (spec 8.3 "מפה חיה") — real Leaflet with the app's base-map tiles,
 * layers managed imperatively through refs (the MapView pattern):
 *  - heat grid from GET /heatmap (canvas, tier colour + bars glyph; measure LTE RSRP / NR SS-RSRP),
 *  - dead-zone polygons (dashed, labelled "אזור מת"),
 *  - device markers (ADMIN/OPERATOR only): tier colour + bars glyph + label; offline = dashed ring,
 *    stale cell data = grey, unclassified = empty glyph; coarse-location devices are drawn ONLY as
 *    an approximate ~1 km area, never as a point; paused / no-contact devices are not on the map
 *    (no current location) and are counted in the header instead,
 *  - the selected device's track (only after the user asked for it — the server audit-logs it).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import { useQuery } from "@tanstack/react-query";
import { fieldApi, apiErrorText } from "../../services/api";
import type { DeadZone, DeviceTrack, FieldLiveRow, FieldOperator, HeatMetric } from "../../services/api";
import { useStore } from "../../store/useStore";
import { makeBaseLayer, ISRAEL_CENTER, ISRAEL_MAX_BOUNDS } from "../../lib/baseMaps";
import { HeatGridLayer, LEVEL_TEXT_HE, bboxParam, heatLevelForZoom } from "../../lib/heatGridLayer";
import { barsSvg, tierDef, TIER_ORDER, tierRangeText } from "../../lib/fieldTiers";
import { fmtDbm, fmtDateTime, fmtNum } from "../../lib/fieldFormat";
import { networkLabel, statusLook } from "../../lib/fieldStatus";
import { operatorName, windowRange } from "../../lib/fieldQueries";
import { TierSwatch } from "./primitives";
import s from "./field.module.css";
import m from "./FleetMap.module.css";

const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, (ch) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]!));

export interface FleetMapProps {
  /** null = VIEWER (no device-level layers at all) */
  live: FieldLiveRow[] | null;
  operators?: FieldOperator[];
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  track?: DeviceTrack | null;
  criticalIds?: Set<string>;
  /** "2 מושהים, 1 ללא קשר אינם במפה" */
  notOnMapText?: string;
  height?: number;
  /** filters from the page */
  window: string;
  operator: string | null;
  tech: "LTE" | "NR" | null;
  title?: string;
}

export default function FleetMap(p: FleetMapProps) {
  const elRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const heatRef = useRef<HeatGridLayer | null>(null);
  const devRef = useRef<L.LayerGroup | null>(null);
  const dzRef = useRef<L.LayerGroup | null>(null);
  const trackRef = useRef<L.LayerGroup | null>(null);
  const baseRef = useRef<L.TileLayer | null>(null);
  const fittedRef = useRef(false);
  const activeBaseMapId = useStore((st) => st.activeBaseMapId);

  const [view, setView] = useState<{ bbox: string; zoom: number } | null>(null);
  const [show, setShow] = useState({ heat: true, devices: true, dz: true, track: true });
  const [measure, setMeasure] = useState<"lte_rsrp" | "nr_ss_rsrp">(p.tech === "NR" ? "nr_ss_rsrp" : "lte_rsrp");
  const [metric, setMetric] = useState<HeatMetric>("p50");
  useEffect(() => { if (p.tech) setMeasure(p.tech === "NR" ? "nr_ss_rsrp" : "lte_rsrp"); }, [p.tech]);
  const isViewer = p.live === null;

  // ── init ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (mapRef.current || !elRef.current) return;
    const base = makeBaseLayer(useStore.getState().activeBaseMapId);
    baseRef.current = base;
    const map = L.map(elRef.current, {
      center: ISRAEL_CENTER, zoom: 7, minZoom: 6, maxBounds: ISRAEL_MAX_BOUNDS, layers: [base],
      zoomControl: true, keyboard: true,
    });
    map.zoomControl.setPosition("topleft");
    L.control.scale({ position: "bottomright", imperial: false }).addTo(map);
    let disposed = false;
    const report = () => {
      if (disposed) return;
      const b = map.getBounds();
      // rounded bbox → stable query keys while panning a little
      const r = (v: number) => Math.round(v * 50) / 50;
      const bb = L.latLngBounds([r(b.getSouth()) - 0.02, r(b.getWest()) - 0.02], [r(b.getNorth()) + 0.02, r(b.getEast()) + 0.02]);
      setView({ bbox: bboxParam(bb), zoom: map.getZoom() });
      elRef.current?.classList.toggle(m.labels, map.getZoom() >= 9);
    };
    let t: ReturnType<typeof setTimeout> | null = null;
    map.on("moveend", () => { if (disposed) return; if (t) clearTimeout(t); t = setTimeout(report, 300); });
    report();
    mapRef.current = map;
    if (import.meta.env.DEV) (window as unknown as { __coverageopsFieldMap?: L.Map }).__coverageopsFieldMap = map;
    // The card can change size (drawer opens, tab switch) — keep Leaflet in sync
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(elRef.current);
    return () => {
      disposed = true;
      ro.disconnect();
      if (t) clearTimeout(t);
      map.remove();
      mapRef.current = null;
      // Layers belonged to the removed map — a remount (React StrictMode, tab switch) starts clean
      heatRef.current = null; devRef.current = null; dzRef.current = null; trackRef.current = null;
      baseRef.current = null; fittedRef.current = false;
      if (import.meta.env.DEV) delete (window as unknown as { __coverageopsFieldMap?: L.Map }).__coverageopsFieldMap;
    };
  }, []);

  // Base map follows the user's choice in 🗂 שכבות
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !baseRef.current) return;
    map.removeLayer(baseRef.current);
    baseRef.current = makeBaseLayer(activeBaseMapId).addTo(map);
    baseRef.current.bringToBack();
  }, [activeBaseMapId]);

  // ── heat grid ───────────────────────────────────────────────────────────
  const level = heatLevelForZoom(view?.zoom ?? 7);
  const heatQ = useQuery({
    queryKey: ["field", "heatmap", view?.bbox, level, measure, metric, p.window, p.operator],
    queryFn: () => fieldApi.heatmap({
      ...windowRange(p.window), bbox: view!.bbox, measure, metric, level, operator: p.operator,
    }),
    enabled: !!view && show.heat,
    placeholderData: (prev) => prev,
    refetchInterval: 5 * 60_000, refetchIntervalInBackground: false,
    retry: false,
  });
  const measureLabel = measure === "lte_rsrp" ? "RSRP LTE" : "SS-RSRP NR";
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const d = heatQ.data;
    if (!show.heat || !d) {
      if (heatRef.current) { map.removeLayer(heatRef.current); heatRef.current = null; }
      return;
    }
    const data = { cells: d.cells, cellDeg: d.cell_deg, metric: d.metric, measureLabel };
    if (heatRef.current) heatRef.current.setData(data);
    else heatRef.current = new HeatGridLayer(data).addTo(map);
  }, [heatQ.data, show.heat, measureLabel]);

  // ── dead zones ──────────────────────────────────────────────────────────
  const dzQ = useQuery({
    queryKey: ["field", "dead-zones", p.window, p.operator, p.tech],
    queryFn: () => fieldApi.deadZones({ ...windowRange(p.window === "1h" ? "24h" : p.window), operator: p.operator, tech: p.tech }),
    enabled: show.dz,
    refetchInterval: 5 * 60_000, refetchIntervalInBackground: false,
  });
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (dzRef.current) { map.removeLayer(dzRef.current); dzRef.current = null; }
    if (!show.dz || !dzQ.data?.length) return;
    const g = L.layerGroup();
    for (const z of dzQ.data) drawDeadZone(z, g, p.operators);
    dzRef.current = g.addTo(map);
  }, [dzQ.data, show.dz, p.operators]);

  // ── devices ─────────────────────────────────────────────────────────────
  const live = p.live;
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (devRef.current) { map.removeLayer(devRef.current); devRef.current = null; }
    if (!live || !show.devices) return;
    const g = L.layerGroup();
    for (const d of live) {
      const sel = d.id === p.selectedId;
      const name = d.label ?? d.id.slice(0, 8);
      const tdef = tierDef(d.tier);
      const tierText = d.tier_stale ? `נתוני תא ישנים (${tdef.label_he})` : tdef.label_he;
      const sum = `${name} · ${tierText}${d.rsrp != null ? ` · ${fmtDbm(d.rsrp)}` : ""}`;
      const tip = `<div dir="rtl" style="font-size:11px;line-height:1.5"><b>${esc(name)}</b><br>`
        + `רמה: <b>${esc(tierText)}</b> <bdi dir="ltr">${d.rsrp != null ? esc(fmtDbm(d.rsrp)) : ""}</bdi><br>`
        + `${esc(statusLook(d.status).label)} · ${esc(networkLabel(d.network_type))} · ${esc(operatorName(d.operator_key, p.operators))}`
        + `${d.precision === "coarse" ? "<br><b>מיקום משוער</b> (~1 ק״מ)" : ""}`
        + `<br>מיקום: ${esc(fmtDateTime(d.loc_ts))}</div>`;
      if (d.precision === "coarse") {
        // Approximate area only — never a point
        const c = L.circle([d.lat, d.lon], {
          radius: Math.max(1000, d.acc ?? 1000), color: sel ? "#00d4ff" : "#8fb7d9", weight: sel ? 2.5 : 1.5,
          dashArray: "5 4", fillColor: d.tier_stale ? "#5a7a9a" : tdef.color, fillOpacity: 0.18,
        });
        c.bindTooltip(tip, { className: "field-dev-tip", direction: "top", sticky: true });
        c.on("click", () => p.onSelect?.(d.id));
        c.addTo(g);
        const lbl = L.marker([d.lat, d.lon], {
          icon: L.divIcon({ className: "fdm", html: `<div class="fdm-area" title="${esc(sum)} · מיקום משוער"></div>`, iconSize: [0, 0] }),
          keyboard: true, title: `${sum} · מיקום משוער`, alt: `${sum} · מיקום משוער`,
        });
        lbl.bindTooltip(`מיקום משוער · ${esc(name)}`, { permanent: true, direction: "center", className: "field-coarse-lbl" });
        lbl.on("click", () => p.onSelect?.(d.id));
        lbl.addTo(g);
        continue;
      }
      const fill = d.tier_stale ? "#5a7a9a" : tdef.color;
      const cls = ["fdm", sel ? "fdm-sel" : "", d.status === "offline" ? "fdm-off" : "",
        d.tier_stale ? "fdm-stale" : "", p.criticalIds?.has(d.id) ? "fdm-crit" : ""].join(" ");
      const html = `<div class="fdm-b" style="background:${fill}">`
        + barsSvg(d.tier_stale ? 0 : tdef.bars, { size: 14, fill: "#0a0e14", dim: "rgba(10,14,20,.55)" })
        + `</div><div class="fdm-lbl">${esc(name)} · ${esc(tierText)}${d.rsrp != null ? ` <bdi dir="ltr">${esc(fmtNum(d.rsrp))}</bdi>` : ""}`
        + `${d.status === "offline" ? " · לא מקוון" : ""}</div>`;
      const mk = L.marker([d.lat, d.lon], {
        icon: L.divIcon({ className: cls, html, iconSize: [26, 26], iconAnchor: [13, 13] }),
        keyboard: true, title: sum, alt: sum, riseOnHover: true, zIndexOffset: sel ? 1000 : 0,
      });
      mk.bindTooltip(tip, { className: "field-dev-tip", direction: "top", offset: [0, -14] });
      mk.on("click", () => p.onSelect?.(d.id));
      mk.on("add", () => mk.getElement()?.setAttribute("data-device-id", d.id));
      mk.addTo(g);
    }
    devRef.current = g.addTo(map);
    if (!fittedRef.current && live.length) {
      fittedRef.current = true;
      map.fitBounds(L.latLngBounds(live.map((d) => [d.lat, d.lon] as L.LatLngTuple)).pad(0.15), { maxZoom: 11 });
    }
  }, [live, show.devices, p.selectedId, p.criticalIds, p.operators, p.onSelect]);

  // ── track ───────────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (trackRef.current) { map.removeLayer(trackRef.current); trackRef.current = null; }
    if (!p.track || !show.track) return;
    const g = L.layerGroup();
    const renderer = L.canvas({ padding: 0.3 });
    const pts: L.LatLngTuple[] = [];
    for (const f of p.track.features) {
      const t = f.properties.tier;
      const color = f.properties.stale ? "#7f9bb8" : tierDef(t).color;
      if (f.geometry.type === "LineString") {
        const ll = (f.geometry.coordinates as number[][]).map(([lon, lat]) => [lat, lon] as L.LatLngTuple);
        pts.push(...ll);
        L.polyline(ll, { color, weight: 4, opacity: 0.9, dashArray: f.properties.stale ? "4 5" : undefined })
          .bindTooltip(`${tierDef(t).label_he}${f.properties.stale ? " · נתוני תא ישנים" : ""} · ${fmtDateTime(f.properties.from)}–${fmtDateTime(f.properties.to)}`,
            { sticky: true, className: "field-dev-tip" })
          .addTo(g);
      } else if (f.geometry.type === "Polygon") {
        const ll = (f.geometry.coordinates[0] as number[][]).map(([lon, lat]) => [lat, lon] as L.LatLngTuple);
        pts.push(...ll);
        L.polygon(ll, { color: "#8fb7d9", weight: 1, dashArray: "4 4", fillColor: color, fillOpacity: 0.2 })
          .bindTooltip(`מיקום משוער (תא ~1 ק״מ) · ${fmtDateTime(f.properties.ts)}`, { sticky: true, className: "field-dev-tip" })
          .addTo(g);
      } else if (f.geometry.type === "Point") {
        const [lon, lat] = f.geometry.coordinates as number[];
        L.circleMarker([lat, lon], { renderer, radius: 2.5, color: "#0a0e14", weight: 1, fillColor: color, fillOpacity: 1 })
          .bindTooltip(`${fmtDateTime(f.properties.ts)} · ${tierDef(t).label_he}${f.properties.rsrp_dbm != null ? ` · ${fmtDbm(f.properties.rsrp_dbm)}` : ""}`,
            { className: "field-dev-tip" })
          .addTo(g);
      }
    }
    trackRef.current = g.addTo(map);
    if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.1), { maxZoom: 13 });
  }, [p.track, show.track]);

  function fitDevices() {
    const map = mapRef.current;
    if (!map || !live?.length) return;
    map.fitBounds(L.latLngBounds(live.map((d) => [d.lat, d.lon] as L.LatLngTuple)).pad(0.15), { maxZoom: 12 });
  }

  // Focus the selected device when it changes (from the table)
  const selected = useMemo(() => live?.find((d) => d.id === p.selectedId) ?? null, [live, p.selectedId]);
  const lastFocus = useRef<string | null>(null);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selected || lastFocus.current === selected.id) return;
    lastFocus.current = selected.id;
    if (!map.getBounds().pad(-0.1).contains([selected.lat, selected.lon])) {
      map.setView([selected.lat, selected.lon], Math.max(map.getZoom(), 10));
    }
  }, [selected]);

  const heatErr = heatQ.error ? apiErrorText(heatQ.error, "שגיאה בטעינת מפת החום") : null;
  const hd = heatQ.data;
  const nDev = live?.length ?? 0;
  const nCoarse = live?.filter((d) => d.precision === "coarse").length ?? 0;

  return (
    <section className={`${s.card} ${m.card}`} aria-label="מפה חיה" data-testid="fleet-map-card">
      <div className={s.cardH}>
        <h2>{p.title ?? "מפה חיה"}</h2>
        <span className={s.sub}>
          {isViewer
            ? "מפת חום מצרפית ואזורים מתים בלבד — ללא מיקומי מכשירים"
            : <>{nDev} מכשירים עם מיקום{nCoarse ? ` · ${nCoarse} במיקום משוער מוצגים כאזור` : ""}{p.notOnMapText ? ` · ${p.notOnMapText}` : ""}</>}
        </span>
        <div className={s.tools}>
          <label className={m.sel}>
            <span className={s.srOnly}>מדד מפת החום</span>
            <select className={s.select} value={measure} onChange={(e) => setMeasure(e.target.value as typeof measure)}
              aria-label="מדד מפת החום">
              <option value="lte_rsrp">RSRP LTE (כולל עוגן NSA)</option>
              <option value="nr_ss_rsrp">SS-RSRP NR (5G SA)</option>
            </select>
          </label>
          <select className={s.select} value={metric} onChange={(e) => setMetric(e.target.value as HeatMetric)} aria-label="ערך התא">
            <option value="p50">חציון (P50)</option>
            <option value="p5">אחוזון 5 (P5)</option>
            <option value="oos_pct">% זמן ללא שירות</option>
          </select>
          {!isViewer && <button className={s.btn} onClick={fitDevices} disabled={!nDev}>התמקד בכל המכשירים</button>}
        </div>
      </div>

      <div className={m.mapWrap} style={{ height: p.height ?? 460 }}>
        <div ref={elRef} className={m.map} data-testid="fleet-map" />
        <fieldset className={m.layers}>
          <legend className={s.srOnly}>שכבות</legend>
          <label><input type="checkbox" checked={show.heat} onChange={(e) => setShow({ ...show, heat: e.target.checked })} /> מפת חום ({LEVEL_TEXT_HE[level]})</label>
          {!isViewer && <label><input type="checkbox" checked={show.devices} onChange={(e) => setShow({ ...show, devices: e.target.checked })} /> מכשירים</label>}
          <label><input type="checkbox" checked={show.dz} onChange={(e) => setShow({ ...show, dz: e.target.checked })} /> אזורים מתים</label>
          {!isViewer && p.track && <label><input type="checkbox" checked={show.track} onChange={(e) => setShow({ ...show, track: e.target.checked })} /> מסלול נבחר</label>}
        </fieldset>
        {(heatQ.isFetching || dzQ.isFetching) && <span className={m.busy} role="status" aria-label="טוען שכבות"><span className={s.spinner} /></span>}
        {heatErr && <div className={m.mapMsg} role="alert">⚠ {heatErr}</div>}
        {!heatErr && hd && show.heat && hd.cells.length === 0 && (
          <div className={m.mapMsg}>אין תאי מדידה ב{measure === "nr_ss_rsrp" ? "-SS-RSRP NR" : "-RSRP LTE"} בתחום ובחלון הזמן</div>
        )}
      </div>

      <ul className={s.legend} aria-label="מקרא המפה" data-testid="fleet-map-legend">
        <li className={s.lgH}>{metric === "oos_pct" ? `${measureLabel} · % זמן ללא שירות` : `${measureLabel} (dBm) · ${metric === "p5" ? "אחוזון 5" : "חציון"}`}</li>
        {metric === "oos_pct" ? (
          <li><span className={s.lgSw} style={{ background: "linear-gradient(90deg, rgba(255,59,92,.15), rgba(255,59,92,.85))", width: 40 }} /> 0% → 100% ללא שירות</li>
        ) : TIER_ORDER.map((t) => (
          <li key={t}><TierSwatch tier={t} /> {tierDef(t).label_he} <bdi dir="ltr" className={s.num}>{tierRangeText(t)}</bdi></li>
        ))}
        {!isViewer && <>
          <li><span className={s.lgMk} style={{ background: "var(--cov-unk)" }} dangerouslySetInnerHTML={{ __html: barsSvg(0, { size: 10, dim: "#0a0e14" }) }} /> לא מסווג / נתוני תא ישנים</li>
          <li><span className={s.lgMk} style={{ background: "transparent", border: "2px dashed #ff8fa3" }} /> לא מקוון (מיקום אחרון)</li>
          <li><span className={s.lgMk} style={{ background: "rgba(143,183,217,.2)", border: "1.5px dashed #8fb7d9" }} /> מיקום משוער (~1 ק״מ, ללא נקודה)</li>
        </>}
        <li><span className={s.lgSw} style={{ background: "rgba(255,59,92,.18)", border: "1.5px dashed var(--danger)" }} /> אזור מת</li>
        {hd?.includes_coarse && <li className={s.muted}>ברמת 2.5 ק״מ נכללות גם דגימות במיקום משוער</li>}
        {isViewer && hd && hd.suppressed > 0 && (
          <li className={s.muted} data-testid="heat-suppressed">{hd.suppressed} תאים הוסתרו (פחות מ-{hd.k_min ?? 3} מכשירים)</li>
        )}
        {isViewer && <li className={s.muted}>לצופה: רזולוציית יום</li>}
      </ul>
    </section>
  );
}

function drawDeadZone(z: DeadZone, g: L.LayerGroup, ops?: FieldOperator[]) {
  const polys = z.polygon.coordinates.map((poly) => poly[0].map(([lon, lat]) => [lat, lon] as L.LatLngTuple));
  const label = `אזור מת · ${operatorName(z.operator_key, ops)} ${z.tech}`;
  const detail = `${z.cells} תאים · <bdi dir="ltr">${fmtNum(z.area_km2, 2)} km²</bdi> · P50 <bdi dir="ltr">${z.p50 != null ? fmtDbm(z.p50) : "—"}</bdi>`
    + ` · ללא שירות <bdi dir="ltr">${fmtNum(z.oos_pct, 0)}%</bdi>${z.n_devices != null ? ` · ${z.n_devices} מכשירים` : ""}`;
  const poly = L.polygon(polys, {
    color: "#ff3b5c", weight: 1.5, dashArray: "5 4",
    fillColor: "#ff3b5c", fillOpacity: z.severity === "critical" ? 0.22 : 0.12,
  });
  poly.bindTooltip(`<div dir="rtl"><b>⚠ ${esc(label)}</b>${z.severity === "critical" ? " (קריטי)" : ""}<br>${detail}</div>`,
    { sticky: true, className: "field-dev-tip" });
  poly.addTo(g);
  L.marker([z.centroid_lat, z.centroid_lon], {
    icon: L.divIcon({ className: "fdm", html: "", iconSize: [0, 0] }), interactive: false, keyboard: false,
  }).bindTooltip(`⚠ ${esc(label)}`, { permanent: true, direction: "right", offset: [8, 0], className: "field-dz-lbl" }).addTo(g);
}
