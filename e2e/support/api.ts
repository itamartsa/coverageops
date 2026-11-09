/**
 * Thin REST client for seeding / inspecting data quickly (bypasses the UI).
 */
import { request, type APIRequestContext, type APIResponse, expect } from "@playwright/test";

// eslint-disable-next-line @typescript-eslint/no-var-requires
const env = require("../scripts/env.cjs") as typeof import("../scripts/env.cjs");

export const API_URL: string = env.API_URL;

export type Role = "ADMIN" | "OPERATOR" | "VIEWER";

export const USERS: Record<Role, { username: string; password: string; full_name: string }> = {
  ADMIN:    { username: "adminuser", password: "12345678",  full_name: "מנהל מערכת" },
  OPERATOR: { username: "operator1", password: "Ops1234!",  full_name: "קצין קשר א" },
  VIEWER:   { username: "viewer1",   password: "View1234!", full_name: "צופה א" },
};

export interface LoginData {
  access_token: string;
  user_id: number;
  username: string;
  full_name: string;
  role: Role;
}

export interface SiteIn {
  name: string;
  lat: number;
  lon: number;
  ant_height?: number;
  frequency?: number;
  tx_power?: number;
  rx_threshold?: number;
  max_radius?: number;
  notes?: string;
}

export interface Site extends Required<Omit<SiteIn, "notes" | "ant_height">> {
  id: number;
  ant_height: number;
  params_source?: string;
  cell_site_id?: number | null;
}

export interface BBox { sw_lat: number; sw_lon: number; ne_lat: number; ne_lon: number }

export interface CellSiteRow {
  id: number; lat: number; lon: number; operator: string; status: string;
  site_type: string; techs: string; ant_height_m: number | null; height_source: string;
  bands?: number[] | null;
}

/** Unique, readable suffix for names created by a test. */
export function uid(): string {
  return `${Date.now().toString(36).slice(-5)}${Math.floor(Math.random() * 1296).toString(36)}`;
}

/** POST /api/auth/login (form-encoded, OAuth2 password grant). */
export async function apiLogin(ctx: APIRequestContext, username: string, password: string): Promise<LoginData> {
  const res = await ctx.post(`${API_URL}/api/auth/login`, { form: { username, password } });
  expect(res.status(), `login ${username}: ${await res.text()}`).toBe(200);
  return res.json();
}

export class Api {
  private constructor(readonly ctx: APIRequestContext, readonly auth: LoginData) {}

  static async as(role: Role): Promise<Api> {
    const ctx = await request.newContext();
    const u = USERS[role];
    const auth = await apiLogin(ctx, u.username, u.password);
    return new Api(ctx, auth);
  }

  async dispose() { await this.ctx.dispose(); }

  private headers() { return { Authorization: `Bearer ${this.auth.access_token}` }; }

  private async ok(res: APIResponse, what: string) {
    expect(res.ok(), `${what} → ${res.status()} ${await res.text()}`).toBeTruthy();
    return res.status() === 204 ? null : res.json();
  }

  get(path: string) { return this.ctx.get(`${API_URL}${path}`, { headers: this.headers() }); }
  post(path: string, data?: unknown) { return this.ctx.post(`${API_URL}${path}`, { headers: this.headers(), data }); }
  put(path: string, data?: unknown) { return this.ctx.put(`${API_URL}${path}`, { headers: this.headers(), data }); }
  patch(path: string, data?: unknown) { return this.ctx.patch(`${API_URL}${path}`, { headers: this.headers(), data }); }
  delete(path: string) { return this.ctx.delete(`${API_URL}${path}`, { headers: this.headers() }); }

  async createSite(s: SiteIn): Promise<Site> {
    return this.ok(await this.post("/api/sites/", {
      frequency: 900, tx_power: 43, rx_threshold: -105, max_radius: 50, ant_height: 20, ...s,
    }), "create site");
  }

  async listSites(): Promise<Site[]> { return this.ok(await this.get("/api/sites/"), "list sites"); }

  async deleteSite(id: number) { await this.delete(`/api/sites/${id}`); }

  async analyze(siteId: number, bbox: BBox, mode: "DTM" | "DSM" = "DTM") {
    return this.ok(await this.post("/api/coverage/analyze", { site_id: siteId, mode, ...bbox }), "analyze");
  }

  async coverageHistory(): Promise<Array<{ id: number; site_id: number; site_name: string }>> {
    return this.ok(await this.get("/api/coverage/"), "coverage history");
  }

  async runLink(siteId: number, rx: { lat: number; lon: number }) {
    return this.ok(await this.post("/api/link/", {
      transmitter: { kind: "site", id: siteId }, receiver: rx, mode: "DTM", save: true,
    }), "link");
  }

  async cellSites(): Promise<CellSiteRow[]> {
    const data = await this.ok(await this.get("/api/cell-sites/"), "cell sites");
    const f: string[] = data.fields;
    return data.rows.map((r: unknown[]) => Object.fromEntries(f.map((k, i) => [k, r[i]])) as unknown as CellSiteRow);
  }

  async cellSite(id: number) { return this.ok(await this.get(`/api/cell-sites/${id}`), "cell site"); }

  async resetCellSite(id: number) {
    await this.delete(`/api/cell-sites/${id}/height`);
    await this.delete(`/api/cell-sites/${id}/rf`);
  }

  async users(): Promise<Array<{ id: number; username: string; role: Role }>> {
    return this.ok(await this.get("/api/users/"), "users");
  }

  async createUser(u: { username: string; full_name: string; password: string; role: Role }) {
    return this.ok(await this.post("/api/users/", u), "create user");
  }

  async deviceProfiles(): Promise<Array<{ id: number; key: string; name_he: string; is_default: boolean }>> {
    return this.ok(await this.get("/api/device-profiles/"), "device profiles");
  }
}
