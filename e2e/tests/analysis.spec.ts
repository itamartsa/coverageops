import { test, expect } from "../support/fixtures";
import { uid, type BBox } from "../support/api";
import {
  JERUSALEM_BBOX, clearResultsButton, prepareAnalysis, resetAreaButton, resultBlock, runAndWait,
  runButton, runRow,
} from "../support/analysis";
import { coverageOverlays, svgPaths } from "../support/map";

const near = (a: number, b: number, tol = 0.004) => Math.abs(a - b) < tol;

test.describe("אשף ניתוח כיסוי", () => {
  test("ניתוח אתר יחיד: שרטוט תא שטח, התקדמות, כרטיס תוצאה ושכבה במפה", async ({ page, loginAs, ui, api }) => {
    const name = `E2E כיסוי ${uid()}`;
    await api.createSite({ name, lat: 31.772, lon: 35.212, rx_threshold: -105 });
    await loginAs("ADMIN");
    await prepareAnalysis(page, ui, [name]);

    // Hold the analyze response briefly so the per-site "computing" state is observable
    let body: BBox & { site_id: number; mode: string } | null = null;
    await page.route("**/api/coverage/analyze", async (route) => {
      body = route.request().postDataJSON();
      await new Promise((r) => setTimeout(r, 1200));
      await route.continue();
    });

    await runButton(page).click();
    await expect(runRow(page, name)).toContainText("⏳ מחשב...");
    await expect(page.getByText("מחשב כיסוי...", { exact: true })).toBeVisible();         // map overlay spinner
    await expect(runRow(page, name)).toContainText("✔ הושלם", { timeout: 45_000 });
    await expect(runRow(page, name)).toContainText(/\d+\.\d%/);

    // The request used the rectangle that was dragged on the map
    expect(body).not.toBeNull();
    const b = body!;
    expect(b.mode).toBe("DTM");
    expect(near(b.sw_lat, JERUSALEM_BBOX.sw_lat) && near(b.ne_lat, JERUSALEM_BBOX.ne_lat)).toBe(true);
    expect(near(b.sw_lon, JERUSALEM_BBOX.sw_lon) && near(b.ne_lon, JERUSALEM_BBOX.ne_lon)).toBe(true);

    // Result card
    const card = resultBlock(page, name);
    await expect(card).toBeVisible();
    await expect(card.locator("[class*='coveredValue']")).toHaveText(/^\d+(\.\d)?% ≥ −105 dBm$/);
    await expect(card).toContainText("סימולציה");                           // DEM badge
    const warn = card.locator("[class*='warnBox']");
    await expect(warn).toBeVisible();
    await expect(warn).toContainText("ללא נתוני טופוגרפיה");                 // simulated-terrain warning
    await expect(card).toContainText("סף: −105 dBm");
    await ui.expectToast(/ניתוח הושלם – 1\/1 אתרים/);

    // Coverage overlay on the map
    await expect(coverageOverlays(page)).toHaveCount(1);
  });

  test("ניתוח שני אתרים יוצר שתי שכבות כיסוי", async ({ page, loginAs, ui, api }) => {
    const a = `E2E רב-א ${uid()}`, b = `E2E רב-ב ${uid()}`;
    await api.createSite({ name: a, lat: 31.772, lon: 35.212 });
    await api.createSite({ name: b, lat: 31.760, lon: 35.230, frequency: 1800 });
    await loginAs("ADMIN");
    await prepareAnalysis(page, ui, [a, b]);
    await expect(runButton(page)).toHaveText("▶ הרץ חישוב (2 אתרים)");
    await runAndWait(page, [a, b]);

    await expect(resultBlock(page, a)).toBeVisible();
    await expect(resultBlock(page, b)).toBeVisible();
    await expect(coverageOverlays(page)).toHaveCount(2);
    await ui.expectToast(/2\/2 אתרים/);

    await ui.tab("🗂 שכבות");
    await expect(page.locator("aside [class*='coverageRow']")).toHaveCount(2);
    await expect(page.locator("aside [class*='coverageRow']").filter({ hasText: a })).toContainText("⚠ סימולציה");
  });

  test("רגרסיה: \"נקה תוצאות מהמפה\" מסיר את כל שכבות הכיסוי — גם אחרי רענון", async ({ page, loginAs, ui, api }) => {
    const a = `E2E ניקוי-א ${uid()}`, b = `E2E ניקוי-ב ${uid()}`;
    await api.createSite({ name: a, lat: 31.772, lon: 35.212 });
    await api.createSite({ name: b, lat: 31.760, lon: 35.230 });
    await loginAs("ADMIN");
    await prepareAnalysis(page, ui, [a, b]);
    await runAndWait(page, [a, b]);
    await expect(coverageOverlays(page)).toHaveCount(2);

    await clearResultsButton(page).click();
    await ui.expectToast("תוצאות נוקו מהמפה");
    await expect(coverageOverlays(page)).toHaveCount(0);
    await expect(resultBlock(page, a)).toHaveCount(0);
    await expect(runRow(page, a)).toHaveCount(0);

    await ui.tab("🗂 שכבות");
    await expect(page.locator("aside [class*='coverageRow']")).toHaveCount(0);
    await expect(page.getByText("הרץ ניתוח כיסוי — כל ניתוח יישמר כאן")).toBeVisible();

    // After a reload the overlays must not come back from persisted state
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.locator(".leaflet-container")).toBeVisible();
    await page.waitForTimeout(1000);
    await expect(coverageOverlays(page)).toHaveCount(0);
    await ui.tab("🗂 שכבות");
    await expect(page.locator("aside [class*='coverageRow']")).toHaveCount(0);
  });

  test("רגרסיה: ניקוי מיד אחרי הרצה (לפני שתמונות הכיסוי נטענו) לא משאיר שכבות יתומות", async ({ page, loginAs, ui, api }) => {
    const a = `E2E מהיר-א ${uid()}`, b = `E2E מהיר-ב ${uid()}`;
    await api.createSite({ name: a, lat: 31.772, lon: 35.212 });
    await api.createSite({ name: b, lat: 31.760, lon: 35.230 });
    await loginAs("ADMIN");
    await prepareAnalysis(page, ui, [a, b]);

    // Slow PNG download → "clear" happens while the overlay images are still in flight
    let pngRequests = 0;
    await page.route("**/api/coverage/result/*/png*", async (route) => {
      pngRequests++;
      await new Promise((r) => setTimeout(r, 1500));
      await route.continue().catch(() => {});
    });

    await runButton(page).click();
    await expect(clearResultsButton(page)).toBeVisible({ timeout: 45_000 });
    await clearResultsButton(page).click();
    // Let the delayed PNGs arrive — they must not be drawn
    await expect.poll(() => pngRequests).toBeGreaterThanOrEqual(2);
    await page.waitForTimeout(2500);
    await expect(coverageOverlays(page)).toHaveCount(0);

    // Run again + clear again (rapid repeat)
    await runButton(page).click();
    await expect(clearResultsButton(page)).toBeVisible({ timeout: 45_000 });
    await expect(coverageOverlays(page)).toHaveCount(2, { timeout: 15_000 });
    await clearResultsButton(page).click();
    await expect(coverageOverlays(page)).toHaveCount(0);

    await ui.tab("🗂 שכבות");
    await expect(page.locator("aside [class*='coverageRow']")).toHaveCount(0);

    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);
    await expect(coverageOverlays(page)).toHaveCount(0);
  });

  test("רגרסיה: \"אפס ובחר חדש\" מסיר את כל שכבות הכיסוי ואת תא השטח", async ({ page, loginAs, ui, api }) => {
    const a = `E2E איפוס-א ${uid()}`, b = `E2E איפוס-ב ${uid()}`;
    await api.createSite({ name: a, lat: 31.772, lon: 35.212 });
    await api.createSite({ name: b, lat: 31.760, lon: 35.230 });
    await loginAs("ADMIN");
    await prepareAnalysis(page, ui, [a, b]);
    await runAndWait(page, [a, b]);
    await expect(coverageOverlays(page)).toHaveCount(2);
    const pathsWithArea = await svgPaths(page).count();

    await resetAreaButton(page).click();
    await expect(coverageOverlays(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "🔲 שרטט מלבן כיסוי חופשי" })).toBeVisible();
    await expect(runButton(page)).toBeDisabled();
    await expect(runRow(page, a)).toHaveCount(0);
    // Area rectangle (+ LOS lines to its centre) removed from the map
    await expect.poll(() => svgPaths(page).count()).toBeLessThan(pathsWithArea);
    await expect(page.locator('div[title="גרור להזזת תא השטח"]')).toHaveCount(0);

    await ui.tab("🗂 שכבות");
    await expect(page.locator("aside [class*='coverageRow']")).toHaveCount(0);

    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(1000);
    await expect(coverageOverlays(page)).toHaveCount(0);
  });

  test("מצב DSM נשלח לשרת ומוצג בתוצאה", async ({ page, loginAs, ui, api }) => {
    const name = `E2E DSM ${uid()}`;
    await api.createSite({ name, lat: 31.772, lon: 35.212 });
    await loginAs("ADMIN");
    await prepareAnalysis(page, ui, [name]);
    await page.getByRole("button", { name: /תכסית/ }).click();
    const req = page.waitForRequest("**/api/coverage/analyze");
    await runAndWait(page, [name]);
    expect((await req).postDataJSON().mode).toBe("DSM");
    await expect(resultBlock(page, name)).toContainText("מודל DSM");
  });
});
