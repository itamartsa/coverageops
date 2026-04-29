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
  ant_height: number;
  frequency: number;
  tx_power: number;
  rx_threshold: number;
  max_radius: number;
  notes?: string;
}

export interface Site extends SiteCreate {
  id: number;
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

export interface CoverageResult {
  id:          number;
  site_id:     number;
  mode:        string;
  covered_pct: number;
  rssi_avg:    number;
  rssi_max:    number;
  rssi_min:    number;
  duration_sec: number;
  created_at:  string;
  geojson:     GeoJSON.FeatureCollection;
  // Bounding box — used to position the PNG image overlay on the map
  sw_lat?: number; sw_lon?: number;
  ne_lat?: number; ne_lon?: number;
}

export interface HistoryItem {
  id:          number;
  site_id:     number;
  site_name:   string;
  mode:        string;
  covered_pct: number;
  rssi_avg:    number;
  rssi_max:    number;
  rssi_min:    number;
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
    score: number;
  };
  dead_zones_clusters?: Array<{
    centroid_lat: number; centroid_lon: number;
    cell_count: number; severity: string; rssi_avg: number;
  }>;
  recommendations?: string[];
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
}

export interface CrossSectionRequest {
  site_id:    number;
  mode:       "DTM" | "DSM";
  waypoints:  { lat: number; lon: number }[];
  resolution?: number;
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

  resultPng: async (
    id: number,
    bbox: { sw_lat: number; sw_lon: number; ne_lat: number; ne_lon: number },
  ): Promise<string> => {
    const p = new URLSearchParams({
      sw_lat: bbox.sw_lat.toString(), sw_lon: bbox.sw_lon.toString(),
      ne_lat: bbox.ne_lat.toString(), ne_lon: bbox.ne_lon.toString(),
      width: "900", height: "900",
    });
    const res = await api.get(`/api/coverage/result/${id}/png?${p}`, {
      responseType: "blob",
    });
    return URL.createObjectURL(res.data);
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

export default api;
