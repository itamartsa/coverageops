/**
 * Coverage-analysis wizard helpers (ניתוח tab).
 */
import { expect, type Page } from "@playwright/test";
import type { BBox } from "./api";
import { dragRect, fitBounds } from "./map";
import type { Ui } from "./fixtures";

export const JERUSALEM_BBOX: BBox = { sw_lat: 31.750, sw_lon: 35.180, ne_lat: 31.795, ne_lon: 35.245 };

export function siteCheckbox(page: Page, siteName: string) {
  return page.locator("aside label").filter({ hasText: siteName }).locator('input[type="checkbox"]');
}

export function runRow(page: Page, siteName: string) {
  return page.locator("aside [class*='runRow']").filter({ hasText: siteName });
}

export function resultBlock(page: Page, siteName: string) {
  return page.locator("aside [class*='siteResultBlock']").filter({ hasText: siteName });
}

export const runButton = (page: Page) => page.getByRole("button", { name: /▶ הרץ חישוב/ });
export const clearResultsButton = (page: Page) => page.getByRole("button", { name: "✕ נקה תוצאות מהמפה" });
export const resetAreaButton = (page: Page) => page.getByRole("button", { name: "✕ אפס ובחר חדש" });

/** Opens the analysis tab, ticks the sites and drags the area rectangle on the map. */
export async function prepareAnalysis(page: Page, ui: Ui, siteNames: string[], bbox: BBox = JERUSALEM_BBOX) {
  await ui.tab("ניתוח");
  for (const n of siteNames) {
    await siteCheckbox(page, n).check();
  }
  await expect(page.getByText(`✔ ${siteNames.length} נבחרו`)).toBeVisible();

  await fitBounds(page, bbox, 13);
  await page.getByRole("button", { name: "🔲 שרטט מלבן כיסוי חופשי" }).click();
  await expect(page.getByText("שרטוט - לחץ, החזק וגרור לקביעת תא השטח")).toBeVisible();
  await dragRect(page, bbox);
  await expect(page.getByText("✔ הוגדר")).toBeVisible();
  await expect(runButton(page)).toBeEnabled();
}

/** Clicks run and waits until every site row reports done. */
export async function runAndWait(page: Page, siteNames: string[]) {
  await runButton(page).click();
  for (const n of siteNames) {
    await expect(runRow(page, n)).toContainText("✔ הושלם", { timeout: 45_000 });
  }
  await expect(runButton(page)).toBeEnabled({ timeout: 30_000 });
}
