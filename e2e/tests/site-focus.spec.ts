/**
 * Sites list "🎯 מיקוד": pins a site's marker on the map in every tab, flies the map to it,
 * opens its popup and pulses the marker. Toggling un-focuses; clear / delete / reload / logout
 * drop the focus (it is never persisted).
 */
import type { Page } from "@playwright/test";
import { test, expect } from "../support/fixtures";
import { USERS, uid, type Site } from "../support/api";
import { JERUSALEM_BBOX, clearResultsButton, resetAreaButton, siteCheckbox } from "../support/analysis";
import { card, focusButton } from "../support/form";
import { loadFromHistory } from "../support/history";
import { dragRect, fitBounds, mapCenter, setView, siteMarkers, waitForMap } from "../support/map";

const STORE_KEY = "coverageops-storage";

const sitePopup = (page: Page, name: string) => page.locator(".leaflet-popup").filter({ hasText: name });
const pulsingMarkers = (page: Page) => page.locator(".leaflet-marker-icon[class*='focusPulse']");
const mapZoom = (page: Page) => page.evaluate(() => (window as any).__coverageopsMap.getZoom() as number);

/** After the fly animation: map centre (almost) on the site and zoomed in to at least 14. */
async function expectMapOn(page: Page, site: Site) {
  await expect.poll(async () => {
    const c = await mapCenter(page);
    const near = Math.max(Math.abs(c.lat - site.lat), Math.abs(c.lon - site.lon)) < 0.01;
    return near && (await mapZoom(page)) >= 14;
  }, { message: "map centred on the focused site at zoom ≥ 14" }).toBe(true);
}

async function expectFocused(page: Page, name: string, on: boolean) {
  const btn = focusButton(page, name);
  await expect(btn).toHaveAttribute("aria-pressed", on ? "true" : "false");
  await expect(btn).toHaveText(on ? "🎯 ממוקד" : "🎯 מיקוד");
  if (on) await expect(card(page, name)).toHaveClass(/_focused_/);
  else await expect(card(page, name)).not.toHaveClass(/_focused_/);
}

test.describe("מיקוד על אתר במפה (🎯 מיקוד ברשימת האתרים)", () => {
  test("מיקוד מציג סמן, טס אליו, פותח חלון מידע, מהבהב ומסמן את הכרטיס", async ({ page, loginAs, api }) => {
    const name = `E2E מיקוד ${uid()}`;
    const site = await api.createSite({ name, lat: 31.6123, lon: 34.9876 });
    await loginAs("ADMIN");
    await setView(page, 32.80, 35.50, 10);                     // far away from the site
    await expect(siteMarkers(page)).toHaveCount(0);
    await expectFocused(page, name, false);
    await expect(focusButton(page, name)).toHaveAttribute("title", "מיקוד על האתר במפה");

    await focusButton(page, name).click();
    await expect(siteMarkers(page, site.id)).toHaveCount(1);
    await expect(siteMarkers(page)).toHaveCount(1);
    await expectMapOn(page, site);
    await expect(sitePopup(page, name)).toBeVisible();
    await expect(pulsingMarkers(page)).toHaveCount(1);
    await expectFocused(page, name, true);
    // The button does not trigger the card click (the site is not "viewed")
    await expect(card(page, name)).not.toHaveClass(/_active_/);
    // The pulse is brief (~2 s) — the marker stays
    await expect(pulsingMarkers(page)).toHaveCount(0, { timeout: 5_000 });
    await expect(siteMarkers(page, site.id)).toHaveCount(1);
  });

  test("הסמן נשאר במעבר לשוניות; לחיצה חוזרת מבטלת את המיקוד", async ({ page, loginAs, ui, api }) => {
    const name = `E2E מיקוד-לשוניות ${uid()}`;
    const site = await api.createSite({ name, lat: 31.772, lon: 35.212 });
    await loginAs("ADMIN");

    await focusButton(page, name).click();
    await expect(siteMarkers(page, site.id)).toHaveCount(1);
    for (const t of ["ניתוח", "🗂 שכבות", "היסטוריה", "חתך רדיו"] as const) {
      await ui.tab(t);
      await expect(siteMarkers(page, site.id)).toHaveCount(1);
    }

    await ui.tab("אתרי קשר");
    await expectFocused(page, name, true);
    await focusButton(page, name).click();
    await expectFocused(page, name, false);
    await expect(siteMarkers(page)).toHaveCount(0);
    await expect(sitePopup(page, name)).toHaveCount(0);
    await ui.tab("ניתוח");
    await expect(siteMarkers(page)).toHaveCount(0);
  });

  test("מיקוד A ואז B → רק B; אתר נצפה + ממוקד זהה → סמן אחד", async ({ page, loginAs, ui, api }) => {
    const a = await api.createSite({ name: `E2E מיקוד-א ${uid()}`, lat: 31.772, lon: 35.212 });
    const b = await api.createSite({ name: `E2E מיקוד-ב ${uid()}`, lat: 31.700, lon: 35.100, frequency: 1800 });
    await loginAs("ADMIN");

    await focusButton(page, a.name).click();
    await expect(siteMarkers(page, a.id)).toHaveCount(1);
    await focusButton(page, b.name).click();
    await expect(siteMarkers(page, b.id)).toHaveCount(1);
    await expect(siteMarkers(page, a.id)).toHaveCount(0);
    await expect(siteMarkers(page)).toHaveCount(1);
    await expectFocused(page, a.name, false);
    await expectFocused(page, b.name, true);
    await expectMapOn(page, b);
    await expect(sitePopup(page, b.name)).toBeVisible();

    // Viewing the focused site too → still exactly one marker
    await card(page, b.name).click();
    await expect(card(page, b.name)).toHaveClass(/_active_/);
    await expect(siteMarkers(page)).toHaveCount(1);
    // Viewing A (Sites tab) adds A; leaving the tab keeps only the focused B
    await card(page, a.name).click();
    await expect(siteMarkers(page)).toHaveCount(2);
    await ui.tab("ניתוח");
    await expect(siteMarkers(page, a.id)).toHaveCount(0);
    await expect(siteMarkers(page, b.id)).toHaveCount(1);

    // Another context rule (wizard selection) keeps B after un-focusing
    await siteCheckbox(page, b.name).check();
    await ui.tab("אתרי קשר");
    await focusButton(page, b.name).click();
    await expectFocused(page, b.name, false);
    await ui.tab("ניתוח");
    await expect(siteMarkers(page, b.id)).toHaveCount(1);
    await siteCheckbox(page, b.name).uncheck();
    await expect(siteMarkers(page)).toHaveCount(0);
  });

  test("\"אפס ובחר חדש\" ו-\"נקה תוצאות מהמפה\" מנקים גם את המיקוד", async ({ page, loginAs, ui, api }) => {
    const f = await api.createSite({ name: `E2E מיקוד-ניקוי ${uid()}`, lat: 31.700, lon: 35.100 });
    const w = await api.createSite({ name: `E2E אשף-ניקוי ${uid()}`, lat: 31.772, lon: 35.212 });
    await api.analyze(w.id, JERUSALEM_BBOX);
    await loginAs("ADMIN");

    // (1) area drawn in the wizard → "✕ אפס ובחר חדש"
    await focusButton(page, f.name).click();
    await expect(siteMarkers(page, f.id)).toHaveCount(1);
    await ui.tab("ניתוח");
    await siteCheckbox(page, w.name).check();
    await fitBounds(page, JERUSALEM_BBOX, 13);
    await page.getByRole("button", { name: "🔲 שרטט מלבן כיסוי חופשי" }).click();
    await dragRect(page, JERUSALEM_BBOX);
    await expect(page.getByText("✔ הוגדר")).toBeVisible();
    await expect(siteMarkers(page)).toHaveCount(2);
    await resetAreaButton(page).click();
    await expect(siteMarkers(page)).toHaveCount(0);
    await ui.tab("אתרי קשר");
    await expectFocused(page, f.name, false);

    // (2) result loaded from History → "✕ נקה תוצאות מהמפה"
    await focusButton(page, f.name).click();
    await loadFromHistory(page, ui, w.name);
    await expect(siteMarkers(page, w.id)).toHaveCount(1);
    await expect(siteMarkers(page, f.id)).toHaveCount(1);
    await clearResultsButton(page).click();
    await expect(siteMarkers(page)).toHaveCount(0);
    await ui.tab("אתרי קשר");
    await expectFocused(page, f.name, false);
    await expect(siteMarkers(page)).toHaveCount(0);
  });

  test("רענון ויציאה/כניסה → אין מיקוד (לא נשמר)", async ({ page, loginAs, ui, api }) => {
    const name = `E2E מיקוד-רענון ${uid()}`;
    const site = await api.createSite({ name, lat: 31.772, lon: 35.212 });
    await loginAs("ADMIN");
    await focusButton(page, name).click();
    await expect(siteMarkers(page, site.id)).toHaveCount(1);

    const stored = await page.evaluate((k) => JSON.parse(localStorage.getItem(k) ?? "{}"), STORE_KEY);
    expect(stored.state?.focusedSiteId).toBeUndefined();

    await page.reload();
    await waitForMap(page);
    await expect(card(page, name)).toBeVisible();
    await page.waitForTimeout(800);
    await expect(siteMarkers(page)).toHaveCount(0);
    await expectFocused(page, name, false);

    // Logout clears it too
    await focusButton(page, name).click();
    await expect(siteMarkers(page, site.id)).toHaveCount(1);
    await page.getByRole("button", { name: "יציאה" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.getByPlaceholder("הקלד שם משתמש").fill(USERS.ADMIN.username);
    await page.getByPlaceholder("••••••••").fill(USERS.ADMIN.password);
    await page.getByRole("button", { name: "כניסה למערכת" }).click();
    await waitForMap(page);
    await ui.tab("אתרי קשר");
    await expect(card(page, name)).toBeVisible();
    await page.waitForTimeout(800);
    await expect(siteMarkers(page)).toHaveCount(0);
    await expectFocused(page, name, false);
  });

  test("משתמש צופה (VIEWER) יכול למקד — בלי כפתורי עריכה/מחיקה", async ({ page, loginAs, ui, api }) => {
    const name = `E2E מיקוד-צופה ${uid()}`;
    const site = await api.createSite({ name, lat: 31.650, lon: 35.050 });
    // The backend lists only a user's own sites and a VIEWER cannot create any, so serve the
    // VIEWER a list containing a real site (as if it were assigned to them).
    await page.route(/\/api\/sites\/?$/, (route) =>
      route.request().method() === "GET" ? route.fulfill({ json: [site] }) : route.fallback());
    await loginAs("VIEWER");
    const c = card(page, name);
    await expect(c).toBeVisible();
    await expect(c.getByRole("button", { name: "✏️ ערוך" })).toHaveCount(0);
    await expect(c.getByRole("button", { name: "✕ מחק" })).toHaveCount(0);

    await focusButton(page, name).click();
    await expect(siteMarkers(page, site.id)).toHaveCount(1);
    await expectMapOn(page, site);
    await expect(sitePopup(page, name)).toBeVisible();
    await expectFocused(page, name, true);
    await ui.tab("ניתוח");
    await expect(siteMarkers(page, site.id)).toHaveCount(1);
  });

  test("מחיקת האתר הממוקד → הסמן נעלם והמיקוד מתבטל", async ({ page, loginAs, ui, api }) => {
    const name = `E2E מיקוד-מחיקה ${uid()}`;
    const site = await api.createSite({ name, lat: 31.720, lon: 35.150 });
    await loginAs("ADMIN");
    await focusButton(page, name).click();
    await expect(siteMarkers(page, site.id)).toHaveCount(1);

    await card(page, name).getByRole("button", { name: "✕ מחק" }).click();
    await ui.expectToast("אתר נמחק");
    await expect(card(page, name)).toHaveCount(0);
    await expect(siteMarkers(page)).toHaveCount(0);
    await expect(sitePopup(page, name)).toHaveCount(0);
    await ui.tab("ניתוח");
    await expect(siteMarkers(page)).toHaveCount(0);
    expect((await api.listSites()).some((s) => s.id === site.id)).toBe(false);
  });
});
