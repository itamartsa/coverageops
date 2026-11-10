/**
 * CoverageOps – API Service
 * Centralizes all backend communication.
 */
import axios from "axios";
import { useStore } from "../store/useStore";

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

const api = axios.create({
  baseURL: BASE,
  headers: { "Content-Type": "application/json" },
});

// ── Attach JWT to every request ───────────────────────────────────────────────
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// ── Auto-logout on 401 ────────────────────────────────────────────────────────
// A 401 from the login request itself is a wrong password — the login form shows
// the server message, so no redirect (a reload would wipe that message).
api.interceptors.response.use(
  (r) => r,
  (err) => {
    const isLogin = String(err.config?.url ?? "").includes("/api/auth/login");
    if (err.response?.status === 401 && !isLogin) {
      // Drop the persisted session too, otherwise "/" would load with a dead token
      useStore.getState().logout();
      if (window.location.pathname !== "/login") window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

// ── Error text ────────────────────────────────────────────────────────────────
function detailToText(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length)
    return detail.map((d: any) => d?.msg ?? String(d)).join("; ");
  return null;
}

/** Server `detail` (e.g. Hebrew 413/400 "area too large") or a fallback. JSON responses only. */
export function apiErrorText(err: any, fallback = "שגיאה בחישוב"): string {
  return detailToText(err?.response?.data?.detail) ?? err?.message ?? fallback;
}

/** Same as apiErrorText, but also reads `detail` from a Blob body (requests with responseType "blob"). */
export async function apiErrorTextAsync(err: any, fallback = "שגיאה בחישוב"): Promise<string> {
  const data = err?.response?.data;
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    try {
      const t = detailToText(JSON.parse(await data.text())?.detail);
      if (t) return t;
    } catch { /* not JSON */ }
    return err?.message ?? fallback;
  }
  return apiErrorText(err, fallback);
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export const authApi = {
  login: async (username: string, password: string) => {
    const form = new FormData();
    form.append("username", username);
    form.append("password", password);
    const { data } = await api.post("/api/auth/login", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  },
  me: async () => (await api.get("/api/auth/me")).data,
  register: async (payload: {
    username: string; full_name: string; password: string; role: string;
  }) => (await api.post("/api/auth/register", payload)).data,
};

// ── Sites ─────────────────────────────────────────────────────────────────────
export interface SiteCreate {
  name: string;
  lat: number; lon: number;
  /** Omit to let the backend apply the default (10 m AGL) and flag it as default. */
  ant_height?: number;
  frequency: number;
  tx_power: number;
  rx_threshold: number;
  max_radius: number;
  notes?: string;
}

/** Antenna height (m AGL) the backend uses when none is entered – keep in sync with sites.py. */
export const DEFAULT_ANT_HEIGHT_M = 10;

export type ParamsSource = "manual" | "estimated";

export interface Site extends SiteCreate {
  id: number;
  ant_height: number;
  /** True while ant_height is the system default and was never set by a user. */
  ant_height_is_default: boolean;
  elevation_m: number;   // auto-fetched by backend from terrain tiles
  owner_id: number;
  created_at: string;
  // Provenance — set when the site was promoted from a civil cell site
  cell_site_id?:  number | null;
  params_source?: ParamsSource | null;
  source_note?:   string | null;
}

export const sitesApi = {
  list:   async (): Promise<Site[]>  => (await api.get("/api/sites/")).data,
  create: async (s: SiteCreate)      => (await api.post("/api/sites/", s)).data,
  update: async (id: number, s: Partial<SiteCreate>) =>
    (await api.put(`/api/sites/${id}`, s)).data,
  delete: async (id: number)         => api.delete(`/api/sites/${id}`),
};

// ── Coverage ──────────────────────────────────────────────────────────────────
export interface CoverageRequest {
  site_id:    number;
  mode:       "DTM" | "DSM";
  sw_lat:     number; sw_lon: number;
  ne_lat:     number; ne_lon: number;
  resolution?: number;
}

export type DemSource = "terrarium" | "none" | string;

export interface LevelBucket { count: number; pct: number }
export type CoverageLevel = "excellent" | "good" | "medium" | "weak" | "marginal" | "none";

export interface CoverageResult {
  id:          number;
  site_id:     number;
  mode:        string;
  /** % of area at/above the site's rx_threshold (threshold_dbm). */
  covered_pct: number;
  /** % of area with any signal (> −110 dBm) — legacy meaning of covered_pct. */
  covered_pct_any_signal?: number | null;
  threshold_dbm?: number | null;
  weak_pct?:      number | null;
  level_distribution?: Partial<Record<CoverageLevel, LevelBucket>> | null;
  rssi_avg:    number;
  rssi_max:    number;
  rssi_min:    number;
  duration_sec: number;
  created_at:  string;
  // Bounding box — used to position the PNG image overlay on the map
  sw_lat?: number; sw_lon?: number;
  ne_lat?: number; ne_lon?: number;
  // Data-quality metadata
  dem_source?:         DemSource | null;
  dem_valid_pct?:      number | null;
  dem_m_per_px?:       number | null;
  resolution?:         number | null;
  model?:              string | null;
  diffraction_method?: string | null;
  warnings?:           string[] | null;
  risk_level?:   string | null;
  /** 1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL */
  risk_score?:   number | null;
  rssi_min_all?: number | null;
  /** No longer returned by the API — use coverageApi.downloadResultGeojson(). */
  geojson?:            GeoJSON.FeatureCollection;
}

export interface HistoryItem {
  id:          number;
  site_id:     number;
  site_name:   string;
  mode:        string;
  covered_pct: number | null;
  covered_pct_any_signal?: number | null;
  threshold_dbm?: number | null;
  weak_pct?:      number | null;
  dem_source?:    DemSource | null;
  dem_valid_pct?: number | null;
  resolution?:    number | null;
  rssi_avg:    number | null;
  rssi_max:    number | null;
  rssi_min:    number | null;
  risk_level?:   string | null;
  risk_score?:   number | null;
  rssi_min_all?: number | null;
  duration_sec: number;
  created_at:  string;
  poly_sw_lat: number; poly_sw_lon: number;
  poly_ne_lat: number; poly_ne_lon: number;
}

export interface CoverageReport {
  meta: {
    result_id: number; site_name: string;
    site_lat: number; site_lon: number;
    ant_height: number; frequency: number;
    tx_power: number; rx_threshold: number; max_radius: number;
    mode: string; analysis_date: string; duration_sec: number;
    /** Threshold the report's percentages were computed against; null/absent for legacy results. */
    threshold_dbm?: number | null;
    /** True when the underlying result predates threshold_dbm (percentages = any signal). */
    legacy?: boolean;
  };
  macro: {
    covered_pct: number; rssi_avg: number; rssi_max: number; rssi_min: number;
    bbox: { sw_lat: number; sw_lon: number; ne_lat: number; ne_lon: number };
  };
  signal_distribution: Record<string, { count: number; pct: number }>;
  propagation: {
    model: string; formula: string; height_gain_db: number;
    terrain_margin_assumed_db: number;
    fspl_table_db: Record<string, number>;
    theoretical_radii_km: Record<string, number>;
  };
  operational_summary?: {
    status: string; conclusion: string;
    covered_pct: number; dead_zones_count: number;
    rssi_avg: number; rssi_min: number; rssi_max: number;
  };
  risk?: {
    level: string;
    /** 1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL */
    score?: number | null;
  };
  dead_zones_clusters?: Array<{
    centroid_lat: number; centroid_lon: number;
    cell_count: number; severity: string; rssi_avg: number;
  }>;
  recommendations?: string[];
  data_quality?: {
    dem_source?:         DemSource | null;
    dem_valid_pct?:      number | null;
    dem_m_per_px?:       number | null;
    resolution?:         number | null;
    diffraction_method?: string | null;
  } | null;
  warnings?: string[] | null;
}

// ── Cross-section ─────────────────────────────────────────────────────────────
export interface CrossSectionPoint {
  dist_along:     number;
  dist_from_site: number;
  lat:  number;
  lon:  number;
  rssi: number;
  level: string;
}

export interface CrossSectionDeadZone {
  start_km:  number;
  end_km:    number;
  length_km: number;
  start_lat: number; start_lon: number;
  end_lat:   number; end_lon:   number;
}

export interface CrossSectionResult {
  site_id:     number;
  site_name:   string;
  mode:        string;
  waypoints:   { lat: number; lon: number }[];
  total_length_km: number;
  covered_pct: number;
  rssi_min:    number;
  rssi_max:    number;
  rssi_avg:    number;
  risk_level:  string;
  points:      CrossSectionPoint[];
  dead_zones:  CrossSectionDeadZone[];
  signal_distribution: Record<string, { count: number; pct: number }>;
  recommendations: string[];
  duration_sec: number;
  /** 1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL */
  risk_score?:    number | null;
  covered_pct_any_signal?: number | null;
  threshold_dbm?: number | null;
  dem_source?:    DemSource | null;
  dem_valid_pct?: number | null;
  warnings?:      string[] | null;
}

export interface CrossSectionRequest {
  site_id:    number;
  mode:       "DTM" | "DSM";
  waypoints:  { lat: number; lon: number }[];
  resolution?: number;
}

/** Save a Blob to disk via a temporary <a download>. */
export function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a   = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Filename from a Content-Disposition header (RFC 5987 aware), or null. */
function filenameFromDisposition(cd: string | undefined): string | null {
  const m = cd?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  if (!m) return null;
  try { return decodeURIComponent(m[1]); } catch { return m[1]; }
}

/** GET an authenticated binary endpoint and save it (server filename when given). */
async function downloadAuthed(path: string, fallbackName: string): Promise<void> {
  const res = await api.get(path, { responseType: "blob" });
  const blob: Blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
  triggerBlobDownload(blob, filenameFromDisposition(res.headers?.["content-disposition"]) ?? fallbackName);
}

export const coverageApi = {
  analyze:      async (req: CoverageRequest): Promise<CoverageResult> =>
    (await api.post("/api/coverage/analyze", req)).data,
  history:      async (siteId: number) =>
    (await api.get(`/api/coverage/history/${siteId}`)).data,
  listAll:      async (): Promise<HistoryItem[]> =>
    (await api.get("/api/coverage/")).data,
  getResult:    async (id: number): Promise<CoverageResult> =>
    (await api.get(`/api/coverage/result/${id}`)).data,
  deleteResult: async (id: number): Promise<void> => {
    await api.delete(`/api/coverage/result/${id}`);
  },
  getReport:    async (id: number): Promise<CoverageReport> =>
    (await api.get(`/api/coverage/result/${id}/report`)).data,

  /** Server renders the result's stored bbox; the bbox param is kept only for
   *  backward compatibility (the server ignores it). */
  resultPng: async (
    id: number,
    bbox?: { sw_lat: number; sw_lon: number; ne_lat: number; ne_lon: number },
  ): Promise<string> => {
    const p = new URLSearchParams({ width: "900", height: "900" });
    if (bbox) {
      p.set("sw_lat", bbox.sw_lat.toString()); p.set("sw_lon", bbox.sw_lon.toString());
      p.set("ne_lat", bbox.ne_lat.toString()); p.set("ne_lon", bbox.ne_lon.toString());
    }
    const res = await api.get(`/api/coverage/result/${id}/png?${p}`, {
      responseType: "blob",
    });
    return URL.createObjectURL(res.data);
  },

  /** Relative endpoint path of the downloadable GeoJSON (needs auth header —
   *  use downloadResultGeojson rather than a plain <a href>). */
  resultGeojsonUrl: (id: number): string => `/api/coverage/result/${id}/geojson`,

  /** Fetches the GeoJSON with the JWT (axios interceptor) and triggers a download. */
  downloadResultGeojson: async (id: number, filename?: string): Promise<void> => {
    const res = await api.get(coverageApi.resultGeojsonUrl(id), { responseType: "blob" });
    const blob: Blob = res.data instanceof Blob
      ? res.data
      : new Blob([JSON.stringify(res.data)], { type: "application/geo+json" });
    const name = filename
      ?? filenameFromDisposition(res.headers?.["content-disposition"])
      ?? `coverage_${id}.geojson`;
    triggerBlobDownload(blob, name);
  },

  crossSection: async (req: CrossSectionRequest): Promise<CrossSectionResult> =>
    (await api.post("/api/coverage/cross-section", req)).data,

  crossSectionDocx: async (req: CrossSectionRequest): Promise<Blob> => {
    const res = await api.post("/api/coverage/cross-section/docx", req, {
      responseType: "blob",
    });
    return res.data;
  },
};

// ── Users (Admin only) ────────────────────────────────────────────────────────
export interface UserOut {
  id:         number;
  username:   string;
  full_name:  string;
  role:       string;
  is_active:  boolean;
  last_login: string | null;
  created_at: string;
}

export const usersApi = {
  list:   async (): Promise<UserOut[]> =>
    (await api.get("/api/users/")).data,
  create: async (payload: { username: string; full_name: string; password: string; role: string }) =>
    (await api.post("/api/users/", payload)).data,
  update: async (
    id: number,
    payload: { full_name?: string; role?: string; is_active?: boolean; password?: string }
  ) => (await api.put(`/api/users/${id}`, payload)).data,
  remove: async (id: number) => api.delete(`/api/users/${id}`),
};

// ── Civil cellular sites (data.gov.il) ────────────────────────────────────────
/** "estimated" = by site type (not in the government data); "manual" = entered by a user */
export type CellSiteHeightSource = "estimated" | "manual";
export type CellSiteRfSource = "estimated" | "manual";

/** Antenna-height fields shared by the list row and the detail record */
export interface CellSiteHeight {
  ant_height_m: number | null;
  height_source: CellSiteHeightSource;
  height_note: string | null;
  height_updated_at: string | null;   // ISO, UTC ("…Z")
}

/** RF fields (EIRP + frequency) — estimated by site type / lowest band unless entered by a user.
 *  Optional: older backends don't send them. */
export interface CellSiteRf {
  tx_power_dbm?: number | null;        // EIRP, dBm
  frequency_mhz?: number | null;
  /** Legacy whole-record flag; prefer the per-field sources below when present */
  rf_source?: CellSiteRfSource | null;
  tx_power_source?: CellSiteRfSource | null;
  frequency_source?: CellSiteRfSource | null;
  rf_note?: string | null;
  rf_updated_at?: string | null;
  /** Representative bands (MHz) of the site's technologies, ascending */
  bands?: number[] | null;
}

export interface CellSiteRow extends CellSiteHeight, CellSiteRf {
  id: number; lat: number; lon: number;
  operator: string; status: "active" | "construction";
  site_type: string; techs: string;
}

export interface CellSiteTypeDefault {
  site_type: string; default_height_m: number;
  default_tx_power_dbm?: number | null;
  label_he: string | null; note_he: string | null;
}

export interface CellSiteBandDefault {
  generation: string;          // "2G" | "3G" | "4G" | "5G"
  frequency_mhz: number;
  label_he: string | null;
}

export interface CellSiteDetail extends CellSiteHeight, CellSiteRf {
  height_updated_by: string | null;
  rf_updated_by?: string | null;
  id: number; status: string; operator: string; operator_he: string | null;
  site_code: string | null; city: string | null; address: string | null;
  authority: string | null; jurisdiction: string | null;
  site_type: string | null; site_type_he: string | null; technologies: string | null;
  lat: number; lon: number;
  construction_permit_date: string | null; operation_permit_date: string | null;
  last_periodic_check: string | null; radiation_permit: string | null;
  max_power_uw_cm2: number | null; max_pct_of_threshold: number | null;
  construction_permit_url: string | null; operation_permit_url: string | null;
  request_date: string | null; permit_type: string | null;
}

export interface CellSitesMeta {
  source: {
    publisher: string; portal: string; update_frequency: string; crs_note: string;
    datasets: { id: string; title: string; url: string }[];
  };
  total: number;
  by_status: Record<string, number>;
  by_operator: Record<string, number>;
  by_site_type: Record<string, number>;
  by_technology: Record<string, number>;
  last_sync: null | {
    synced_at: string; source: "live" | "snapshot"; data_fetched_at: string | null;
    active: number; construction: number; skipped: number; note: string | null;
  };
  height_estimation: {
    note_he: string;
    defaults: CellSiteTypeDefault[];
    manual_count: number;
    estimated_count: number;
  };
  rf_estimation?: {
    note_he?: string | null;
    type_defaults?: { site_type: string; label_he: string | null; default_tx_power_dbm: number | null }[];
    band_defaults?: CellSiteBandDefault[];
    manual_count?: number;
    estimated_count?: number;
  } | null;
}

export interface CellSiteRfUpdate { tx_power_dbm?: number; frequency_mhz?: number; note?: string | null }

/** Result of promoting a civil cell site to an analysis Site. `created` = 201 (false = already existed). */
export interface PromoteResult { site: Site; created: boolean }

export const cellSitesApi = {
  list: async (): Promise<CellSiteRow[]> => {
    const { data } = await api.get("/api/cell-sites/");
    const f: string[] = data.fields;
    return data.rows.map((r: unknown[]) =>
      Object.fromEntries(f.map((k, i) => [k, r[i]])) as unknown as CellSiteRow);
  },
  meta: async (): Promise<CellSitesMeta> => (await api.get("/api/cell-sites/meta")).data,
  get:  async (id: number): Promise<CellSiteDetail> => (await api.get(`/api/cell-sites/${id}`)).data,
  sync: async () => (await api.post("/api/cell-sites/sync", null, { timeout: 120000 })).data,
  /** Record the real antenna height (ADMIN/OPERATOR) — marks the site "manual" */
  setHeight: async (id: number, ant_height_m: number, note?: string): Promise<CellSiteDetail> =>
    (await api.patch(`/api/cell-sites/${id}/height`, { ant_height_m, note: note || null })).data,
  /** Drop the manual height and return to the estimate for the site type */
  resetHeight: async (id: number): Promise<CellSiteDetail> =>
    (await api.delete(`/api/cell-sites/${id}/height`)).data,
  /** Record real EIRP and/or frequency (ADMIN/OPERATOR) — marks the site's RF "manual" */
  setRf: async (id: number, body: CellSiteRfUpdate): Promise<CellSiteDetail> =>
    (await api.patch(`/api/cell-sites/${id}/rf`, { ...body, note: body.note || null })).data,
  /** Drop manual RF and return to the estimates (type EIRP, lowest band) */
  resetRf: async (id: number): Promise<CellSiteDetail> =>
    (await api.delete(`/api/cell-sites/${id}/rf`)).data,
  /** Create (201) or return the caller's existing (200) analysis Site for this cell site */
  promote: async (id: number, body: { frequency_mhz?: number; rx_threshold?: number } = {}): Promise<PromoteResult> => {
    const res = await api.post(`/api/cell-sites/${id}/promote`, body);
    return { site: res.data, created: res.status === 201 };
  },
  typeDefaults: async (): Promise<CellSiteTypeDefault[]> =>
    (await api.get("/api/cell-sites/type-defaults")).data,
  /** ADMIN — also updates every still-estimated site of that type.
   *  A number = default_height_m (backward-compatible call form). */
  setTypeDefault: async (
    site_type: string,
    body: number | { default_height_m?: number; default_tx_power_dbm?: number },
  ): Promise<CellSiteTypeDefault & { updated_sites?: number; updated_rf_sites?: number }> =>
    (await api.put(`/api/cell-sites/type-defaults/${encodeURIComponent(site_type)}`,
      typeof body === "number" ? { default_height_m: body } : body)).data,
  bandDefaults: async (): Promise<CellSiteBandDefault[]> =>
    (await api.get("/api/cell-sites/band-defaults")).data,
  /** ADMIN — RF-estimated sites whose default band changes get the new frequency */
  setBandDefault: async (generation: string, frequency_mhz: number)
    : Promise<CellSiteBandDefault & Record<string, unknown>> =>
    (await api.put(`/api/cell-sites/band-defaults/${encodeURIComponent(generation)}`, { frequency_mhz })).data,
};

// ── Device (receiver) profiles ────────────────────────────────────────────────
export interface DeviceProfileIn {
  key: string;
  name_he: string;
  rx_height_m: number;
  rx_sensitivity_dbm: number;
  antenna_gain_dbi: number;
  description_he?: string | null;
  is_default?: boolean;
}
export interface DeviceProfile extends DeviceProfileIn {
  id: number;
  description_he: string | null;
  is_default: boolean;
}

export const deviceProfilesApi = {
  list:   async (): Promise<DeviceProfile[]> => (await api.get("/api/device-profiles/")).data,
  create: async (p: DeviceProfileIn): Promise<DeviceProfile> => (await api.post("/api/device-profiles/", p)).data,
  update: async (id: number, p: Partial<DeviceProfileIn>): Promise<DeviceProfile> =>
    (await api.put(`/api/device-profiles/${id}`, p)).data,
  remove: async (id: number): Promise<void> => { await api.delete(`/api/device-profiles/${id}`); },
};

// ── Point-to-point link ───────────────────────────────────────────────────────
export type LinkMode = "DTM" | "DSM";
export type TransmitterKind = "site" | "cell_site";

export interface LinkReceiverIn {
  lat: number; lon: number;
  device_profile_id?: number;
  height_agl?: number;
  sensitivity_dbm?: number;
  antenna_gain_dbi?: number;
  name?: string;
}

export interface LinkRequest {
  transmitter: { kind: TransmitterKind; id: number; frequency_mhz?: number };
  receiver: LinkReceiverIn;
  mode: LinkMode;
  save?: boolean;
}

export interface LinkProfilePoint {
  dist_km: number; lat: number; lon: number;
  terrain_m: number | null;
  los_m: number;
  fresnel_m: number;
}

export interface LinkObstacle {
  dist_km: number; lat: number; lon: number;
  terrain_m: number; los_m: number; nu: number;
}

export interface LinkResult {
  id?: number | null;
  created_at?: string | null;
  distance_km: number;
  bearing_deg: number;
  tx_h_asl?: number | null; rx_h_asl?: number | null;
  tx_ground_asl?: number | null; rx_ground_asl?: number | null;
  fspl_db?: number | null;
  diffraction_db?: number | null;
  clutter_db?: number | null;
  height_gain_db?: number | null;
  rx_gain_db?: number | null;
  rssi_dbm: number;
  margin_db: number;
  level: string;
  is_los: boolean;
  fresnel_clearance_pct?: number | null;
  worst_obstacle?: LinkObstacle | null;
  profile?: LinkProfilePoint[] | null;
  required_rx_height_m?: number | null;
  dem_source?: DemSource | null;
  dem_valid_pct?: number | null;
  mode: string;
  recommendations?: string[] | null;
  transmitter: {
    kind: TransmitterKind; id: number; name?: string | null;
    lat: number; lon: number;
    ant_height_m?: number | null; frequency_mhz?: number | null; tx_power_dbm?: number | null;
    params_source?: ParamsSource | null;
    estimated_fields?: string[] | null;
  };
  receiver: {
    lat: number; lon: number;
    height_agl?: number | null; sensitivity_dbm?: number | null; antenna_gain_dbi?: number | null;
    profile_key?: string | null; profile_name?: string | null; name?: string | null;
  };
  warnings?: string[] | null;
}

export interface LinkSummary {
  id: number;
  created_at: string;
  tx_name?: string | null;
  tx_kind?: TransmitterKind | null;
  rx_lat: number; rx_lon: number;
  rx_profile_key?: string | null;
  mode?: string | null;
  distance_km?: number | null;
  rssi_dbm?: number | null;
  margin_db?: number | null;
  level?: string | null;
  is_los?: boolean | null;
}

export type BestServersOperator = "PHI" | "PELEPHONE" | "CELLCOM";
export interface BestServersRequest {
  receiver: LinkReceiverIn;
  mode: LinkMode;
  radius_km?: number;
  operator?: BestServersOperator;
  top_n?: number;
  band_policy?: "lowest" | "highest";
}

export interface BestServerEntry {
  rank: number;
  cell_site: {
    id: number; operator: string; operator_he?: string | null;
    site_type?: string | null; site_type_he?: string | null;
    city?: string | null; address?: string | null;
    lat: number; lon: number;
    ant_height_m?: number | null; height_source?: CellSiteHeightSource | null;
    frequency_mhz?: number | null; tx_power_dbm?: number | null; rf_source?: CellSiteRfSource | null;
    tx_power_source?: CellSiteRfSource | null; frequency_source?: CellSiteRfSource | null;
    bands?: number[] | null;
  };
  link: {
    distance_km: number; bearing_deg: number;
    rssi_dbm: number; margin_db: number; level: string; is_los: boolean;
    diffraction_db?: number | null; fresnel_clearance_pct?: number | null;
  };
}

export interface BestServersResult {
  receiver: LinkResult["receiver"];
  candidates_evaluated?: number | null;
  dem_source?: DemSource | null;
  dem_valid_pct?: number | null;
  results: BestServerEntry[];
  warnings?: string[] | null;
}

export const linkApi = {
  run: async (req: LinkRequest): Promise<LinkResult> =>
    (await api.post("/api/link/", { save: true, ...req }, { timeout: 120000 })).data,
  list: async (): Promise<LinkSummary[]> => (await api.get("/api/link/")).data,
  get: async (id: number): Promise<LinkResult> => (await api.get(`/api/link/${id}`)).data,
  remove: async (id: number): Promise<void> => { await api.delete(`/api/link/${id}`); },
  /** Word report of a stored link result (auth header → blob download) */
  downloadDocx: (id: number): Promise<void> => downloadAuthed(`/api/link/${id}/docx`, `link_${id}.docx`),
  bestServers: async (req: BestServersRequest): Promise<BestServersResult> =>
    (await api.post("/api/link/best-servers", req, { timeout: 180000 })).data,
};

// ── Field reception (/api/field) ──────────────────────────────────────────────
// Shapes follow backend/app/api/field.py (the code is the source of truth).
// List endpoints return {fields, rows}; they are mapped to objects like cellSitesApi.list.

/** {fields, rows} → objects */
function rowsToObjects<T>(data: { fields: string[]; rows: unknown[][] }): T[] {
  const f = data.fields;
  return data.rows.map((r) => Object.fromEntries(f.map((k, i) => [k, r[i]])) as unknown as T);
}

/** Query-string params, dropping null / undefined / "" values. */
function qs(p: object): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(p as Record<string, unknown>)) if (v !== null && v !== undefined && v !== "") out[k] = String(v);
  return out;
}

export interface FieldWindow { from: string; to: string }

export interface RsrpStat { p5: number | null; p50: number | null; n: number }

export interface FieldKpis {
  samples: number;
  time_s: number;
  /** index i = tier i (0 = marginal … 4 = excellent); denominator = classified time only */
  pct_time_by_tier: (number | null)[];
  pct_time_unclassified: number | null;
  pct_time_unknown: number | null;
  rsrp: { lte: RsrpStat; nr_sa: RsrpStat; nsa_nr_leg: RsrpStat };
  oos_minutes: number | null;
  cell_change_per_hour: number | null;
  nsa_share_pct: number | null;
  data_connected_pct: number | null;
  fallback_pct: number | null;
  stale_excluded_pct: number | null;
  age_unknown_pct: number | null;
  devices_reporting_15m: { reporting: number; expected: number; pct: number | null };
}

export interface FieldSummary {
  from: string; to: string;
  fleet: {
    total: number; active: number; degraded: number; paused: number; no_contact: number;
    offline: number; never_seen: number; withdrawn: number; revoked: number;
  };
  kpis: FieldKpis;
  kpi_notes_he: string;
  freshness: { last_sample_received_at: string | null; server_time: string };
}

export interface FieldStatusFlags {
  config_pending: boolean; no_recent_samples: boolean; upload_lag: boolean; cell_stale: boolean; coarse: boolean;
}

export interface FieldDeviceRow {
  id: string; label: string | null; ownership: "ORG" | "PERSONAL";
  group_id: number | null; group: string | null;
  status: string; status_label_he: string; status_since: string | null;
  monitoring_state: string | null; degraded_reasons: string[]; effective_location_mode: string | null;
  tier: number | null; tier_stale: boolean; rsrp: number | null; nr_ss_rsrp: number | null;
  operator_key: string | null; operator_name: string | null; network_type: string | null;
  battery_pct: number | null; is_charging: boolean | null; last_seen_at: string | null;
  app_version: string | null; config_pending: boolean; open_alerts: number; open_commands: number;
  flags: FieldStatusFlags;
}

export interface FieldLiveRow {
  id: string; label: string | null; lat: number; lon: number; acc: number | null;
  precision: "precise" | "coarse"; tier: number | null; tier_stale: boolean; rsrp: number | null;
  network_type: string | null; operator_key: string | null; status: string; loc_ts: string | null;
}

export interface FieldCommand {
  id: string; type: string; status: string; issued_by: string | number | null;
  issued_at: string | null; delivered_at: string | null; acked_at: string | null; expires_at: string | null;
}

export interface FieldAlert {
  id: number; type: string; type_label_he: string; severity: "info" | "warning" | "critical";
  status: "open" | "acked" | "resolved"; message_he: string;
  device_id: string | null; device_label: string | null;
  gx: number | null; gy: number | null; operator_key: string | null; details: Record<string, unknown> | null;
  opened_at: string | null; last_seen_at: string | null; resolved_at: string | null;
  occurrences: number; acked_by: string | null; acked_at: string | null; note: string | null;
}

export interface FieldDeviceConfig {
  sampling_interval_s: number; min_distance_m: number; stationary_interval_s: number; upload_interval_s: number;
  upload_wifi_only: boolean; active_test_interval_s: number; collect_neighbors: boolean;
  location_mode: "precise" | "coarse" | "off"; schedule: { days: number[]; start: string; end: string }[] | null;
  low_battery_threshold_pct: number; allow_user_pause: boolean; config_version?: number;
}

export interface FieldDeviceDetail {
  id: string; label: string | null; ownership: "ORG" | "PERSONAL"; admin_status: string;
  status: string; status_label_he: string; status_since: string | null; status_flags: FieldStatusFlags;
  group_id: number | null; group: string | null; notes: string | null;
  model: string | null; manufacturer: string | null; os_version: string | null; app_version: string | null;
  consent_version: string | null; consented_at: string | null; enrolled_at: string | null;
  revoked_at: string | null; withdrawn_at: string | null;
  config: FieldDeviceConfig; config_override: Partial<FieldDeviceConfig> | null;
  config_version: number | null; applied_config_version: number | null;
  monitoring_state: string | null; state_changed_at: string | null;
  degraded_reasons: { code: string; label_he: string; known: boolean }[];
  effective_location_mode: string | null; location_services_enabled: boolean | null;
  permissions: Record<string, unknown> | null;
  battery_pct: number | null; is_charging: boolean | null; queue_size: number | null;
  evicted_samples_total: number | null;
  last_heartbeat_at: string | null; last_sample_at: string | null; last_sample_received_at: string | null;
  last_sample_ts_reported: string | null;
  location: { lat: number; lon: number; acc: number | null; precision: "precise" | "coarse"; ts: string | null } | null;
  signal: {
    tier: number | null; key: string; label_he: string; token: string; min_dbm?: number | null;
    tier_stale: boolean; rsrp_dbm: number | null; nr_ss_rsrp_dbm: number | null; network_type: string | null;
    tier_basis_he: string | null; operator_key: string | null; operator_name: string | null;
    service_state: string | null;
  };
  current_cell: Record<string, unknown> | null;
  nr_leg: Record<string, unknown> | null;
  commands: FieldCommand[];
  open_alerts: FieldAlert[];
}

export type DeviceTsMetric = "rsrp" | "nr_ss_rsrp" | "nr_leg_ss_rsrp" | "rsrq" | "sinr" | "battery";
/** point = [t, min, avg, max, n, tier_mode] */
export type DeviceTsPoint = [string, number, number, number, number, number | null];
export interface DeviceTimeseries {
  metric: DeviceTsMetric; label_he: string; unit: string; bucket_s: number; from: string; to: string;
  series: { meas_tech: string; points: DeviceTsPoint[] }[];
  /** [t0, t1, reason] — reason: oos | no_data | paused | stale */
  gaps: [string, string, "oos" | "no_data" | "paused" | "stale"][];
}

export interface TrackFeature {
  type: "Feature";
  geometry: { type: "LineString" | "Point" | "Polygon"; coordinates: any };
  properties: {
    kind: "segment" | "coarse_cell" | "point"; tier: number | null; stale: boolean;
    from?: string; to?: string; ts?: string; rsrp_dbm?: number | null; network_type?: string | null;
  };
}
export interface DeviceTrack {
  type: "FeatureCollection"; features: TrackFeature[];
  properties: { device_id: string; from: string; to: string; points_total: number; points_returned: number; thinned: boolean };
}

export type HeatMeasure = "lte_rsrp" | "nr_ss_rsrp";
export type HeatMetric = "p50" | "p5" | "tier" | "n" | "oos_pct";
export interface HeatCell { gx: number; gy: number; n: number; value: number | null; tier: number | null; n_devices?: number }
export interface HeatmapResult {
  level: 100 | 500 | 2500; measure: HeatMeasure; metric: HeatMetric; meas_tech: string;
  cell_deg: [number, number]; from: string; to: string; source: string; includes_coarse: boolean;
  cells: HeatCell[]; suppressed: number; k_min: number | null;
}

export interface DeadZone {
  id: string; operator_key: string | null; tech: "LTE" | "NR";
  centroid_lat: number; centroid_lon: number; cells: number; area_km2: number;
  p50: number | null; oos_pct: number | null; n: number; n_devices?: number;
  severity: "critical" | "warning"; first_seen: string | null; last_seen: string | null;
  polygon: { type: "MultiPolygon"; coordinates: number[][][][] };
}

export interface BreakdownRow {
  key: string | number; label: string; n: number; time_s: number;
  pct_time_by_tier: (number | null)[]; pct_time_unclassified: number | null;
  rsrp: { lte: RsrpStat; nr_sa: RsrpStat; nsa_nr_leg: RsrpStat };
  oos_min: number | null; cell_changes_per_h: number | null; nsa_share: number | null;
  stale_excluded_pct: number | null; n_devices?: number;
}
export interface BreakdownResult { by: string; from: string; to: string; fair: boolean; rows: BreakdownRow[]; suppressed: number }

export type AggTsMetric = "rsrp_p50" | "rsrp_p5" | "pct_time_ge_medium" | "oos_minutes" | "samples";
export interface AggTimeseries {
  metric: AggTsMetric; bucket: "1h" | "1d"; from: string; to: string;
  /** points = [t, value, n] */
  series: { key: string | null; meas_tech: string | null; points: [string, number | null, number][] }[];
  suppressed: number;
}

export interface EnrollmentCode {
  id: number; label: string | null; code_hint: string; group_id: number | null; group: string | null;
  ownership: "ORG" | "PERSONAL"; max_uses: number; use_count: number; expires_at: string | null;
  created_by: string | null; created_at: string | null; revoked_at: string | null;
}
export interface EnrollmentCodeCreated extends EnrollmentCode {
  /** Plain-text code — returned ONLY by the create call, never again */
  code: string; qr_payload: string; qr_svg: string | null; note_he: string;
}

export interface FieldGroup {
  id: number; name: string; description: string | null; is_default: boolean;
  config: Partial<FieldDeviceConfig>; effective_config: FieldDeviceConfig; device_count: number;
  updated_at: string | null; updated_by: string | null; personal_note_he: string;
}

export interface FieldOperator { operator_key: string; name_he: string; cell_sites_operator: string | null; note: string | null }

export interface FieldAuditEntry {
  id: number; action: string; user_id: number | null; username: string | null;
  detail: any; ip_address: string | null; created_at: string | null;
}

export interface TiersMeta {
  measure_he: string;
  tiers: { tier: number; key: string; label_he: string; min_dbm: number | null; token: string }[];
  unclassified: { key: string; label_he: string; token: string };
  status_labels_he: Record<string, string>;
}

export interface RequirementItem { text_he: string; status: string; source: string; note_he?: string }
export interface DeviceRequirements {
  title_he: string; headline_he: string; spec_document: string;
  status_legend_he: Record<string, string>;
  sections: { key: string; title_he: string; items: RequirementItem[] }[];
}

export interface FieldDemoStatus { enabled: boolean; group: string; devices: number }

export const fieldApi = {
  demoStatus: async (): Promise<FieldDemoStatus> => (await api.get("/api/field/demo")).data,
  demoSeed: async (): Promise<{ devices: number; samples: number }> =>
    (await api.post("/api/field/demo/seed", null, { timeout: 180_000 })).data,
  demoRemove: async (): Promise<{ devices: number; samples: number }> =>
    (await api.delete("/api/field/demo", { timeout: 180_000 })).data,
  tiersMeta: async (): Promise<TiersMeta> => (await api.get("/api/field/meta/tiers")).data,
  deviceRequirements: async (): Promise<DeviceRequirements> =>
    (await api.get("/api/field/meta/device-requirements")).data,
  operators: async (): Promise<FieldOperator[]> => (await api.get("/api/field/operators")).data,

  summary: async (p: FieldWindow & { group_id?: number | null; operator?: string | null }): Promise<FieldSummary> =>
    (await api.get("/api/field/summary", { params: qs(p) })).data,

  devices: async (p: { group_id?: number | null; status?: string; ownership?: string; q?: string; sort?: string } = {})
    : Promise<{ rows: FieldDeviceRow[]; server_time: string }> => {
    const { data } = await api.get("/api/field/devices", { params: qs(p) });
    return { rows: rowsToObjects<FieldDeviceRow>(data), server_time: data.server_time };
  },
  live: async (p: { bbox?: string } = {}): Promise<{ rows: FieldLiveRow[]; server_time: string }> => {
    const { data } = await api.get("/api/field/devices/live", { params: qs(p) });
    return { rows: rowsToObjects<FieldLiveRow>(data), server_time: data.server_time };
  },
  device: async (id: string): Promise<FieldDeviceDetail> => (await api.get(`/api/field/devices/${id}`)).data,
  /** Audit-logged on the server (FIELD_TRACK_VIEW). Window ≤ 7 days. */
  track: async (id: string, p: FieldWindow & { max_points?: number }): Promise<DeviceTrack> =>
    (await api.get(`/api/field/devices/${id}/track`, { params: qs(p) })).data,
  deviceTimeseries: async (id: string, p: FieldWindow & { metric: DeviceTsMetric }): Promise<DeviceTimeseries> =>
    (await api.get(`/api/field/devices/${id}/timeseries`, { params: qs(p) })).data,
  updateDevice: async (id: string, body: { label?: string | null; group_id?: number | null; notes?: string | null;
    config_override?: Partial<FieldDeviceConfig> | null }): Promise<FieldDeviceDetail> =>
    (await api.patch(`/api/field/devices/${id}`, body)).data,
  revoke: async (id: string): Promise<{ status: string }> => (await api.post(`/api/field/devices/${id}/revoke`)).data,
  /** ORG devices: anonymise only (server answers 422 org_anonymize_only to mode=delete). */
  remove: async (id: string, mode?: "delete" | "anonymize"): Promise<{ data_action: string; completes_by: string }> =>
    (await api.delete(`/api/field/devices/${id}`, { params: qs({ mode }) })).data,

  issueCommand: async (id: string, type: "sample_now" | "upload_now")
    : Promise<FieldCommand & { warning_he?: string }> =>
    (await api.post(`/api/field/devices/${id}/commands`, { type })).data,
  deviceCommands: async (id: string, limit = 20): Promise<FieldCommand[]> =>
    (await api.get(`/api/field/devices/${id}/commands`, { params: { limit } })).data,
  cancelCommand: async (commandId: string): Promise<FieldCommand> =>
    (await api.delete(`/api/field/commands/${commandId}`)).data,

  heatmap: async (p: FieldWindow & { bbox: string; measure: HeatMeasure; metric?: HeatMetric;
    level?: 100 | 500 | 2500; operator?: string | null }): Promise<HeatmapResult> => {
    const { data } = await api.get("/api/field/heatmap", { params: qs(p) });
    const { fields, rows, ...rest } = data;
    return { ...rest, cells: rowsToObjects<HeatCell>({ fields, rows }) };
  },
  deadZones: async (p: FieldWindow & { operator?: string | null; tech?: "LTE" | "NR" | null; bbox?: string })
    : Promise<DeadZone[]> => (await api.get("/api/field/dead-zones", { params: qs(p) })).data,
  breakdown: async (p: FieldWindow & { by: "operator" | "tech" | "band" | "cell" | "group"; fair?: boolean;
    operator?: string | null; group_id?: number | null }): Promise<BreakdownResult> =>
    (await api.get("/api/field/breakdown", { params: qs(p) })).data,
  timeseriesAgg: async (p: FieldWindow & { metric: AggTsMetric; bucket: "1h" | "1d"; operator?: string | null;
    meas_tech?: "LTE" | "NR" | null; group_id?: number | null }): Promise<AggTimeseries> =>
    (await api.get("/api/field/timeseries", { params: qs(p) })).data,

  alerts: async (p: { status?: string; type?: string; device_id?: string; limit?: number } = {}): Promise<FieldAlert[]> =>
    (await api.get("/api/field/alerts", { params: qs(p) })).data,
  ackAlert: async (id: number, note?: string): Promise<FieldAlert> =>
    (await api.post(`/api/field/alerts/${id}/ack`, note ? { note } : {})).data,
  resolveAlert: async (id: number, note?: string): Promise<FieldAlert> =>
    (await api.post(`/api/field/alerts/${id}/resolve`, note ? { note } : {})).data,

  enrollmentCodes: async (includeExpired = false): Promise<EnrollmentCode[]> =>
    (await api.get("/api/field/enrollment-codes", { params: { include_expired: includeExpired } })).data,
  createEnrollmentCode: async (body: { label?: string | null; group_id?: number | null; ownership: "ORG" | "PERSONAL";
    max_uses: number; ttl_hours: number }): Promise<EnrollmentCodeCreated> =>
    (await api.post("/api/field/enrollment-codes", body)).data,
  revokeEnrollmentCode: async (id: number): Promise<void> => { await api.delete(`/api/field/enrollment-codes/${id}`); },

  groups: async (): Promise<FieldGroup[]> => (await api.get("/api/field/groups")).data,
  createGroup: async (body: { name: string; description?: string | null; config: Partial<FieldDeviceConfig> })
    : Promise<FieldGroup> => (await api.post("/api/field/groups", body)).data,
  updateGroup: async (id: number, body: { name?: string; description?: string | null; config?: Partial<FieldDeviceConfig> })
    : Promise<FieldGroup> => (await api.put(`/api/field/groups/${id}`, body)).data,
  deleteGroup: async (id: number): Promise<void> => { await api.delete(`/api/field/groups/${id}`); },

  audit: async (p: { limit?: number } = {}): Promise<FieldAuditEntry[]> =>
    (await api.get("/api/field/audit", { params: qs(p) })).data,

  /** Device-level export — audit-logged on the server (FIELD_EXPORT). Window ≤ 31 days. */
  exportMeasurementsCsv: (p: FieldWindow & { device_id?: string; group_id?: number | null }): Promise<void> =>
    downloadAuthed(`/api/field/export/measurements.csv?${new URLSearchParams(qs(p))}`, "field_measurements.csv"),
  exportKpisCsv: (p: FieldWindow & { by: string; fair?: boolean; operator?: string | null }): Promise<void> =>
    downloadAuthed(`/api/field/export/kpis.csv?${new URLSearchParams(qs(p))}`, `field_kpis_${p.by}.csv`),
};

export default api;
