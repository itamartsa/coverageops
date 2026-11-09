import { test, expect } from "../support/fixtures";
import { Api, type CellSiteRow } from "../support/api";
import { isolatedCellSites, openCellSitePopup, showCellSitesLayer } from "../support/cellsites";
import { siteCheckbox } from "../support/analysis";

const multiBand = (c: CellSiteRow) => (c.techs ?? "").split(",").filter(Boolean).length >= 2;

test.describe("חלון אתר סלולרי", () => {
  let sites: CellSiteRow[] = [];

  test.beforeAll(async () => {
    const api = await Api.as("ADMIN");
    sites = await isolatedCellSites(api, 6, multiBand);
    for (const s of sites) await api.resetCellSite(s.id);
    await api.dispose();
  });

  test.afterEach(async ({ api }) => {
    for (const s of sites) await api.resetCellSite(s.id);
  });

  test("פרטי האתר וגובה משוער", async ({ page, loginAs, ui, api }) => {
    const s = sites[0];
    const detail = await api.cellSite(s.id);
    await loginAs("OPERATOR");
    await showCellSitesLayer(page, ui);
    const popup = await openCellSitePopup(page, s);
    await expect(popup).toContainText("📡 אתר סלולר פעיל");
    await expect(popup).toContainText(detail.site_type_he);
    await expect(popup).toContainText("גובה אנטנה:");
    await expect(popup).toContainText("⚠ משוער לפי סוג האתר");
    await expect(popup).toContainText(`${detail.ant_height_m} מ'`);
    await expect(popup.getByRole("button", { name: "ערוך גובה" })).toBeVisible();
    await expect(popup.getByRole("button", { name: "ערוך RF" })).toBeVisible();
  });

  test("עריכת גובה אנטנה: שמירה ← \"הוזן ידנית\", חזרה להערכה ← \"משוער\"", async ({ page, loginAs, ui, api }) => {
    const s = sites[1];
    await loginAs("OPERATOR");
    await showCellSitesLayer(page, ui);
    let popup = await openCellSitePopup(page, s);

    await popup.getByRole("button", { name: "ערוך גובה" }).click();
    const h = popup.locator('[data-cs-field="h"]');
    await expect(h).toBeFocused();

    // Out of range → inline error, nothing saved
    await h.fill("500");
    await popup.getByRole("button", { name: "שמור" }).click();
    await expect(popup.locator('[data-cs-field="err"]')).toHaveText("גובה חייב להיות בין 0.5 ל-300 מטר");

    await h.fill("32.5");
    await popup.locator('[data-cs-field="note"]').fill("מדידת שטח E2E");
    await h.press("Enter");
    await ui.expectToast("גובה האנטנה נשמר: 32.5 מ'");
    popup = page.locator(`.leaflet-popup-content [data-cellsite-id="${s.id}"]`);
    await expect(popup).toContainText("✓ הוזן ידנית");
    await expect(popup).toContainText("32.5 מ'");
    await expect(popup).toContainText("מדידת שטח E2E");
    await expect(popup).not.toContainText("⚠ משוער לפי סוג האתר");
    expect((await api.cellSite(s.id)).height_source).toBe("manual");

    // Survives closing / reopening the popup (fresh fetch)
    await page.locator(".leaflet-popup-close-button").click();
    await expect(popup).toHaveCount(0);
    popup = await openCellSitePopup(page, s);
    await expect(popup).toContainText("✓ הוזן ידנית");

    // Reset to the estimate
    await popup.getByRole("button", { name: "ערוך גובה" }).click();
    await popup.getByRole("button", { name: "חזור להערכה" }).click();
    await ui.expectToast("הגובה הוחזר להערכה");
    await expect(popup).toContainText("⚠ משוער לפי סוג האתר");
    expect((await api.cellSite(s.id)).height_source).toBe("estimated");
  });

  test("עריכת פרמטרי RF ושחזור להערכה", async ({ page, loginAs, ui, api }) => {
    const s = sites[2];
    await loginAs("OPERATOR");
    await showCellSitesLayer(page, ui);
    const popup = await openCellSitePopup(page, s);

    await popup.getByRole("button", { name: "ערוך RF" }).click();
    await popup.locator('[data-cs-field="tx"]').fill("80");
    await popup.getByRole("button", { name: "שמור" }).click();
    await expect(popup.locator('[data-cs-field="err"]')).toHaveText("הספק חייב להיות בין 0 ל-70 dBm");

    await popup.locator('[data-cs-field="tx"]').fill("51");
    await popup.locator('[data-cs-field="freq-custom"]').fill("2600");
    await popup.locator('[data-cs-field="rfnote"]').fill("נתוני מפעיל");
    await popup.getByRole("button", { name: "שמור" }).click();
    await ui.expectToast("פרמטרי RF נשמרו: 2600 MHz · 51 dBm");
    await expect(popup).toContainText("2600 MHz");
    await expect(popup).toContainText("51 dBm");
    await expect(popup).toContainText("נתוני מפעיל");
    const d = await api.cellSite(s.id);
    expect(d.frequency_mhz).toBe(2600);
    expect(d.tx_power_dbm).toBe(51);

    await popup.getByRole("button", { name: "ערוך RF" }).click();
    await popup.getByRole("button", { name: "חזור להערכה" }).click();
    await ui.expectToast("פרמטרי RF הוחזרו להערכה");
    await expect(popup).not.toContainText("נתוני מפעיל");
    expect((await api.cellSite(s.id)).tx_power_dbm).not.toBe(51);
  });

  test("\"📡 נתח כיסוי מאתר זה\" יוצר אתר ניתוח ופותח את אשף הניתוח עם האתר מסומן", async ({ page, loginAs, ui }) => {
    const s = sites[3];
    await loginAs("OPERATOR");
    await showCellSitesLayer(page, ui);
    const popup = await openCellSitePopup(page, s);

    await popup.getByRole("button", { name: "📡 נתח כיסוי מאתר זה" }).click();
    // Multi-band site → band picker first (defaults to the lowest band)
    const pick = popup.locator('[data-cs-field="pfreq"]');
    await expect(pick).toBeVisible();
    await expect(pick.locator("option").first()).toContainText("(הנמוך)");
    await popup.getByRole("button", { name: "צור אתר לניתוח" }).click();

    await ui.expectToast(/נוצר אתר ".+" עם פרמטרים משוערים|כבר קיים — נבחר לניתוח/);
    await expect(popup).toHaveCount(0);
    await expect(page.getByText("אשף ניתוח כיסוי")).toBeVisible();

    // The promoted site belongs to the operator — look it up through the operator's own listing
    const opApi = await Api.as("OPERATOR");
    const created = (await opApi.listSites()).find((x) => x.cell_site_id === s.id)!;
    expect(created).toBeTruthy();
    expect(created.params_source).toBe("estimated");
    await expect(siteCheckbox(page, created.name)).toBeChecked();
    await expect(page.getByText("✔ 1 נבחרו")).toBeVisible();

    // In the sites list it carries the "estimated" badge
    await ui.tab("אתרי קשר");
    await expect(page.locator("aside [class*='_card_']").filter({ hasText: created.name }))
      .toContainText("⚠ משוער (אתר סלולרי)");
    await opApi.deleteSite(created.id);
    await opApi.dispose();
  });

  test("\"🎯 קישור למכשיר מאתר זה\" בוחר את האתר כמשדר בלשונית קישור נקודתי", async ({ page, loginAs, ui, api }) => {
    const s = sites[4];
    const detail = await api.cellSite(s.id);
    await loginAs("OPERATOR");
    await showCellSitesLayer(page, ui);
    const popup = await openCellSitePopup(page, s);
    await popup.getByRole("button", { name: "🎯 קישור למכשיר מאתר זה" }).click();
    await ui.expectToast("האתר נבחר כמשדר");
    await expect(popup).toHaveCount(0);
    await expect(page.getByLabel("אתר סלולרי אזרחי")).toBeChecked();
    const tx = page.locator("aside [class*='txInfo']");
    await expect(tx).toContainText(detail.site_code);
    await expect(tx).toContainText("משוער");
    await expect(page.getByText("1. משדר")).toBeVisible();
    await expect(page.locator("aside").getByText("✔ נבחר")).toBeVisible();
  });

  test("משתמש צופה רואה את החלון ללא כפתורי עריכה ופעולה", async ({ page, loginAs, ui }) => {
    const s = sites[5];
    await loginAs("VIEWER");
    await showCellSitesLayer(page, ui);
    const popup = await openCellSitePopup(page, s);
    await expect(popup).toContainText("⚠ משוער לפי סוג האתר");
    await expect(popup.getByRole("button")).toHaveCount(0);
  });
});
