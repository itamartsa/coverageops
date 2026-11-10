/**
 * Field-reception (/field) helpers. The backend is seeded with the synthetic demo fleet
 * (`python -m app.scripts.field_simulator --seed-demo`, see scripts/start-backend.cjs):
 * ~23 devices labelled "דמו · …", 8 h of history ending at server start, open alerts.
 */
import { expect, type Page } from "@playwright/test";
import type { Api } from "./api";

export type FieldTab = "overview" | "live" | "devices" | "analysis" | "predict" | "alerts" | "admin";

/** Open /field (optionally on a tab) and wait for the header + first data. */
export async function openField(page: Page, tab?: FieldTab, extra = "") {
  await page.goto(`/field${tab ? `?tab=${tab}` : ""}${extra}`);
  await expect(page.getByRole("heading", { level: 1, name: /קליטה בשטח/ })).toBeVisible();
  await expect(page.getByTestId("freshness")).toContainText("עודכן");
}

export function fieldTab(page: Page, label: string) {
  return page.getByRole("tab", { name: new RegExp(`^${label}`) });
}

export interface LiveDevice { id: string; label: string; status: string }

/** Device rows of GET /api/field/devices as objects. */
export async function fieldDevices(api: Api): Promise<Array<Record<string, any>>> {
  const res = await api.get("/api/field/devices");
  expect(res.ok()).toBeTruthy();
  const data = await res.json();
  return data.rows.map((r: unknown[]) => Object.fromEntries(data.fields.map((k: string, i: number) => [k, r[i]])));
}

export async function deviceByLabel(api: Api, label: string): Promise<Record<string, any>> {
  const d = (await fieldDevices(api)).find((x) => x.label === label);
  expect(d, `demo device "${label}" (is the demo fleet seeded?)`).toBeTruthy();
  return d!;
}

/** FIELD_* audit entries (ADMIN). */
export async function fieldAudit(api: Api, action: string): Promise<Array<Record<string, any>>> {
  const res = await api.get(`/api/field/audit?action=${action}`);
  expect(res.ok()).toBeTruthy();
  return res.json();
}

/** True when the document has no horizontal page scroll. */
export async function noHorizontalScroll(page: Page): Promise<boolean> {
  return page.evaluate(() => document.scrollingElement!.scrollWidth <= window.innerWidth);
}
