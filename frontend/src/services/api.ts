/**
 * CoverageOps – API Service
 * Centralizes all backend communication.
 */
import axios from "axios";

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
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem("token");
      window.location.href = "/login";
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

export interface Site extends SiteCreate {
  id: number;
  ant_height: number;
  /** True while ant_height is the system default and was never set by a user. */
  ant_height_is_default: boolean;
  elevation_m: number;   // auto-fetched by backend from terrain tiles
  owner_id: number;
  created_at: string;
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
    let name = filename;
    if (!name) {
      const cd: string | undefined = res.headers?.["content-disposition"];
      const m = cd?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
      name = m ? decodeURIComponent(m[1]) : `coverage_${id}.geojson`;
    }
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
export interface CellSiteRow {
  id: number; lat: number; lon: number;
  operator: string; status: "active" | "construction";
  site_type: string; techs: string;
}

export interface CellSiteDetail {
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
}

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
};

// ── OSM cellular towers (Overpass, proxied + cached by the backend) ──────────
export interface OsmTower {
  id: number;
  lat: number;
  lon: number;
  operator: string | null;
  height: string | null;
  tags: Record<string, string>;
}

export interface OsmTowersResponse {
  elements: OsmTower[];
  count: number;
  source: string;
  cached: boolean;
  bbox: { south: number; west: number; north: number; east: number };
}

export const osmTowersApi = {
  /** bbox ≤ 1.5° per side; server answers 400 (too large) / 503 (Overpass busy) with a Hebrew `detail`. */
  list: async (
    bbox: { south: number; west: number; north: number; east: number },
    signal?: AbortSignal,
  ): Promise<OsmTowersResponse> =>
    (await api.get("/api/cell-sites/osm", { params: bbox, signal, timeout: 150000 })).data,
};

export default api;
