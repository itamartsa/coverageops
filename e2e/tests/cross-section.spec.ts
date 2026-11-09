import fs from "node:fs";
import { test, expect } from "../support/fixtures";
import { uid } from "../support/api";
import { clickMapAt, fitBounds, svgPaths } from "../support/map";

const ROUTE = [
  { lat: 31.705, lon: 35.110 },
  { lat: 31.740, lon: 35.170 },
  { lat: 31.780, lon: 35.240 },
];

test.describe("חתך רדיו", () => {
  test("הגדרת ציר בלחיצות על המפה, הרצה, תוצאות, דוח Word וניקוי", async ({ page, loginAs, ui, api }) => {
    const name = `E2E חתך ${uid()}`;
    // Weak, low site → part of the route is below the threshold (dead zones)
    await api.createSite({ name, lat: 31.70, lon: 35.10, frequency: 1800, tx_power: 10, rx_threshold: -90, ant_height: 5 });
    await loginAs("ADMIN");
    await ui.tab("חתך רדיו");

    const run = page.getByRole("button", { name: "▶ הרץ חתך רדיו" });
    await expect(run).toBeDisabled();
    await page.locator("aside select").first().selectOption({ label: `${name} (1800 MHz)` });
    await expect(page.getByText(`✔ ${name}`)).toBeVisible();

    await fitBounds(page, { sw_lat: 31.69, sw_lon: 35.09, ne_lat: 31.79, ne_lon: 35.25 }, 13);
    await page.getByRole("button", { name: "📍 הוסף נקודות מסלול" }).click();
    await expect(page.getByText("הוספת נקודות מסלול - לחץ על המפה")).toBeVisible();
    for (const p of ROUTE) await clickMapAt(page, p);
    await expect(page.getByText(`✔ ${ROUTE.length} נקודות`)).toBeVisible();
    await expect(page.locator("aside [class*='waypointRow']")).toHaveCount(3);
    // numbered waypoint markers + dashed route line on the map
    await expect(page.locator(".leaflet-marker-icon").filter({ hasText: /^3$/ })).toHaveCount(1);

    // "remove last" then re-add
    await page.getByRole("button", { name: "↩ הסר אחרונה" }).click();
    await expect(page.locator("aside [class*='waypointRow']")).toHaveCount(2);
    await clickMapAt(page, ROUTE[2]);
    await expect(page.locator("aside [class*='waypointRow']")).toHaveCount(3);
    await page.getByRole("button", { name: "🛑 סיים הוספת נקודות" }).click();
    await expect(page.getByText("הוספת נקודות מסלול - לחץ על המפה")).toHaveCount(0);

    const pathsBefore = await svgPaths(page).count();
    const req = page.waitForRequest("**/api/coverage/cross-section");
    await run.click();
    const body = (await req).postDataJSON();
    expect(body.waypoints).toHaveLength(3);
    expect(Math.abs(body.waypoints[1].lat - ROUTE[1].lat)).toBeLessThan(0.003);

    // Results: stats, risk, dead zones, distribution chart, recommendations, simulated-terrain warning
    await expect(page.getByText("תוצאות חתך")).toBeVisible();
    const results = page.locator("aside [class*='results']");
    await expect(results.locator("[class*='warnBox']")).toContainText("ללא נתוני טופוגרפיה");
    await expect(results).toContainText(/\d+(\.\d)?% ≥ −90 dBm/);
    await expect(results).toContainText(/קריטי|גבוה|בינוני|נמוך/);
    await expect(results).toContainText(/\d \/ 4/);                         // risk score
    await expect(results.getByText("אזורי מתים", { exact: true })).toBeVisible();
    expect(await results.locator("[class*='deadZone']").count()).toBeGreaterThan(0);
    await expect(results.getByText("התפלגות אות")).toBeVisible();
    expect(await results.locator("[class*='distRow']").count()).toBeGreaterThan(0);
    expect(await results.locator("[class*='_rec_']").count()).toBeGreaterThan(0);
    await ui.expectToast(/חתך הושלם/);
    // Coloured segments drawn on the map
    await expect.poll(() => svgPaths(page).count()).toBeGreaterThan(pathsBefore + 3);

    // Word report download
    const dl = page.waitForEvent("download");
    await page.getByRole("button", { name: "📄 הורד דוח Word (.docx)" }).click();
    const download = await dl;
    expect(download.suggestedFilename()).toBe(`חתך_רדיו_${name}.docx`);
    const file = await download.path();
    expect(await download.failure()).toBeNull();
    const head = fs.readFileSync(file).subarray(0, 2).toString("latin1");
    expect(head).toBe("PK");                                                  // a real .docx (zip)
    await ui.expectToast("הדוח הורד בהצלחה");

    // Clear the route → results and every route/segment path leave the map
    await page.getByRole("button", { name: "✕ נקה מסלול" }).click();
    await expect(page.getByText("תוצאות חתך")).toHaveCount(0);
    await expect(page.locator("aside [class*='waypointRow']")).toHaveCount(0);
    await expect(svgPaths(page)).toHaveCount(0);
    await expect(page.locator(".leaflet-marker-icon").filter({ hasText: /^[123]$/ })).toHaveCount(0);
    await expect(run).toBeDisabled();
  });

  test("הסרת חתך מהמפה מלשונית היסטוריה", async ({ page, loginAs, ui, api }) => {
    const name = `E2E חתך-היסט ${uid()}`;
    const site = await api.createSite({ name, lat: 31.70, lon: 35.10, frequency: 900 });
    await api.analyze(site.id, { sw_lat: 31.69, sw_lon: 35.09, ne_lat: 31.72, ne_lon: 35.13 });   // history not empty
    await loginAs("ADMIN");
    await ui.tab("חתך רדיו");
    await page.locator("aside select").first().selectOption({ label: `${name} (900 MHz)` });
    await fitBounds(page, { sw_lat: 31.69, sw_lon: 35.09, ne_lat: 31.79, ne_lon: 35.25 }, 13);
    await page.getByRole("button", { name: "📍 הוסף נקודות מסלול" }).click();
    await clickMapAt(page, ROUTE[0]);
    await clickMapAt(page, ROUTE[1]);
    await page.getByRole("button", { name: "▶ הרץ חתך רדיו" }).click();
    await expect(page.getByText("תוצאות חתך")).toBeVisible();
    expect(await svgPaths(page).count()).toBeGreaterThan(2);

    await ui.tab("היסטוריה");
    await page.getByRole("button", { name: "✕ הסר חתך רדיו מהמפה" }).click();
    await ui.expectToast("חתך הרדיו הוסר מהמפה");
    await expect(svgPaths(page)).toHaveCount(0);
  });
});
