import fs from "node:fs";
import { test, expect } from "../support/fixtures";
import { uid, type Site } from "../support/api";
import { JERUSALEM_BBOX, resultBlock } from "../support/analysis";
import { card } from "../support/form";
import { coverageOverlays } from "../support/map";

test.describe("היסטוריית ניתוחים", () => {
  let name = "";
  let site: Site;
  let resultIds: number[] = [];

  test.beforeEach(async ({ api }) => {
    name = `E2E היסטוריה ${uid()}`;
    site = await api.createSite({ name, lat: 31.772, lon: 35.212, rx_threshold: -105 });
    resultIds = [
      (await api.analyze(site.id, JERUSALEM_BBOX, "DTM")).id,
      (await api.analyze(site.id, JERUSALEM_BBOX, "DSM")).id,
    ];
  });

  test("הרשימה מציגה את הניתוחים עם תגיות ונתונים", async ({ page, loginAs, ui }) => {
    await loginAs("ADMIN");
    await ui.tab("היסטוריה");
    await expect(page.getByText(/היסטוריית ניתוחים \(\d+\)/)).toBeVisible();
    const cards = card(page, name);
    await expect(cards).toHaveCount(2);
    await expect(cards.filter({ hasText: "DSM" })).toHaveCount(1);
    await expect(cards.filter({ hasText: "DTM" })).toHaveCount(1);
    await expect(cards.first()).toContainText("⚠ סימולציה");
    await expect(cards.first()).toContainText(/% ≥ −105 dBm/);
    await expect(cards.first()).toContainText("אות כלשהו");
  });

  test("\"⬆ טען למפה\" מוסיף שכבת כיסוי ופותח את תוצאת הניתוח", async ({ page, loginAs, ui }) => {
    await loginAs("ADMIN");
    await ui.tab("היסטוריה");
    await expect(coverageOverlays(page)).toHaveCount(0);
    await card(page, name).filter({ hasText: "DTM" }).getByRole("button", { name: "⬆ טען למפה" }).click();
    await ui.expectToast(`תוצאה #${resultIds[0]} נטענה למפה`);
    await expect(coverageOverlays(page)).toHaveCount(1);
    await expect(page.getByText("אשף ניתוח כיסוי")).toBeVisible();
    await expect(page.getByText("✔ הוגדר")).toBeVisible();              // stored bbox restored
    await expect(resultBlock(page, name)).toContainText("≥ −105 dBm");
    await expect(resultBlock(page, name).locator("[class*='warnBox']")).toBeVisible();

    // Loading the same result twice keeps one layer/overlay
    await ui.tab("היסטוריה");
    await card(page, name).filter({ hasText: "DTM" }).getByRole("button", { name: "⬆ טען למפה" }).click();
    await ui.tab("🗂 שכבות");
    await expect(page.locator("aside [class*='coverageRow']")).toHaveCount(1);
    await expect(coverageOverlays(page)).toHaveCount(1);
  });

  test("ייצוא דוח: קובץ HTML עם סעיף איכות נתונים", async ({ page, loginAs, ui, context }) => {
    await loginAs("ADMIN");
    await ui.tab("היסטוריה");
    const dl = page.waitForEvent("download");
    await card(page, name).filter({ hasText: "DTM" }).getByRole("button", { name: "📄 ייצא דוח" }).click();
    const download = await dl;
    expect(download.suggestedFilename()).toBe(`coverage_report_${name}_${resultIds[0]}.html`);
    await ui.expectToast("הדוח הורד בהצלחה");
    const html = fs.readFileSync(await download.path(), "utf8");

    // Open the report like a user would and check its sections
    const report = await context.newPage();
    await report.setContent(html);
    await expect(report.getByRole("heading", { name: /דוח ניתוח כיסוי סלולרי/ })).toBeVisible();
    await expect(report.getByRole("heading", { name: /איכות נתונים – Data Quality/ })).toBeVisible();
    await expect(report.getByText(/טרן מדומה \(סימולציה\)/)).toBeVisible();
    await expect(report.getByRole("heading", { name: /סיכום מבצעי/ })).toBeVisible();
    await expect(report.getByText(name).first()).toBeVisible();
    await expect(report.locator("body")).toContainText("≥ −105 dBm");
    await report.close();
  });

  test("הורדת GeoJSON", async ({ page, loginAs, ui }) => {
    await loginAs("ADMIN");
    await ui.tab("היסטוריה");
    const dl = page.waitForEvent("download");
    await card(page, name).filter({ hasText: "DSM" }).getByRole("button", { name: "⬇ GeoJSON" }).click();
    const download = await dl;
    expect(download.suggestedFilename()).toMatch(/\.geojson$/);
    const gj = JSON.parse(fs.readFileSync(await download.path(), "utf8"));
    expect(gj.type).toBe("FeatureCollection");
    expect(gj.features.length).toBeGreaterThan(0);
    await ui.expectToast("קובץ GeoJSON הורד");
  });

  test("מחיקת תוצאה מסירה אותה מהרשימה — וגם מהמפה אם נטענה", async ({ page, loginAs, ui, api }) => {
    await loginAs("ADMIN");
    await ui.tab("היסטוריה");
    const dtm = card(page, name).filter({ hasText: "DTM" });
    await dtm.getByRole("button", { name: "⬆ טען למפה" }).click();
    await expect(coverageOverlays(page)).toHaveCount(1);

    await ui.tab("היסטוריה");
    await card(page, name).filter({ hasText: "DTM" }).getByTitle("מחק תוצאה").click();
    await ui.expectToast("תוצאה נמחקה");
    await expect(card(page, name)).toHaveCount(1);
    expect((await api.coverageHistory()).some((h) => h.id === resultIds[0])).toBe(false);

    // The deleted result's overlay + layer are gone too (no dead layer pointing at a deleted result)
    await expect(coverageOverlays(page)).toHaveCount(0);
    await ui.tab("🗂 שכבות");
    await expect(page.locator("aside [class*='coverageRow']")).toHaveCount(0);
  });
});
