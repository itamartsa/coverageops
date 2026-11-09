import { test, expect } from "../support/fixtures";
import { uid } from "../support/api";
import { JERUSALEM_BBOX } from "../support/analysis";
import { loadFromHistory } from "../support/history";
import { cellSiteMarkerCount, coverageOverlays, demOverlays, setView } from "../support/map";

const coverageRow = (page: import("@playwright/test").Page, text: string | RegExp) =>
  page.locator("aside [class*='coverageRow']").filter({ hasText: text });

const systemRow = (page: import("@playwright/test").Page, name: string) =>
  page.locator("aside [class*='layerRow']").filter({ hasText: name });

const shownCount = async (page: import("@playwright/test").Page) => {
  const t = await page.locator("[class*='toast']").filter({ hasText: "מוצגים" }).last().innerText();
  return Number(t.replace(/[^\d]/g, ""));
};

test.describe("לשונית שכבות", () => {
  test.describe("שכבות כיסוי", () => {
    let siteName = "";
    test.beforeEach(async ({ api, page, loginAs, ui }) => {
      siteName = `E2E שכבה ${uid()}`;
      const site = await api.createSite({ name: siteName, lat: 31.772, lon: 35.212 });
      await api.analyze(site.id, JERUSALEM_BBOX);
      await loginAs("ADMIN");
      await loadFromHistory(page, ui, siteName);
      await expect(coverageOverlays(page)).toHaveCount(1);
      await ui.tab("🗂 שכבות");
      await expect(coverageRow(page, siteName)).toBeVisible();
    });

    test("הסתרה והצגה של שכבת כיסוי", async ({ page }) => {
      const row = coverageRow(page, siteName);
      await row.getByTitle("הסתר כיסוי").click();
      await expect(coverageOverlays(page)).toHaveCount(0);
      await expect(row.getByTitle("הצג כיסוי")).toBeVisible();
      await row.getByTitle("הצג כיסוי").click();
      await expect(coverageOverlays(page)).toHaveCount(1);
    });

    test("שינוי שם שכבה נשמר גם אחרי רענון", async ({ page, ui }) => {
      const row = coverageRow(page, siteName);
      await row.getByTitle("לחץ לשינוי שם").click();
      const input = page.locator("aside input[class*='renameInput']");
      const newName = `E2E שם חדש ${uid()}`;
      await input.fill(newName);
      await input.press("Enter");
      await expect(coverageRow(page, newName)).toBeVisible();
      await expect(coverageRow(page, siteName)).toHaveCount(0);

      // Escape cancels a rename
      await coverageRow(page, newName).getByTitle("לחץ לשינוי שם").click();
      await input.fill("לא יישמר");
      await input.press("Escape");
      await expect(coverageRow(page, newName)).toBeVisible();

      await page.reload();
      await ui.tab("🗂 שכבות");
      await expect(coverageRow(page, newName)).toBeVisible();
      await expect(coverageOverlays(page)).toHaveCount(1);
    });

    test("מחיקת שכבה מסירה אותה מהרשימה ומהמפה", async ({ page }) => {
      await coverageRow(page, siteName).getByTitle("מחק שכבה").click();
      await expect(coverageRow(page, siteName)).toHaveCount(0);
      await expect(coverageOverlays(page)).toHaveCount(0);
      await expect(page.getByText("הרץ ניתוח כיסוי — כל ניתוח יישמר כאן")).toBeVisible();
    });
  });

  test("שכבת גבהים (DEM) — הפעלה וכיבוי ללא קריסה כשאין נתוני גובה", async ({ page, loginAs, ui }) => {
    await loginAs("OPERATOR");
    await setView(page, 31.77, 35.21, 11);
    await ui.tab("🗂 שכבות");
    const row = systemRow(page, "גבהים (DEM)");

    const topo = page.waitForResponse((r) => r.url().includes("/api/coverage/topo-overlay"));
    await row.getByTitle("הצג שכבה").click();
    const res = await topo;
    expect(res.status()).toBeLessThan(500);
    await expect(row.getByTitle("הסתר שכבה")).toBeVisible();
    if (res.ok()) await expect(demOverlays(page)).toHaveCount(1);

    // Panning re-requests the overlay for the new viewport
    const again = page.waitForResponse((r) => r.url().includes("/api/coverage/topo-overlay"));
    await setView(page, 31.70, 35.10, 11);
    expect((await again).status()).toBeLessThan(500);

    await row.getByTitle("הסתר שכבה").click();
    await expect(demOverlays(page)).toHaveCount(0);
    await expect(page.locator(".leaflet-container")).toBeVisible();
  });

  test("שכבת אתרי סלולר: סמנים במפה, סינון מפעילים ואתרים בהקמה", async ({ page, loginAs, ui }) => {
    await loginAs("OPERATOR");
    await ui.tab("🗂 שכבות");
    const row = systemRow(page, "אתרי סלולר – מאגר ממשלתי");
    await row.getByTitle("הצג שכבה").click();
    await ui.expectToast(/מוצגים [\d,]+ אתרי סלולר/);
    const all = await shownCount(page);
    expect(all).toBeGreaterThan(8000);
    await expect.poll(() => cellSiteMarkerCount(page)).toBe(all);

    // Info panel opens automatically with the stats + operator chips
    await expect(page.locator("aside [class*='_stat_']").filter({ hasText: "אתרים פעילים" })).toBeVisible();
    const phi = page.locator("aside button[class*='chip']").filter({ hasText: /^PHI/ });
    await phi.click();
    await expect.poll(() => cellSiteMarkerCount(page)).toBeLessThan(all);
    const withoutPhi = await cellSiteMarkerCount(page);
    expect(withoutPhi).toBeGreaterThan(0);

    // Hide sites under construction
    await page.getByLabel("הצג אתרים בהקמה").uncheck();
    await expect.poll(() => cellSiteMarkerCount(page)).toBeLessThan(withoutPhi);

    // Restore
    await phi.click();
    await page.getByLabel("הצג אתרים בהקמה").check();
    await expect.poll(() => cellSiteMarkerCount(page)).toBe(all);

    // Hide the layer
    await row.getByTitle("הסתר שכבה").click();
    await expect.poll(() => cellSiteMarkerCount(page)).toBe(0);
  });

  test("פאנל המידע ⓘ של שכבת אתרי הסלולר — גובה ו-RF", async ({ page, loginAs, ui }) => {
    await loginAs("ADMIN");
    await ui.tab("🗂 שכבות");
    const row = systemRow(page, "אתרי סלולר – מאגר ממשלתי");
    const info = row.getByTitle("מידע על הנתונים וסינון");
    await expect(page.getByText("על הנתונים")).toHaveCount(0);
    await info.click();
    await expect(page.getByText("על הנתונים")).toBeVisible();
    await expect(page.getByText("בהקמה").first()).toBeVisible();

    await page.getByRole("button", { name: "▼ גובה אנטנות (משוער)" }).click();
    await expect(page.getByText(/ברירת מחדל לפי סוג אתר/)).toBeVisible();
    await expect(page.getByText("הוזנו ידנית").first()).toBeVisible();
    // ADMIN can click a default value to edit it
    await expect(page.locator("aside button[title='ערוך ברירת מחדל']").first()).toBeVisible();

    await page.getByRole("button", { name: "▼ פרמטרי RF (משוערים)" }).click();
    await expect(page.getByText(/הספק \(EIRP\) לפי סוג אתר/)).toBeVisible();
    await expect(page.getByText(/תדר מייצג לפי דור/)).toBeVisible();
    await expect(page.getByText("4G", { exact: true }).first()).toBeVisible();

    // ⓘ closes the panel again
    await info.click();
    await expect(page.getByText("על הנתונים")).toHaveCount(0);
  });
});
