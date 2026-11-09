import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import { useStore } from "../store/useStore";
import { notify } from "./Notification";
import api, { apiErrorText, apiErrorTextAsync, sitesApi, coverageApi, cellSitesApi, osmTowersApi } from "../services/api";
import type { CellSiteRow } from "../services/api";
import { CELL_OPERATORS, cellSitePopupHtml, esc } from "../lib/cellSites";
import styles from "./MapView.module.css";

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

function fsplDb(dist_km: number, freq_mhz: number): number {
  if (dist_km <= 0) return 0;
  return 20*Math.log10(dist_km*1000) + 20*Math.log10(freq_mhz*1e6) - 147.55;
}

export default function MapView() {
  const mapRef          = useRef<L.Map | null>(null);
  const mapElRef        = useRef<HTMLDivElement>(null);
  const markersRef      = useRef<Map<number, L.Marker>>(new Map());
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
  // Special overlays
  const demOverlayRef   = useRef<L.ImageOverlay | null>(null);
  const demBlobUrlRef   = useRef<string | null>(null);
  const cellularRef     = useRef<L.LayerGroup | null>(null);
  // Civil cellular sites (gov data) — canvas-rendered for ~9k points
  const cellSitesRef     = useRef<L.LayerGroup | null>(null);
  const cellSitesDataRef = useRef<CellSiteRow[] | null>(null);
  const cellCanvasRef    = useRef<L.Canvas | null>(null);

  const {
    sites, drawMode, setDrawMode, setBbox, bbox,
    isCalculating, setActiveTab,
    selectedSiteIds, routeWaypoints, setRouteWaypoints,
    crossSectionResult,
    activeBaseMapId, mapLayers,
    cellSiteFilter, cellSitesVersion,
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

  // ── Cellular towers (OpenStreetMap via backend proxy → Overpass, cached) ──────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const visible = mapLayers.find((l) => l.id === "cellular")?.visible ?? false;
    const clearTowers = () => {
      if (cellularRef.current) { map.removeLayer(cellularRef.current); cellularRef.current = null; }
    };

    if (!visible) { clearTowers(); return; }

    const MIN_ZOOM = 9;
    let ctrl: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let zoomHintShown = false;
    let lastErr = "";

    const fetchTowers = async () => {
      ctrl?.abort();                       // drop any in-flight request for an older viewport
      if (map.getZoom() < MIN_ZOOM) {
        clearTowers();
        if (!zoomHintShown) { zoomHintShown = true; notify("🔍 התקרב למפה (זום 9 ומעלה) כדי לטעון אתרי סלולר מ-OSM"); }
        return;
      }
      zoomHintShown = false;
      const myCtrl = ctrl = new AbortController();
      const b = map.getBounds();
      try {
        // Existing markers stay on the map while loading
        const data = await osmTowersApi.list(
          { south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() },
          myCtrl.signal,
        );
        if (myCtrl.signal.aborted) return;

        const group = L.layerGroup();
        data.elements.forEach((el) => {
          // OSM tags are user-editable → escape before interpolating into HTML
          const operator = esc(el.operator ?? "לא ידוע");
          const height   = esc(el.height ?? "—");
          const icon = L.divIcon({
            className: "",
            html: `<div title="${operator}" style="
              width:20px;height:20px;border-radius:50%;
              background:#f59e0b;border:2px solid #fff;
              display:flex;align-items:center;justify-content:center;
              font-size:11px;box-shadow:0 0 6px rgba(0,0,0,0.4);">📡</div>`,
            iconSize: [20, 20], iconAnchor: [10, 10],
          });
          L.marker([el.lat, el.lon], { icon })
            .bindPopup(`
              <div style="font-family:sans-serif;font-size:12px;direction:rtl;min-width:150px">
                <b>📡 מגדל סלולר</b><br>
                <span style="color:#555">מפעיל: <b>${operator}</b></span><br>
                <span style="color:#555">גובה: ${height} מ'</span><br>
                <span style="color:#aaa;font-size:10px">${el.lat.toFixed(5)}°N, ${el.lon.toFixed(5)}°E</span>
              </div>`)
            .addTo(group);
        });
        clearTowers();
        cellularRef.current = group;
        group.addTo(map);
        lastErr = "";
        notify(`📡 נטענו ${data.count} אתרי סלולר (OSM)${data.cached ? " (מטמון)" : ""}`);
      } catch (err: any) {
        if (myCtrl.signal.aborted || err?.code === "ERR_CANCELED") return;
        // Keep the markers already shown; surface the server's Hebrew detail once per distinct message
        const msg = apiErrorText(err, "שגיאה בטעינת אתרי סלולר מ-OSM");
        if (msg !== lastErr) { lastErr = msg; notify(`⚠ ${msg}`); }
      }
    };

    const onMoveEnd = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(fetchTowers, 700);
    };

    fetchTowers();
    map.on("moveend", onMoveEnd);
    return () => {
      map.off("moveend", onMoveEnd);
      if (timer) clearTimeout(timer);
      ctrl?.abort();
    };
  }, [mapLayers.find((l) => l.id === "cellular")?.visible]);

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
        let n = 0;
        for (const s of cellSitesDataRef.current) {
          if (!ops.has(s.operator)) continue;
          const building = s.status === "construction";
          if (building && !cellSiteFilter.showConstruction) continue;
          const color = (CELL_OPERATORS[s.operator] ?? CELL_OPERATORS.OTHER).color;
          const m = L.circleMarker([s.lat, s.lon], {
            renderer,
            radius: building ? 5 : 4,
            color: building ? color : "#ffffff",
            weight: building ? 2 : 1,
            dashArray: building ? "2 2" : undefined,
            fillColor: color,
            fillOpacity: building ? 0.15 : 0.9,
          });
          m.bindPopup("טוען…", { maxWidth: 320 });
          m.on("popupopen", async (e: L.PopupEvent) => {
            try {
              e.popup.setContent(cellSitePopupHtml(await cellSitesApi.get(s.id)));
            } catch {
              e.popup.setContent("⚠ שגיאה בטעינת פרטי האתר");
            }
          });
          m.addTo(group);
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

      try {
        const blobUrl = await coverageApi.resultPng(layer.resultId, layer.bbox);
        covBlobsRef.current.set(layer.id, blobUrl);
        const overlay = L.imageOverlay(
          blobUrl,
          [[layer.bbox.sw_lat, layer.bbox.sw_lon], [layer.bbox.ne_lat, layer.bbox.ne_lon]],
          { opacity: 1, interactive: false, zIndex: 300 },
        );
        covOverlaysRef.current.set(layer.id, overlay);
        if (layer.visible) overlay.addTo(map);
      } catch { /* network issue — silent */ }
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

  // ── Sync site markers ────────────────────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const siteIds = new Set(sites.map((s) => s.id));

    // Remove stale markers
    markersRef.current.forEach((marker, id) => {
      if (!siteIds.has(id)) { map.removeLayer(marker); markersRef.current.delete(id); }
    });

    // Add new markers
    sites.forEach((site) => {
      if (markersRef.current.has(site.id)) return;
      const color = FREQ_COLORS[site.frequency] ?? "#00d4ff";
      const icon = L.divIcon({
        className: "",
        html: `<div style="display:flex;flex-direction:column;align-items:center;">
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
            <b style="font-size:13px">${site.name}</b>
            <div style="margin:4px 0;border-top:1px solid #eee;"></div>
            <span style="color:#555;font-size:11px">📡 ${site.frequency} MHz</span><br>
            <span style="color:#555;font-size:11px">🏔 גובה מ"פ: <b>${site.elevation_m ?? 0} מ'</b> ASL</span><br>
            <span style="color:#555;font-size:11px">🏗 גובה אנטנה: <b>${site.ant_height} מ'</b> AGL</span><br>
            <span style="color:#555;font-size:11px">⚡ הספק: ${site.tx_power} dBm &nbsp;|&nbsp; סף: ${site.rx_threshold} dBm</span><br>
            <span style="color:#555;font-size:11px">🔭 רדיוס: ${site.max_radius} ק"מ</span>
            ${site.notes ? `<br><i style="color:#aaa;font-size:11px">${site.notes}</i>` : ""}
          </div>
        `);

      marker.on("contextmenu", () => {
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

      markersRef.current.set(site.id, marker);
    });
  }, [sites]);

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
      losLayersRef.current.push(line);
    });
  }, [bbox, selectedSiteIds, sites]);

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

      // Drag handle – visible only when not calculating
      if (!isCalculating) {
        const centerLat = (bbox.sw_lat + bbox.ne_lat) / 2;
        const centerLng = (bbox.sw_lon + bbox.ne_lon) / 2;
        const latSpan   = bbox.ne_lat - bbox.sw_lat;
        const lngSpan   = bbox.ne_lon - bbox.sw_lon;

        const dragIcon = L.divIcon({
          className: "",
          html: `<div title="גרור להזזת תא השטח" style="
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
          {drawMode === "point" ? "בחירת מיקום - לחץ על המפה" : "שרטוט - לחץ, החזק וגרור לקביעת תא השטח"}
          <button className={styles.cancelDraw} onClick={() => setDrawMode(null)}>ביטול</button>
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
