/**
 * Global state – Zustand
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";
import type {
  Site, CoverageResult, CrossSectionResult, LinkResult, BestServersResult, TransmitterKind,
} from "../services/api";

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
  thresholdDbm?: number | null;   // threshold_dbm returned with the result; null = legacy (never the site's current rx_threshold)
  demSource?:    string | null;   // "terrarium" = real DEM; anything else = simulated
  siteName?:   string;
  siteId?:     number;   // analysis site the result belongs to (absent on older persisted layers)
  createdAt?:  string;
}

const SYSTEM_LAYERS: MapLayer[] = [
  { id: "cellsites", name: "אתרי סלולר – מאגר ממשלתי", type: "cellular", visible: false },
  { id: "dem",      name: "גבהים (DEM)",          type: "dem",      visible: false },
];

// ── Point-to-point link ───────────────────────────────────────────────────────

export type DrawMode = "rect" | "square" | "point" | "waypoint" | "linkpoint";
export type SidebarTab = "sites" | "new" | "analysis" | "history" | "crosssection" | "layers" | "link";
export type LinkSubMode = "link" | "best";

/** Chosen transmitter for the link tab. `id: null` = the kind is chosen but no site picked yet. */
export interface LinkTransmitterSel {
  kind: TransmitterKind;
  id: number | null;
  /** cell_site only: band to use (MHz); omitted → the server's estimate */
  frequency_mhz?: number | null;
}

/** Receiver options kept across tab switches (point itself is `linkReceiver`). */
export interface LinkReceiverOpts {
  device_profile_id: number | null;
  height_agl: string;        // raw input strings; "" = use the profile value
  sensitivity_dbm: string;
  antenna_gain_dbi: string;
  name: string;
}

const EMPTY_RX_OPTS: LinkReceiverOpts = {
  device_profile_id: null, height_agl: "", sensitivity_dbm: "", antenna_gain_dbi: "", name: "",
};

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
  drawMode: DrawMode | null;
  setDrawMode: (m: DrawMode | null) => void;
  pickedLocation: { lat: number; lon: number } | null;
  setPickedLocation: (loc: { lat: number; lon: number } | null) => void;
  isCalculating: boolean;
  setIsCalculating: (v: boolean) => void;
  activeTab: SidebarTab;
  setActiveTab: (t: AppState["activeTab"]) => void;

  // ── Layer management ────────────────────────────────────────────────────────
  activeBaseMapId: BaseMapId;
  setActiveBaseMapId: (id: BaseMapId) => void;
  mapLayers: MapLayer[];
  addMapLayer:    (layer: MapLayer) => void;
  removeMapLayer: (id: string) => void;
  /** Remove every coverage-result layer from the map/layer list (results stay in history). */
  clearCoverageLayers: () => void;
  toggleMapLayer: (id: string) => void;
  renameMapLayer: (id: string, name: string) => void;

  // ── Civil cellular sites layer ──────────────────────────────────────────────
  cellSiteFilter: { operators: string[]; showConstruction: boolean };
  setCellSiteFilter: (f: Partial<AppState["cellSiteFilter"]>) => void;
  cellSitesVersion: number;          // bump to force re-fetch after a sync
  bumpCellSitesVersion: () => void;

  // ── Point-to-point link tab ─────────────────────────────────────────────────
  linkSubMode: LinkSubMode;
  setLinkSubMode: (m: LinkSubMode) => void;
  linkTransmitter: LinkTransmitterSel | null;
  setLinkTransmitter: (t: LinkTransmitterSel | null) => void;
  linkReceiver: { lat: number; lon: number } | null;
  setLinkReceiver: (p: { lat: number; lon: number } | null) => void;
  linkReceiverOpts: LinkReceiverOpts;
  setLinkReceiverOpts: (o: Partial<LinkReceiverOpts>) => void;
  linkResult: LinkResult | null;
  setLinkResult: (r: LinkResult | null) => void;
  bestServersResult: BestServersResult | null;
  setBestServersResult: (r: BestServersResult | null) => void;
  /** Cell-site id of the best-servers row highlighted on the map */
  highlightedServerId: number | null;
  setHighlightedServerId: (id: number | null) => void;
  /** While true, the next click on a cell-site marker picks it as the link transmitter */
  linkPickTransmitter: boolean;
  setLinkPickTransmitter: (v: boolean) => void;
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
          linkTransmitter: null, linkReceiver: null, linkResult: null,
          bestServersResult: null, highlightedServerId: null, linkPickTransmitter: false,
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
          // Update an existing layer with the same id (e.g. re-loading a result), keeping a user rename
          mapLayers: s.mapLayers.some((l) => l.id === layer.id)
            ? s.mapLayers.map((l) => (l.id === layer.id ? { ...l, ...layer, name: l.name } : l))
            : [...s.mapLayers, layer],
        })),

      removeMapLayer: (id) =>
        set((s) => ({
          mapLayers: s.mapLayers.filter((l) => l.id !== id),
        })),

      clearCoverageLayers: () =>
        set((s) => ({
          mapLayers: s.mapLayers.filter((l) => l.type !== "coverage"),
          coverageResults: [],
        })),

      toggleMapLayer: (id) =>
        set((s) => ({
          mapLayers: s.mapLayers.map((l) =>
            l.id === id ? { ...l, visible: !l.visible } : l,
          ),
        })),

      cellSiteFilter: { operators: ["PHI", "PELEPHONE", "CELLCOM", "OTHER"], showConstruction: true },
      setCellSiteFilter: (f) => set((s) => ({ cellSiteFilter: { ...s.cellSiteFilter, ...f } })),
      cellSitesVersion: 0,
      bumpCellSitesVersion: () => set((s) => ({ cellSitesVersion: s.cellSitesVersion + 1 })),

      // ── Link ────────────────────────────────────────────────────────────────
      linkSubMode: "link",
      setLinkSubMode: (m) => set({ linkSubMode: m }),
      linkTransmitter: null,
      setLinkTransmitter: (t) => set({ linkTransmitter: t }),
      linkReceiver: null,
      setLinkReceiver: (p) => set({ linkReceiver: p }),
      linkReceiverOpts: EMPTY_RX_OPTS,
      setLinkReceiverOpts: (o) => set((s) => ({ linkReceiverOpts: { ...s.linkReceiverOpts, ...o } })),
      linkResult: null,
      setLinkResult: (r) => set({ linkResult: r }),
      bestServersResult: null,
      setBestServersResult: (r) => set({ bestServersResult: r, highlightedServerId: null }),
      highlightedServerId: null,
      setHighlightedServerId: (id) => set({ highlightedServerId: id }),
      linkPickTransmitter: false,
      setLinkPickTransmitter: (v) => set({ linkPickTransmitter: v }),

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
        cellSiteFilter: s.cellSiteFilter,
      }),
      // Make sure system layers added in newer versions appear for existing users
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<AppState>;
        const saved = p.mapLayers ?? [];
        const sysIds = new Set(SYSTEM_LAYERS.map((l) => l.id));
        const mapLayers = [
          ...SYSTEM_LAYERS.map((sl) => {
            const old = saved.find((l) => l.id === sl.id);
            return old ? { ...sl, visible: old.visible } : sl;
          }),
          // "cellular" = removed OSM layer; drop it from older persisted state
          ...saved.filter((l) => !sysIds.has(l.id) && l.id !== "cellular"),
        ];
        return { ...current, ...p, mapLayers };
      },
    }
  )
);
