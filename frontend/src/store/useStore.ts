/**
 * Global state – Zustand
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Site, CoverageResult, CrossSectionResult } from "../services/api";

interface BBox {
  sw_lat: number; sw_lon: number;
  ne_lat: number; ne_lon: number;
}

// ── Layer management ──────────────────────────────────────────────────────────

export type BaseMapId =
  | "esri-street"
  | "esri-gray"
  | "stadia-dark"
  | "stadia-osm"
  | "topo"
  | "satellite";

export type LayerType = "cellular" | "dem" | "coverage";

export interface MapLayer {
  id:        string;
  name:      string;
  type:      LayerType;
  visible:   boolean;
  // Coverage-layer metadata (optional for other types)
  resultId?:   number;
  bbox?:       BBox;
  mode?:       string;
  coveredPct?: number;
  siteName?:   string;
  createdAt?:  string;
}

const SYSTEM_LAYERS: MapLayer[] = [
  { id: "cellular", name: "אתרי סלולר אזרחיים", type: "cellular", visible: false },
  { id: "dem",      name: "גבהים (DEM)",          type: "dem",      visible: false },
];

// ── AppState ──────────────────────────────────────────────────────────────────

interface AppState {
  // Auth
  token: string | null;
  user: { id: number; username: string; full_name: string; role: string } | null;
  setAuth: (token: string, user: AppState["user"]) => void;
  logout: () => void;

  // Sites
  sites: Site[];
  activeSiteId: number | null;
  editingSite: Site | null;
  setSites: (sites: Site[]) => void;
  addSite: (site: Site) => void;
  removeSite: (id: number) => void;
  setActiveSite: (id: number | null) => void;
  setEditingSite: (site: Site | null) => void;

  // Coverage polygon
  bbox: BBox | null;
  setBbox: (bbox: BBox | null) => void;

  // Analysis
  analysisMode: "DTM" | "DSM";
  setAnalysisMode: (mode: "DTM" | "DSM") => void;
  selectedSiteIds: number[];
  setSelectedSiteIds: (ids: number[]) => void;
  coverageResults: CoverageResult[];
  setCoverageResults: (rs: CoverageResult[]) => void;
  setCoverageResult: (r: CoverageResult | null) => void;

  // Cross-section
  routeWaypoints: { lat: number; lon: number }[];
  setRouteWaypoints: (wps: { lat: number; lon: number }[]) => void;
  crossSectionResult: CrossSectionResult | null;
  setCrossSectionResult: (r: CrossSectionResult | null) => void;

  // UI
  drawMode: "rect" | "square" | "point" | "waypoint" | null;
  setDrawMode: (m: "rect" | "square" | "point" | "waypoint" | null) => void;
  pickedLocation: { lat: number; lon: number } | null;
  setPickedLocation: (loc: { lat: number; lon: number } | null) => void;
  isCalculating: boolean;
  setIsCalculating: (v: boolean) => void;
  activeTab: "sites" | "new" | "analysis" | "history" | "crosssection" | "layers";
  setActiveTab: (t: AppState["activeTab"]) => void;

  // ── Layer management ────────────────────────────────────────────────────────
  activeBaseMapId: BaseMapId;
  setActiveBaseMapId: (id: BaseMapId) => void;
  mapLayers: MapLayer[];
  addMapLayer:    (layer: MapLayer) => void;
  removeMapLayer: (id: string) => void;
  toggleMapLayer: (id: string) => void;
  renameMapLayer: (id: string, name: string) => void;
}

export const useStore = create<AppState>()(
  persist(
    (set) => ({
      // Auth
      token: null,
      user: null,
      setAuth: (token, user) => {
        localStorage.setItem("token", token);
        set({ token, user });
      },
      logout: () => {
        localStorage.removeItem("token");
        set({
          token: null, user: null, sites: [], activeSiteId: null,
          bbox: null, coverageResults: [], selectedSiteIds: [],
        });
      },

      // Sites
      sites: [],
      activeSiteId: null,
      editingSite: null,
      setSites:       (sites) => set({ sites }),
      addSite:        (site)  => set((s) => ({ sites: [...s.sites, site] })),
      removeSite:     (id)    => set((s) => ({
        sites: s.sites.filter((x) => x.id !== id),
        activeSiteId: s.activeSiteId === id ? null : s.activeSiteId,
      })),
      setActiveSite:  (id)   => set({ activeSiteId: id }),
      setEditingSite: (site) => set({ editingSite: site }),

      // Coverage
      bbox:             null,
      setBbox:          (bbox) => set({ bbox }),
      analysisMode:     "DTM",
      setAnalysisMode:  (mode) => set({ analysisMode: mode }),
      selectedSiteIds:  [],
      setSelectedSiteIds: (ids) => set({ selectedSiteIds: ids }),
      coverageResults:  [],
      setCoverageResults: (rs) => set({ coverageResults: rs }),
      setCoverageResult:  (r)  => set({ coverageResults: r ? [r] : [] }),

      // Cross-section
      routeWaypoints:       [],
      setRouteWaypoints:    (wps) => set({ routeWaypoints: wps }),
      crossSectionResult:   null,
      setCrossSectionResult: (r) => set({ crossSectionResult: r }),

      // UI
      drawMode:        null,
      setDrawMode:     (m)   => set({ drawMode: m }),
      pickedLocation:  null,
      setPickedLocation: (loc) => set({ pickedLocation: loc }),
      isCalculating:   false,
      setIsCalculating: (v)  => set({ isCalculating: v }),
      activeTab:       "sites",
      setActiveTab:    (t)   => set({ activeTab: t }),

      // ── Layers ──────────────────────────────────────────────────────────────
      activeBaseMapId: "esri-street",
      setActiveBaseMapId: (id) => set({ activeBaseMapId: id }),

      mapLayers: SYSTEM_LAYERS,

      addMapLayer: (layer) =>
        set((s) => ({
          mapLayers: [...s.mapLayers, layer],
        })),

      removeMapLayer: (id) =>
        set((s) => ({
          mapLayers: s.mapLayers.filter((l) => l.id !== id),
        })),

      toggleMapLayer: (id) =>
        set((s) => ({
          mapLayers: s.mapLayers.map((l) =>
            l.id === id ? { ...l, visible: !l.visible } : l,
          ),
        })),

      renameMapLayer: (id, name) =>
        set((s) => ({
          mapLayers: s.mapLayers.map((l) =>
            l.id === id ? { ...l, name } : l,
          ),
        })),
    }),
    {
      name: "coverageops-storage",
      partialize: (s) => ({
        token:          s.token,
        user:           s.user,
        analysisMode:   s.analysisMode,
        activeBaseMapId: s.activeBaseMapId,
        // Persist layer metadata (not blobs — those are fetched on demand)
        mapLayers:      s.mapLayers,
      }),
    }
  )
);
