import { useEffect, useMemo, useRef } from "react";
import L from "leaflet";
import { useStore } from "../store/useStore";
import { notify } from "./Notification";
import api, { apiErrorText, apiErrorTextAsync, sitesApi, coverageApi, cellSitesApi } from "../services/api";
import type { BestServersResult, CellSiteDetail, CellSiteRow, Site } from "../services/api";
import {
  CELL_OPERATORS, canEditCellSiteHeight, canPromoteCellSite, cellSiteBands, cellSiteHeightFormHtml,
  cellSitePopupHtml, cellSitePromoteFormHtml, cellSiteRfFormHtml, isManualHeight,
} from "../lib/cellSites";
import { levelColor, levelHe, fmtDb, fmtKm } from "../lib/link";
import { canWrite } from "../lib/roles";
import { fmtDbm } from "../services/coverageFormat";
import styles from "./MapView.module.css";

/** Canvas style of a civil cell-site marker. Manual (user-entered) heights get a dark, thicker ring. */
function cellSiteMarkerStyle(s: CellSiteRow): L.CircleMarkerOptions {
  const color = (CELL_OPERATORS[s.operator] ?? CELL_OPERATORS.OTHER).color;
  const building = s.status === "construction";
  const manual = isManualHeight(s);
  return {
    radius: building ? 5 : 4,
    color: manual ? "#111827" : building ? color : "#ffffff",
    weight: manual ? 2 : building ? 2 : 1,
    dashArray: building ? "2 2" : undefined,
    fillColor: color,
    fillOpacity: building ? 0.15 : 0.9,
  };
}

// Coverage colors matching backend levels
const LEVEL_COLORS: Record<string, string> = {
  excellent: "#00ff88",
  good:      "#7dff6b",
  medium:    "#ffe600",
  weak:      "#ff8c00",
  marginal:  "#ff3b5c",
};

const FREQ_COLORS: Record<number, string> = {
  700: "#ff6b35", 850: "#ffb347", 900:  "#00ff88",
  1800: "#00d4ff", 2100: "#a78bfa", 2600: "#f472b6",
  3500: "#f59e0b",
};

// ── Propagation helpers (frontend LOS estimate) ───────────────────────────
function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const p1 = lat1 * Math.PI / 180, p2 = lat2 * Math.PI / 180;
  const dp = (lat2 - lat1) * Math.PI / 180;
  const dl = (lon2 - lon1) * Math.PI / 180;
  const a  = Math.sin(dp/2)**2 + Math.cos(p1)*Math.cos(p2)*Math.sin(dl/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

const escHtml = (v: unknown) =>
  String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

function fsplDb(dist_km: number, freq_mhz: number): number {
  if (dist_km <= 0) return 0;
  return 20*Math.log10(dist_km*1000) + 20*Math.log10(freq_mhz*1e6) - 147.55;
}

export default function MapView() {
  const mapRef          = useRef<L.Map | null>(null);
  const mapElRef        = useRef<HTMLDivElement>(null);
  // Site markers + the Site object each was built from (rebuilt when the site changes)
  const markersRef      = useRef<Map<number, { marker: L.Marker; site: Site }>>(new Map());
  const polyLayerRef    = useRef<L.Rectangle | null>(null);
  const bboxDragRef     = useRef<L.Marker | null>(null);
  const losLayersRef    = useRef<L.Polyline[]>([]);
  const routeLayerRef   = useRef<L.LayerGroup | null>(null);
  const crossSectionRouteRef = useRef<L.LayerGroup | null>(null);

  // ── Layer management refs ────────────────────────────────────────────────────
  // Base tile layers keyed by BaseMapId — created once, swapped on demand
  const baseLayersRef   = useRef<Map<string, L.TileLayer>>(new Map());
  const activeBaseRef   = useRef<L.TileLayer | null>(null);
  // Coverage PNG overlays — keyed by layer id
  const covOverlaysRef  = useRef<Map<string, L.ImageOverlay>>(new Map());
  const covBlobsRef     = useRef<Map<string, string>>(new Map());
  // Coverage PNGs currently being fetched — prevents duplicate fetches when
  // mapLayers changes mid-flight (each duplicate used to leave an orphaned
  // overlay on the map that no "clear"/"remove" could reach).
  const covPendingRef   = useRef<Set<string>>(new Set());
  // Special overlays
  const demOverlayRef   = useRef<L.ImageOverlay | null>(null);
  const demBlobUrlRef   = useRef<string | null>(null);
  // Civil cellular sites (gov data) — canvas-rendered for ~9k points
  const cellSitesRef     = useRef<L.LayerGroup | null>(null);
  const cellSitesDataRef = useRef<CellSiteRow[] | null>(null);
  const cellCanvasRef    = useRef<L.Canvas | null>(null);
  const cellMarkersRef   = useRef<Map<number, L.CircleMarker>>(new Map());
  const cellDetailsRef   = useRef<Map<number, CellSiteDetail>>(new Map());
  // Point-to-point link
  const linkRxMarkerRef  = useRef<L.Marker | null>(null);
  const linkLayerRef     = useRef<L.LayerGroup | null>(null);
  const bestLayerRef     = useRef<L.LayerGroup | null>(null);

  const {
    sites, drawMode, setDrawMode, bbox,
    isCalculating, activeTab, activeSiteId, editingSite,
    selectedSiteIds, routeWaypoints,
    crossSectionResult, crossSectionSiteId, linkTransmitter, mapFitRequest,
    activeBaseMapId, mapLayers,
    cellSiteFilter, cellSitesVersion,
    linkReceiver, linkResult, bestServersResult, highlightedServerId, linkSubMode,
    linkPickTransmitter, setLinkPickTransmitter,
  } = useStore();

  // ── Init map ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (mapRef.current || !mapElRef.current) return;

    const mkTile = (url: string, opts = {}) => L.tileLayer(url, opts);

    const layers: Record<string, L.TileLayer> = {
      "esri-street": mkTile(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        { attribution: "© Esri © OpenStreetMap", maxZoom: 20 }
      ),
      "esri-gray": mkTile(
        "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        { attribution: "© Esri © OpenStreetMap", maxZoom: 16 }
      ),
      "stadia-dark": mkTile(
        "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png",
        { attribution: "© Stadia Maps © OpenMapTiles © OpenStreetMap", maxZoom: 20 }
      ),
      "stadia-osm": mkTile(
        "https://tiles.stadiamaps.com/tiles/osm_bright/{z}/{x}/{y}{r}.png",
        { attribution: "© Stadia Maps © OpenMapTiles © OpenStreetMap", maxZoom: 20 }
      ),
      "topo": mkTile(
        "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        { attribution: "© OpenTopoMap", maxZoom: 17 }
      ),
      "satellite": mkTile(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        { attribution: "© Esri World Imagery", maxZoom: 18 }
      ),
    };

    Object.entries(layers).forEach(([id, layer]) => baseLayersRef.current.set(id, layer));

    const initialId = useStore.getState().activeBaseMapId ?? "esri-street";
    const initialLayer = layers[initialId] ?? layers["esri-street"];
    activeBaseRef.current = initialLayer;

    const map = L.map(mapElRef.current, {
      center: [31.5, 35.0],
      zoom: 8,
      minZoom: 7,
      maxBounds: [[29.0, 33.5], [33.8, 36.5]],
      zoomControl: false,
      layers: [initialLayer],
    });

    L.control.zoom({ position: "topleft" }).addTo(map);

    // Coord + elevation display
    // Elevation is fetched with debounce (600ms) to avoid flooding the backend
    let elevTimer: ReturnType<typeof setTimeout> | null = null;
    map.on("mousemove", (e) => {
      const el = document.getElementById("coord-display");
      if (!el) return;

      const { lat, lng } = e.latlng;
      // Update coords immediately
      el.textContent = `N ${lat.toFixed(5)}°  E ${lng.toFixed(5)}°`;

      // Debounce elevation fetch
      if (elevTimer) clearTimeout(elevTimer);
      elevTimer = setTimeout(async () => {
        try {
          // Shared axios instance → JWT attached by the interceptor
          const data = (await api.get<{ elevation_m: number }>(
            "/api/coverage/point-elevation", { params: { lat, lon: lng } },
          )).data;
          if (el && data?.elevation_m != null) {
            el.textContent = `N ${lat.toFixed(5)}°  E ${lng.toFixed(5)}°  |  ${data.elevation_m} מ' ASL`;
          }
        } catch {
          // silently ignore network errors
        }
      }, 600);
    });

    mapRef.current = map;
    // Dev builds only: expose the map so end-to-end tests can convert lat/lon ↔ screen points
    if (import.meta.env.DEV) (window as unknown as { __coverageopsMap?: L.Map }).__coverageopsMap = map;
  }, []);

  // ── Drag & Drop Map Draw Handler ─────────────────────────────────────────────
  useEffect(() => {
    const mapOrNull = mapRef.current;
    if (!mapOrNull) return;
    // Explicitly non-null so the hoisted handler functions below keep the narrowing
    const map: L.Map = mapOrNull;

    let drawingStart: L.LatLng | null = null;
    let tempRect: L.Rectangle | null = null;

    function onMouseDown(e: L.LeafletMouseEvent) {
      const mode = useStore.getState().drawMode;
      if (!mode) return;

      if (mode === "point") {
        useStore.getState().setPickedLocation({ lat: e.latlng.lat, lon: e.latlng.lng });
        useStore.getState().setDrawMode(null);
        notify("✅ מיקום נקלט בהצלחה");
        return;
      }

      if (mode === "linkpoint") {
        useStore.getState().setLinkReceiver({ lat: e.latlng.lat, lon: e.latlng.lng });
        useStore.getState().setDrawMode(null);
        notify("📍 מיקום המקלט נקלט — ניתן לגרור את הסמן לכוונון");
        return;
      }

      if (mode === "waypoint") {
        const prev = useStore.getState().routeWaypoints;
        useStore.getState().setRouteWaypoints([...prev, { lat: e.latlng.lat, lon: e.latlng.lng }]);
        return;
      }

      if (mode === "rect" || mode === "square") {
        drawingStart = e.latlng;
        document.documentElement.classList.add("drawing");
      }
    }

    function onMouseMove(e: L.LeafletMouseEvent) {
      if (!drawingStart) return;
      const mode = useStore.getState().drawMode;
      const p1 = drawingStart;
      const p2 = e.latlng;
      let sw: L.LatLngLiteral, ne: L.LatLngLiteral;

      if (mode === "square") {
        const side = Math.max(Math.abs(p2.lat - p1.lat), Math.abs(p2.lng - p1.lng));
        sw = { lat: Math.min(p1.lat, p1.lat - side), lng: p1.lng };
        ne = { lat: Math.max(p1.lat, p1.lat - side) + side, lng: p1.lng + side };
      } else {
        sw = { lat: Math.min(p1.lat, p2.lat), lng: Math.min(p1.lng, p2.lng) };
        ne = { lat: Math.max(p1.lat, p2.lat), lng: Math.max(p1.lng, p2.lng) };
      }

      if (tempRect) map.removeLayer(tempRect);
      tempRect = L.rectangle(L.latLngBounds(sw, ne), {
        color: "#3b82f6", weight: 2, fillOpacity: 0.1, dashArray: "4,4"
      }).addTo(map);
    }

    function finishDraw() {
      document.documentElement.classList.remove("drawing");

      if (!drawingStart) return;

      if (tempRect) {
        const bounds = tempRect.getBounds();
        const sw = bounds.getSouthWest();
        const ne = bounds.getNorthEast();

        map.removeLayer(tempRect);
        tempRect = null;

        // Only commit if it's an actual area (not a single click)
        if (Math.abs(sw.lat - ne.lat) > 0.001 && Math.abs(sw.lng - ne.lng) > 0.001) {
          useStore.getState().setBbox({
            sw_lat: sw.lat, sw_lon: sw.lng,
            ne_lat: ne.lat, ne_lon: ne.lng,
          });
          notify("✅ תא שטח הוגדר באופן תקין ויזואלית");
        }
      }
      drawingStart = null;
      useStore.getState().setDrawMode(null);
    }

    // Listen on the document so mouseup outside the map still ends the draw
    function onDocMouseUp() { finishDraw(); }

    map.on("mousedown", onMouseDown);
    map.on("mousemove", onMouseMove);
    document.addEventListener("mouseup", onDocMouseUp);

    return () => {
      map.off("mousedown", onMouseDown);
      map.off("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onDocMouseUp);
      document.documentElement.classList.remove("drawing");
      if (tempRect) map.removeLayer(tempRect);
    };
  }, []);

  // ── Base map switching ────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const next = baseLayersRef.current.get(activeBaseMapId);
    if (!next || next === activeBaseRef.current) return;
    if (activeBaseRef.current) map.removeLayer(activeBaseRef.current);
    next.addTo(map);
    next.setZIndex(0);
    activeBaseRef.current = next;
  }, [activeBaseMapId]);

  // ── DEM overlay (from layers panel) ──────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const demVisible = mapLayers.find((l) => l.id === "dem")?.visible ?? false;

    // Removes the current DEM overlay and revokes its object URL
    const clearDem = () => {
      if (demOverlayRef.current) { map.removeLayer(demOverlayRef.current); demOverlayRef.current = null; }
      if (demBlobUrlRef.current) { URL.revokeObjectURL(demBlobUrlRef.current); demBlobUrlRef.current = null; }
    };

    let ctrl: AbortController | null = null;
    let lastErr = "";
    const updateDem = async () => {
      ctrl?.abort();                       // drop any in-flight request for an older viewport
      const myCtrl = ctrl = new AbortController();
      const bounds = map.getBounds();
      const sw = bounds.getSouthWest();
      const ne = bounds.getNorthEast();
      try {
        // Shared axios instance → JWT attached by the interceptor
        const { data } = await api.get<Blob>("/api/coverage/topo-overlay", {
          params: { sw_lat: sw.lat, sw_lon: sw.lng, ne_lat: ne.lat, ne_lon: ne.lng, width: 800, height: 800 },
          responseType: "blob",
          signal: myCtrl.signal,
        });
        if (myCtrl.signal.aborted) return;
        clearDem();
        const url = URL.createObjectURL(data);
        demBlobUrlRef.current = url;
        demOverlayRef.current = L.imageOverlay(url, bounds, { opacity: 0.55, zIndex: 200 }).addTo(map);
        lastErr = "";
      } catch (err: any) {
        if (myCtrl.signal.aborted || err?.code === "ERR_CANCELED") return;
        // e.g. 413/400 with a Hebrew `detail` for oversized areas — notify once per distinct message
        const msg = await apiErrorTextAsync(err, "שגיאה בטעינת שכבת טופוגרפיה");
        if (msg !== lastErr) { lastErr = msg; notify(`⚠ ${msg}`); }
      }
    };

    if (demVisible) {
      updateDem();
      map.on("moveend", updateDem);
    } else {
      clearDem();
    }
    return () => { map.off("moveend", updateDem); ctrl?.abort(); };
  }, [mapLayers.find((l) => l.id === "dem")?.visible]);

  // Revoke the DEM overlay's object URL on unmount
  useEffect(() => () => {
    if (demBlobUrlRef.current) { URL.revokeObjectURL(demBlobUrlRef.current); demBlobUrlRef.current = null; }
  }, []);

  // ── Civil cellular sites (data.gov.il via backend) ───────────────────────────
  const cellSitesVisible = mapLayers.find((l) => l.id === "cellsites")?.visible ?? false;
  const cellSitesLoadedVersion = useRef(-1);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (cellSitesRef.current) { map.removeLayer(cellSitesRef.current); cellSitesRef.current = null; }
    if (!cellSitesVisible) return;

    let cancelled = false;
    (async () => {
      try {
        if (!cellSitesDataRef.current || cellSitesLoadedVersion.current !== cellSitesVersion) {
          cellSitesDataRef.current = await cellSitesApi.list();
          cellSitesLoadedVersion.current = cellSitesVersion;
        }
        if (cancelled) return;
        const renderer = cellCanvasRef.current ?? (cellCanvasRef.current = L.canvas({ padding: 0.5 }));
        const ops = new Set(cellSiteFilter.operators);
        const group = L.layerGroup();
        cellMarkersRef.current.clear();
        let n = 0;
        for (const s of cellSitesDataRef.current) {
          if (!ops.has(s.operator)) continue;
          if (s.status === "construction" && !cellSiteFilter.showConstruction) continue;
          const m = L.circleMarker([s.lat, s.lon], { renderer, ...cellSiteMarkerStyle(s) });
          m.bindPopup("טוען…", { maxWidth: 320 });
          m.on("popupopen", async (e: L.PopupEvent) => {
            try {
              const d = await cellSitesApi.get(s.id);
              cellDetailsRef.current.set(s.id, d);
              const role = useStore.getState().user?.role;
              e.popup.setContent(cellSitePopupHtml(d, canEditCellSiteHeight(role), canPromoteCellSite(role)));
            } catch {
              e.popup.setContent("⚠ שגיאה בטעינת פרטי האתר");
            }
          });
          // Link tab "בחר במפה": the next click on a marker picks it as the transmitter
          m.on("click", () => {
            const st = useStore.getState();
            if (!st.linkPickTransmitter) return;
            setTimeout(() => m.closePopup(), 0);
            // No band sent unless the user picks one in the panel → backend default/estimate applies
            st.setLinkTransmitter({ kind: "cell_site", id: s.id, frequency_mhz: null });
            st.setLinkPickTransmitter(false);
            st.setLinkSubMode("link");
            if (st.activeTab !== "link") st.setActiveTab("link");
            notify("🎯 אתר סלולרי נבחר כמשדר");
          });
          m.addTo(group);
          cellMarkersRef.current.set(s.id, m);
          n++;
        }
        if (cancelled) return;
        group.addTo(map);
        cellSitesRef.current = group;
        notify(`📡 מוצגים ${n.toLocaleString("he-IL")} אתרי סלולר (מאגר ממשלתי)`);
      } catch {
        notify("⚠ שגיאה בטעינת שכבת אתרי הסלולר מהשרת");
      }
    })();
    return () => { cancelled = true; };
  }, [cellSitesVisible, cellSiteFilter, cellSitesVersion]);

  // ── Cell-site popup: inline height / RF editors + actions (one delegated listener) ──
  // The popup is an HTML string, so its buttons are wired here: on every popupopen
  // we attach (once per popup element) a click/keydown handler that reacts to
  // [data-cs-action] buttons inside a [data-cellsite-id] popup.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const role = () => useStore.getState().user?.role;
    const popupHtml = (d: CellSiteDetail) =>
      cellSitePopupHtml(d, canEditCellSiteHeight(role()), canPromoteCellSite(role()));

    function applyUpdate(popup: L.Popup, d: CellSiteDetail) {
      cellDetailsRef.current.set(d.id, d);
      const row = cellSitesDataRef.current?.find((r) => r.id === d.id);
      if (row) {
        row.ant_height_m = d.ant_height_m;
        row.height_source = d.height_source;
        row.height_note = d.height_note;
        row.height_updated_at = d.height_updated_at;
        row.tx_power_dbm = d.tx_power_dbm;
        row.frequency_mhz = d.frequency_mhz;
        row.rf_source = d.rf_source;
        row.tx_power_source = d.tx_power_source;
        row.frequency_source = d.frequency_source;
        row.rf_note = d.rf_note;
        row.rf_updated_at = d.rf_updated_at;
        if (d.bands) row.bands = d.bands;
        cellMarkersRef.current.get(d.id)?.setStyle(cellSiteMarkerStyle(row));
      }
      popup.setContent(popupHtml(d));
    }

    /**
     * Shows the popup with `formHtml` in one of its slots. The form must be part of the
     * content passed to setContent(): popup.update() re-renders the stored content string,
     * so a form injected into the DOM afterwards would be wiped immediately.
     */
    function showForm(popup: L.Popup, d: CellSiteDetail, slot: "height" | "rf" | "actions",
                      formHtml: string, focusField: string) {
      const tpl = document.createElement("div");
      tpl.innerHTML = popupHtml(d);
      const el = tpl.querySelector<HTMLElement>(`[data-cs-${slot}]`);
      if (el) el.innerHTML = formHtml;
      popup.setContent(tpl.innerHTML);
      popup.getElement()?.querySelector<HTMLElement>(`[data-cs-field="${focusField}"]`)?.focus();
    }

    /** Cell-site ids with a promote request in flight (prevents double submit). */
    const promoting = new Set<number>();

    /** "נתח כיסוי מאתר זה": create (or reuse) an analysis Site and open it in the wizard. */
    async function promote(popup: L.Popup, root: HTMLElement, d: CellSiteDetail, frequency?: number) {
      if (promoting.has(d.id)) return;
      promoting.add(d.id);
      const btns = root.querySelectorAll<HTMLButtonElement>('[data-cs-action="promote"],[data-cs-action="promote-go"]');
      btns.forEach((b) => { b.disabled = true; b.style.opacity = "0.6"; b.style.cursor = "wait"; });
      let r;
      try {
        r = await cellSitesApi.promote(d.id, frequency != null ? { frequency_mhz: frequency } : {});
      } catch (err) {
        btns.forEach((b) => { b.disabled = false; b.style.opacity = ""; b.style.cursor = ""; });
        throw err;
      } finally {
        promoting.delete(d.id);
      }
      const st = useStore.getState();
      try {
        st.setSites(await sitesApi.list());
      } catch {
        if (!st.sites.some((x) => x.id === r.site.id)) st.addSite(r.site);
      }
      const sel = useStore.getState().selectedSiteIds;
      st.setSelectedSiteIds(sel.includes(r.site.id) ? sel : [...sel, r.site.id]);
      st.setActiveSite(r.site.id);
      st.setActiveTab("analysis");
      popup.close();
      notify(!r.created
        ? `ℹ האתר "${r.site.name}" כבר קיים — נבחר לניתוח`
        : r.site.params_source === "estimated"
          ? `✅ נוצר אתר "${r.site.name}" עם פרמטרים משוערים — ניתן לערוך בלשונית אתרים`
          : `✅ נוצר אתר "${r.site.name}" — נבחר לניתוח`);
    }

    async function handle(popup: L.Popup, root: HTMLElement, action: string) {
      const id = Number(root.dataset.cellsiteId);
      const d = cellDetailsRef.current.get(id);
      if (!d) return;
      const hSlot = root.querySelector<HTMLElement>("[data-cs-height]");
      const rfSlot = root.querySelector<HTMLElement>("[data-cs-rf]");
      const actSlot = root.querySelector<HTMLElement>("[data-cs-actions]");
      const canEdit = canEditCellSiteHeight(role());
      // Error line of whichever form is open
      const setErr = (msg: string) => {
        const e = root.querySelector<HTMLElement>('[data-cs-field="err"]');
        if (e) e.textContent = msg; else notify(`⚠ ${msg}`);
      };
      const val = (f: string) => root.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-cs-field="${f}"]`)?.value.trim() ?? "";

      if (action === "edit" && canEdit && hSlot) {
        // Fresh popup (closes any other open form) with the height form in its slot
        showForm(popup, d, "height", cellSiteHeightFormHtml(d), "h");
        return;
      }
      if (action === "edit-rf" && canEdit && rfSlot) {
        showForm(popup, d, "rf", cellSiteRfFormHtml(d), "tx");
        return;
      }
      if (action === "cancel") {
        popup.setContent(popupHtml(d));
        return;
      }
      if (action === "link" && canEdit) {
        const st = useStore.getState();
        st.setLinkTransmitter({ kind: "cell_site", id: d.id, frequency_mhz: null });
        st.setLinkPickTransmitter(false);
        st.setLinkSubMode("link");
        st.setActiveTab("link");
        popup.close();
        notify("🎯 האתר נבחר כמשדר — בחר מיקום מקלט בלשונית קישור נקודתי");
        return;
      }
      if ((action === "promote" || action === "promote-go") && (!canPromoteCellSite(role()) || promoting.has(d.id))) return;
      if (action === "promote" && actSlot) {
        if (cellSiteBands(d).length > 1) {
          showForm(popup, d, "actions", cellSitePromoteFormHtml(d), "pfreq");
          return;
        }
        // single band → go straight away
        action = "promote-go";
      }
      try {
        if (action === "promote-go") {
          const f = Number(val("pfreq"));
          await promote(popup, root, d, Number.isFinite(f) && f > 0 ? f : undefined);
        } else if (action === "save" && hSlot) {
          const h = Number(val("h"));
          const note = val("note");
          if (!Number.isFinite(h) || h < 0.5 || h > 300) { setErr("גובה חייב להיות בין 0.5 ל-300 מטר"); return; }
          applyUpdate(popup, await cellSitesApi.setHeight(id, h, note));
          notify(`✓ גובה האנטנה נשמר: ${h} מ'`);
        } else if (action === "reset") {
          const r = await cellSitesApi.resetHeight(id);
          applyUpdate(popup, r);
          notify(`↺ הגובה הוחזר להערכה לפי סוג האתר (${r.ant_height_m} מ')`);
        } else if (action === "save-rf") {
          const txRaw = val("tx");
          const custom = val("freq-custom");
          const freqRaw = custom || val("freq");
          const tx = txRaw === "" ? undefined : Number(txRaw);
          const freq = freqRaw === "" ? undefined : Number(freqRaw);
          if (tx === undefined && freq === undefined) { setErr("יש להזין הספק או תדר"); return; }
          if (tx !== undefined && (!Number.isFinite(tx) || tx < 0 || tx > 70)) { setErr("הספק חייב להיות בין 0 ל-70 dBm"); return; }
          if (freq !== undefined && (!Number.isInteger(freq) || freq < 100 || freq > 6000)) { setErr("תדר חייב להיות מספר שלם בין 100 ל-6000 MHz"); return; }
          const r = await cellSitesApi.setRf(id, { tx_power_dbm: tx, frequency_mhz: freq, note: val("rfnote") });
          applyUpdate(popup, r);
          notify(`✓ פרמטרי RF נשמרו: ${r.frequency_mhz ?? "—"} MHz · ${r.tx_power_dbm ?? "—"} dBm`);
        } else if (action === "reset-rf") {
          const r = await cellSitesApi.resetRf(id);
          applyUpdate(popup, r);
          notify(`↺ פרמטרי RF הוחזרו להערכה (${r.frequency_mhz ?? "—"} MHz · ${r.tx_power_dbm ?? "—"} dBm)`);
        }
      } catch (err) {
        setErr(apiErrorText(err, action.startsWith("promote") ? "יצירת האתר נכשלה" : "השמירה נכשלה"));
      }
    }

    const onOpen = (e: L.PopupEvent) => {
      const el = e.popup.getElement();
      if (!el || el.dataset.csWired) return;
      el.dataset.csWired = "1";
      el.addEventListener("click", (ev) => {
        const btn = (ev.target as HTMLElement).closest<HTMLElement>("[data-cs-action]");
        const root = btn?.closest<HTMLElement>("[data-cellsite-id]");
        if (btn && root) {
          ev.preventDefault();
          // The action re-renders the popup, detaching the clicked button; Leaflet would then
          // no longer see the click as inside the popup and close it (closePopupOnClick).
          ev.stopPropagation();
          void handle(e.popup, root, btn.dataset.csAction!);
        }
      });
      el.addEventListener("keydown", (ev) => {
        const t = ev.target as HTMLElement;
        const root = t.closest<HTMLElement>("[data-cellsite-id]");
        if (!root || !t.matches("[data-cs-field]")) return;
        ev.stopPropagation();   // keep Leaflet's keyboard handler (Esc closes popup, +/- zoom) out of the form
        const submit = t.closest("[data-cs-rf]") ? "save-rf" : t.closest("[data-cs-actions]") ? "promote-go" : "save";
        if (ev.key === "Enter")  { ev.preventDefault(); void handle(e.popup, root, submit); }
        if (ev.key === "Escape") { ev.preventDefault(); void handle(e.popup, root, "cancel"); }
      });
    };
    map.on("popupopen", onOpen);
    return () => { map.off("popupopen", onOpen); };
  }, []);

  // ── Coverage PNG overlays (from mapLayers) ───────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const coverageLayers = mapLayers.filter((l) => l.type === "coverage");

    // Remove overlays for deleted layers
    covOverlaysRef.current.forEach((overlay, id) => {
      if (!coverageLayers.find((l) => l.id === id)) {
        map.removeLayer(overlay);
        covOverlaysRef.current.delete(id);
        const url = covBlobsRef.current.get(id);
        if (url) { URL.revokeObjectURL(url); covBlobsRef.current.delete(id); }
      }
    });

    coverageLayers.forEach(async (layer) => {
      const existing = covOverlaysRef.current.get(layer.id);

      if (existing) {
        // Toggle visibility of already-fetched overlay
        if (layer.visible && !map.hasLayer(existing)) existing.addTo(map);
        if (!layer.visible && map.hasLayer(existing)) map.removeLayer(existing);
        return;
      }

      if (!layer.visible || !layer.resultId || !layer.bbox) return;
      if (covPendingRef.current.has(layer.id)) return;   // already fetching

      covPendingRef.current.add(layer.id);
      try {
        const blobUrl = await coverageApi.resultPng(layer.resultId, layer.bbox);
        // The layer may have been removed/hidden (e.g. "נקה תוצאות") while the
        // PNG was loading — re-check the CURRENT store state before drawing.
        const current = useStore.getState().mapLayers.find((l) => l.id === layer.id);
        if (!current || mapRef.current !== map || covOverlaysRef.current.has(layer.id)) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        covBlobsRef.current.set(layer.id, blobUrl);
        const overlay = L.imageOverlay(
          blobUrl,
          [[layer.bbox.sw_lat, layer.bbox.sw_lon], [layer.bbox.ne_lat, layer.bbox.ne_lon]],
          { opacity: 1, interactive: false, zIndex: 300 },
        );
        covOverlaysRef.current.set(layer.id, overlay);
        if (current.visible) overlay.addTo(map);
      } catch { /* network issue — silent */ }
      finally { covPendingRef.current.delete(layer.id); }
    });
  }, [mapLayers]);

  // ── Cursor and Map Drag lock ──────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const container = map.getContainer();
    container.style.cursor = drawMode ? "crosshair" : "";
    
    // Disable panning when drawing
    if (drawMode === "rect" || drawMode === "square") {
      map.dragging.disable();
    } else {
      map.dragging.enable();
    }
  }, [drawMode]);

  // ── Site markers: shown only in context ─────────────────────────────────────
  // A user site's marker is on the map iff the site is
  //  · selected in the analysis wizard,
  //  · the owner of a VISIBLE coverage layer,
  //  · the site chosen in the cross-section panel,
  //  · the link-tab transmitter (kind "site"),
  //  · the site being viewed (Sites tab) or edited (edit-site tab).
  // Nothing else draws site markers, so a fresh load / cleared map shows none.
  const shownSiteKey = useMemo(() => {
    const ids = new Set<number>(selectedSiteIds);
    mapLayers.forEach((l) => {
      if (l.type === "coverage" && l.visible && l.siteId != null) ids.add(l.siteId);
    });
    if (crossSectionSiteId != null) ids.add(crossSectionSiteId);
    if (linkTransmitter?.kind === "site" && linkTransmitter.id != null) ids.add(linkTransmitter.id);
    if (activeTab === "sites" && activeSiteId != null) ids.add(activeSiteId);
    if (activeTab === "new" && editingSite) ids.add(editingSite.id);
    return [...ids].sort((a, b) => a - b).join(",");
  }, [selectedSiteIds, mapLayers, crossSectionSiteId, linkTransmitter, activeTab, activeSiteId, editingSite]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const shown = new Set(shownSiteKey ? shownSiteKey.split(",").map(Number) : []);
    const byId = new Map(sites.map((s) => [s.id, s]));

    // Remove markers that are no longer in context, of deleted sites, or of changed sites (rebuilt below)
    markersRef.current.forEach(({ marker, site }, id) => {
      const cur = byId.get(id);
      if (!shown.has(id) || !cur || cur !== site) {
        map.removeLayer(marker);
        markersRef.current.delete(id);
      }
    });

    sites.forEach((site) => {
      if (!shown.has(site.id) || markersRef.current.has(site.id)) return;
      const color = FREQ_COLORS[site.frequency] ?? "#00d4ff";
      const icon = L.divIcon({
        className: "",
        html: `<div data-testid="site-marker-${site.id}" style="display:flex;flex-direction:column;align-items:center;">
          <div style="
            width:32px;height:32px;
            background:${color}22;
            border:2px solid ${color};
            box-shadow:0 0 10px ${color}99, inset 0 0 6px ${color}44;
            display:flex;align-items:center;justify-content:center;
            clip-path:polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%);
          ">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="12" y1="2" x2="12" y2="22"/>
              <path d="M5 8 Q12 2 19 8"/>
              <path d="M7 12 Q12 7 17 12"/>
              <line x1="9" y1="22" x2="15" y2="22"/>
            </svg>
          </div>
          <div style="width:2px;height:6px;background:${color};opacity:0.7;"></div>
        </div>`,
        iconSize: [32, 42], iconAnchor: [16, 42],
      });

      const marker = L.marker([site.lat, site.lon], { icon, draggable: false })
        .addTo(map)
        .bindPopup(`
          <div style="font-family:sans-serif;min-width:200px;direction:rtl">
            <b style="font-size:13px">${escHtml(site.name)}</b>
            <div style="margin:4px 0;border-top:1px solid #eee;"></div>
            <span style="color:#555;font-size:11px">📡 ${site.frequency} MHz</span><br>
            <span style="color:#555;font-size:11px">🏔 גובה מ"פ: <b>${site.elevation_m ?? 0} מ'</b> ASL</span><br>
            <span style="color:#555;font-size:11px">🏗 גובה אנטנה: <b>${site.ant_height} מ'</b> AGL</span><br>
            <span style="color:#555;font-size:11px">⚡ הספק: ${site.tx_power} dBm &nbsp;|&nbsp; סף: ${site.rx_threshold} dBm</span><br>
            <span style="color:#555;font-size:11px">🔭 רדיוס: ${site.max_radius} ק"מ</span>
            ${site.notes ? `<br><i style="color:#aaa;font-size:11px">${escHtml(site.notes)}</i>` : ""}
          </div>
        `);

      marker.on("contextmenu", () => {
        if (!canWrite(useStore.getState().user?.role)) return;   // VIEWER: read-only
        if (marker.dragging && !marker.dragging.enabled()) {
          marker.closePopup();
          marker.dragging.enable();
          notify(`מצב גרירה לאתר ${site.name} הופעל. ניתן לגרור!`);
        }
      });

      marker.on("dragend", async (e) => {
        const newPos = e.target.getLatLng();
        if (marker.dragging) marker.dragging.disable();
        try {
          await sitesApi.update(site.id, { lat: newPos.lat, lon: newPos.lng });
          const store = useStore.getState();
          store.setSites(store.sites.map(s => s.id === site.id ? { ...s, lat: newPos.lat, lon: newPos.lng } : s));
          notify(`✅ מיקום האתר התעדכן בהצלחה!`);
        } catch (err) {
          notify(`❌ שגיאה בשמירת המיקום החדש`);
          e.target.setLatLng([site.lat, site.lon]);
        }
      });

      markersRef.current.set(site.id, { marker, site });
    });
  }, [sites, shownSiteKey]);

  // Clicking a site in the Sites list (or creating one) brings it into view if it is off-screen
  useEffect(() => {
    const map = mapRef.current;
    if (!map || activeSiteId == null) return;
    const site = useStore.getState().sites.find((s) => s.id === activeSiteId);
    if (site && !map.getBounds().contains([site.lat, site.lon])) map.panTo([site.lat, site.lon]);
  }, [activeSiteId]);

  // One-shot "fit the map to this box" requests (e.g. History → "טען למפה")
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapFitRequest) return;
    const r = mapFitRequest;
    map.fitBounds([[r.sw_lat, r.sw_lon], [r.ne_lat, r.ne_lon]], { padding: [40, 40], maxZoom: 15, animate: false });
  }, [mapFitRequest]);

  // Coverage rendering is now driven by mapLayers (see effect above)

  // ── Route waypoints rendering ────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (routeLayerRef.current) { map.removeLayer(routeLayerRef.current); routeLayerRef.current = null; }
    if (!routeWaypoints.length) return;

    const group = L.layerGroup().addTo(map);

    // Polyline
    if (routeWaypoints.length >= 2) {
      L.polyline(routeWaypoints.map(w => [w.lat, w.lon] as [number, number]), {
        color: "#f59e0b", weight: 3, dashArray: "6,4", opacity: 0.9,
      }).addTo(group);
    }

    // Numbered markers
    routeWaypoints.forEach((wp, i) => {
      const icon = L.divIcon({
        className: "",
        html: `<div style="
          width:22px;height:22px;border-radius:50%;
          background:#f59e0b;border:2px solid #fff;
          display:flex;align-items:center;justify-content:center;
          font-weight:700;font-size:11px;color:#000;
          box-shadow:0 0 6px rgba(0,0,0,0.4);">${i + 1}</div>`,
        iconSize: [22, 22], iconAnchor: [11, 11],
      });
      L.marker([wp.lat, wp.lon], { icon })
        .bindTooltip(`נקודה ${i + 1}: ${wp.lat.toFixed(5)}°N, ${wp.lon.toFixed(5)}°E`, { sticky: true })
        .addTo(group);
    });

    routeLayerRef.current = group;
  }, [routeWaypoints]);

  // ── Cross-section result: colored route segments ───────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (crossSectionRouteRef.current) {
      map.removeLayer(crossSectionRouteRef.current);
      crossSectionRouteRef.current = null;
    }
    if (!crossSectionResult?.points?.length) return;

    const SEGMENT_COLORS: Record<string, string> = {
      excellent: "#00ff88",
      good:      "#7dff6b",
      medium:    "#ffe600",
      weak:      "#ff8c00",
      marginal:  "#ff3b5c",
      none:      "#aaaaaa",
    };

    const group = L.layerGroup().addTo(map);
    const pts = crossSectionResult.points;

    // Draw each segment colored by its level
    for (let i = 0; i < pts.length - 1; i++) {
      const p1 = pts[i], p2 = pts[i + 1];
      const color = SEGMENT_COLORS[p1.level] ?? "#aaaaaa";
      L.polyline([[p1.lat, p1.lon], [p2.lat, p2.lon]], {
        color, weight: 5, opacity: 0.9,
      })
        .bindTooltip(
          `<div style="font-family:sans-serif;font-size:11px;direction:rtl">
            <b>${p1.rssi} dBm</b> — ${p1.dist_along.toFixed(2)} ק"מ
          </div>`,
          { sticky: true }
        )
        .addTo(group);
    }

    // Waypoint markers on top
    crossSectionResult.waypoints.forEach((wp, i) => {
      const icon = L.divIcon({
        className: "",
        html: `<div style="width:18px;height:18px;border-radius:50%;background:#f59e0b;border:2px solid #fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:10px;color:#000;">${i + 1}</div>`,
        iconSize: [18, 18], iconAnchor: [9, 9],
      });
      L.marker([wp.lat, wp.lon], { icon }).addTo(group);
    });

    crossSectionRouteRef.current = group;
  }, [crossSectionResult]);

  // ── LOS lines: selected sites → bbox center ─────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    losLayersRef.current.forEach((l) => map.removeLayer(l));
    losLayersRef.current = [];

    if (!bbox || !selectedSiteIds.length) return;

    const centerLat = (bbox.sw_lat + bbox.ne_lat) / 2;
    const centerLng = (bbox.sw_lon + bbox.ne_lon) / 2;

    selectedSiteIds.forEach((siteId) => {
      const site = sites.find((s) => s.id === siteId);
      if (!site) return;

      const dist  = haversineKm(site.lat, site.lon, centerLat, centerLng);
      const hGain = 20 * Math.log10(Math.max(site.ant_height, 1.5) / 1.5);
      const rssi  = site.tx_power - fsplDb(dist, site.frequency) + hGain;
      const margin = rssi - site.rx_threshold;

      let color: string, dashArray: string | undefined, weight: number;
      if (margin >= 10) {
        color = "#00ff88"; dashArray = undefined;    weight = 2.5; // strong LOS
      } else if (margin >= 0) {
        color = "#ffe600"; dashArray = "8,5";        weight = 2;   // marginal
      } else {
        color = "#ff3b5c"; dashArray = "4,6";        weight = 1.5; // no signal
      }

      const line = L.polyline([[site.lat, site.lon], [centerLat, centerLng]], {
        color, weight, dashArray, opacity: 0.85,
      });

      const losLabel = margin >= 10 ? "✅ קשר עין סביר" :
                       margin >= 0  ? "⚠ קשר עין שולי"  : "❌ אין קשר עין";

      line.bindTooltip(`
        <div style="font-family:sans-serif;font-size:12px;direction:rtl;min-width:160px">
          <b>${site.name}</b><br>
          מרחק: <b>${dist.toFixed(2)} ק"מ</b><br>
          RSSI תיאורטי: <b>${rssi.toFixed(1)} dBm</b><br>
          ${losLabel}
        </div>
      `, { sticky: true });

      line.addTo(map);
      line.getElement()?.setAttribute("data-testid", "los-line");
      losLayersRef.current.push(line);
    });
  }, [bbox, selectedSiteIds, sites]);

  // ── Link: draggable receiver marker ─────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!linkReceiver) {
      if (linkRxMarkerRef.current) { map.removeLayer(linkRxMarkerRef.current); linkRxMarkerRef.current = null; }
      return;
    }
    const pos: L.LatLngExpression = [linkReceiver.lat, linkReceiver.lon];
    if (linkRxMarkerRef.current) {
      linkRxMarkerRef.current.setLatLng(pos);
      return;
    }
    const icon = L.divIcon({
      className: "",
      html: `<div title="מקלט — גרור לשינוי מיקום" style="
        width:26px;height:26px;border-radius:50%;
        background:rgba(0,212,255,0.25);border:2px solid #00d4ff;
        box-shadow:0 0 10px rgba(0,212,255,0.7);
        display:flex;align-items:center;justify-content:center;font-size:13px;cursor:move;">📱</div>`,
      iconSize: [26, 26], iconAnchor: [13, 13],
    });
    const m = L.marker(pos, { icon, draggable: true, zIndexOffset: 1200, keyboard: false })
      .bindTooltip("מקלט (מכשיר) — גרור לכוונון", { direction: "top", offset: [0, -12] })
      .addTo(map);
    m.on("dragend", () => {
      const p = m.getLatLng();
      useStore.getState().setLinkReceiver({ lat: p.lat, lon: p.lng });
    });
    linkRxMarkerRef.current = m;
  }, [linkReceiver]);

  // ── Link result: line coloured by level + tx / obstacle markers ────────────
  const linkFitKeyRef = useRef("");
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (linkLayerRef.current) { map.removeLayer(linkLayerRef.current); linkLayerRef.current = null; }
    const r = linkResult;
    if (!r || linkSubMode !== "link") return;
    const tx = r.transmitter, rx = r.receiver;
    if (![tx?.lat, tx?.lon, rx?.lat, rx?.lon].every((v) => typeof v === "number" && Number.isFinite(v))) return;

    const color = levelColor(r.level);
    const group = L.layerGroup().addTo(map);
    // dark casing under the coloured line for contrast on any base map
    L.polyline([[tx.lat, tx.lon], [rx.lat, rx.lon]], { color: "#000", weight: 7, opacity: 0.45, interactive: false }).addTo(group);
    L.polyline([[tx.lat, tx.lon], [rx.lat, rx.lon]], {
      color, weight: 4, opacity: 0.95, dashArray: r.is_los ? undefined : "8,6",
    }).bindTooltip(`<div style="font-family:sans-serif;font-size:12px;direction:rtl">
        <b>${escHtml(tx.name ?? "משדר")}</b> → ${escHtml(rx.name || rx.profile_name || "מקלט")}<br>
        RSSI: <b>${fmtDbm(r.rssi_dbm, 1)}</b> · מרווח: <b>${fmtDb(r.margin_db)}</b><br>
        ${levelHe(r.level)} · ${r.is_los ? "קו ראייה (LOS)" : "ללא קו ראייה (NLOS)"} · ${fmtKm(r.distance_km)}
      </div>`, { sticky: true }).addTo(group);
    L.circleMarker([tx.lat, tx.lon], { radius: 8, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1 })
      .bindTooltip(`משדר: ${escHtml(tx.name ?? "")}`, { direction: "top" }).addTo(group);
    const ob = r.worst_obstacle;
    if (ob && Number.isFinite(ob.lat) && Number.isFinite(ob.lon)) {
      L.marker([ob.lat, ob.lon], {
        icon: L.divIcon({
          className: "",
          html: `<div style="color:#ff3b5c;font-size:16px;font-weight:900;text-shadow:0 0 3px #000">▲</div>`,
          iconSize: [16, 16], iconAnchor: [8, 12],
        }),
      }).bindTooltip(`מכשול עיקרי: ${Math.round(ob.terrain_m)} מ' (${ob.dist_km.toFixed(2)} ק"מ מהמשדר)`, { direction: "top" })
        .addTo(group);
    }
    linkLayerRef.current = group;
    // Re-fit only for new geometry (not on sub-mode toggles / history deletes)
    const key = `${tx.lat},${tx.lon},${rx.lat},${rx.lon}`;
    if (linkFitKeyRef.current !== key) {
      linkFitKeyRef.current = key;
      map.fitBounds(L.latLngBounds([[tx.lat, tx.lon], [rx.lat, rx.lon]]).pad(0.25), { maxZoom: 15 });
    }
  }, [linkResult, linkSubMode]);

  // ── Best servers: lines receiver → each ranked cell site ──────────────────
  const bestFitKeyRef = useRef<BestServersResult | null>(null);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (bestLayerRef.current) { map.removeLayer(bestLayerRef.current); bestLayerRef.current = null; }
    const res = bestServersResult;
    const rx = res?.receiver;
    if (!res?.results?.length || linkSubMode !== "best" || !rx || !Number.isFinite(rx.lat) || !Number.isFinite(rx.lon)) return;

    const group = L.layerGroup().addTo(map);
    const n = res.results.length;
    const bounds = L.latLngBounds([[rx.lat, rx.lon], [rx.lat, rx.lon]]);
    // Lowest rank last so the best server is drawn on top
    [...res.results].sort((a, b) => b.rank - a.rank).forEach((e) => {
      const c = e.cell_site;
      if (!Number.isFinite(c?.lat) || !Number.isFinite(c?.lon)) return;
      const hl = highlightedServerId === c.id;
      const dim = highlightedServerId != null && !hl;
      const color = levelColor(e.link?.level);
      const weight = hl ? 7 : Math.max(1.5, 6 - (e.rank - 1) * (4 / Math.max(1, n - 1)));
      const opacity = dim ? 0.25 : hl ? 1 : Math.max(0.45, 1 - (e.rank - 1) * 0.12);
      L.polyline([[rx.lat, rx.lon], [c.lat, c.lon]], {
        color, weight, opacity, dashArray: e.link?.is_los ? undefined : "7,6",
      }).bindTooltip(`<div style="font-family:sans-serif;font-size:12px;direction:rtl">
          <b>#${e.rank} ${escHtml(c.operator_he ?? c.operator)}</b> · ${escHtml(c.site_type_he ?? "")}<br>
          RSSI: <b>${fmtDbm(e.link?.rssi_dbm, 1)}</b> · מרווח: <b>${fmtDb(e.link?.margin_db)}</b><br>
          ${fmtKm(e.link?.distance_km)} · ${e.link?.is_los ? "LOS" : "NLOS"}
        </div>`, { sticky: true })
        .on("click", () => useStore.getState().setHighlightedServerId(c.id))
        .addTo(group);
      L.marker([c.lat, c.lon], {
        icon: L.divIcon({
          className: "",
          html: `<div style="width:20px;height:20px;border-radius:50%;background:${color};
            border:2px solid ${hl ? "#fff" : "#0a0e14"};color:#000;font:700 11px sans-serif;
            display:flex;align-items:center;justify-content:center;opacity:${dim ? 0.5 : 1};
            box-shadow:0 0 6px rgba(0,0,0,0.6)">${e.rank}</div>`,
          iconSize: [20, 20], iconAnchor: [10, 10],
        }),
        zIndexOffset: hl ? 1100 : 1000 - e.rank,
      }).on("click", () => useStore.getState().setHighlightedServerId(c.id)).addTo(group);
      bounds.extend([c.lat, c.lon]);
    });
    bestLayerRef.current = group;
    // Fit only when a new result arrives (not on every highlight change)
    if (bestFitKeyRef.current !== res) {
      bestFitKeyRef.current = res;
      map.fitBounds(bounds.pad(0.2), { maxZoom: 15 });
    }
  }, [bestServersResult, highlightedServerId, linkSubMode]);

  // Picking a transmitter on the map needs the cell-sites layer
  useEffect(() => {
    if (!linkPickTransmitter) return;
    const st = useStore.getState();
    if (!st.mapLayers.find((l) => l.id === "cellsites")?.visible) st.toggleMapLayer("cellsites");
  }, [linkPickTransmitter]);

  // ── Sync Active Bounding Box ─────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (polyLayerRef.current) {
      map.removeLayer(polyLayerRef.current);
      polyLayerRef.current = null;
    }
    if (bboxDragRef.current) {
      map.removeLayer(bboxDragRef.current);
      bboxDragRef.current = null;
    }

    if (bbox) {
      const sw: L.LatLngLiteral = { lat: bbox.sw_lat, lng: bbox.sw_lon };
      const ne: L.LatLngLiteral = { lat: bbox.ne_lat, lng: bbox.ne_lon };
      polyLayerRef.current = L.rectangle(L.latLngBounds(sw, ne), {
        color: isCalculating ? "#facc15" : "#3b82f6",
        weight: 2,
        fillColor: isCalculating ? "#facc15" : "#3b82f6",
        fillOpacity: isCalculating ? 0.2 : 0.12,
        dashArray: isCalculating ? "8,6" : "",
        className: isCalculating ? styles.pulsingPoly : ""
      }).addTo(map);
      polyLayerRef.current.getElement()?.setAttribute("data-testid", "analysis-bbox");

      // Drag handle – visible only when not calculating
      if (!isCalculating) {
        const centerLat = (bbox.sw_lat + bbox.ne_lat) / 2;
        const centerLng = (bbox.sw_lon + bbox.ne_lon) / 2;
        const latSpan   = bbox.ne_lat - bbox.sw_lat;
        const lngSpan   = bbox.ne_lon - bbox.sw_lon;

        const dragIcon = L.divIcon({
          className: "",
          html: `<div data-testid="analysis-bbox-handle" title="גרור להזזת תא השטח" style="
            width:22px;height:22px;
            background:rgba(59,130,246,0.85);
            border:2px solid #fff;border-radius:4px;
            cursor:move;display:flex;align-items:center;justify-content:center;
            font-size:13px;color:#fff;line-height:1;
          ">✥</div>`,
          iconSize: [22, 22],
          iconAnchor: [11, 11],
        });

        const handle = L.marker([centerLat, centerLng], {
          icon: dragIcon,
          draggable: true,
          zIndexOffset: 1000,
        }).addTo(map);

        handle.on("drag", (e) => {
          const pos = (e.target as L.Marker).getLatLng();
          polyLayerRef.current?.setBounds(L.latLngBounds(
            [pos.lat - latSpan / 2, pos.lng - lngSpan / 2],
            [pos.lat + latSpan / 2, pos.lng + lngSpan / 2],
          ));
        });

        handle.on("dragend", (e) => {
          const pos = (e.target as L.Marker).getLatLng();
          useStore.getState().setBbox({
            sw_lat: pos.lat - latSpan / 2,
            sw_lon: pos.lng - lngSpan / 2,
            ne_lat: pos.lat + latSpan / 2,
            ne_lon: pos.lng + lngSpan / 2,
          });
          notify("✅ תא השטח הועבר בהצלחה");
        });

        bboxDragRef.current = handle;
      }
    }
  }, [bbox, isCalculating]);

  return (
    <div className={styles.wrapper}>
      <div ref={mapElRef} className={styles.map} />

      {/* Draw indicator */}
      {drawMode && (
        <div className={styles.drawBanner}>
          {drawMode === "point" ? "בחירת מיקום - לחץ על המפה"
            : drawMode === "linkpoint" ? "בחירת מיקום המקלט - לחץ על המפה"
            : drawMode === "waypoint" ? "הוספת נקודות מסלול - לחץ על המפה"
            : "שרטוט - לחץ, החזק וגרור לקביעת תא השטח"}
          <button className={styles.cancelDraw} onClick={() => setDrawMode(null)}>ביטול</button>
        </div>
      )}
      {!drawMode && linkPickTransmitter && (
        <div className={styles.drawBanner}>
          בחירת משדר - לחץ על אתר סלולרי במפה
          <button className={styles.cancelDraw} onClick={() => setLinkPickTransmitter(false)}>ביטול</button>
        </div>
      )}

      {/* Calculating overlay */}
      {isCalculating && (
        <div className={styles.calcOverlay}>
          <div className={styles.spinner} />
          <div className={styles.calcText}>מחשב כיסוי...</div>
        </div>
      )}

      {/* DEM / base-map controls moved to 🗂 שכבות tab */}

      {/* Coords */}
      <div id="coord-display" className={styles.coords}>
        31.76830° N  |  35.21370° E
      </div>
    </div>
  );
}
