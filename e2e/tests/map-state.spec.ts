/**
 * Map state: the app always opens with a clean map, site markers are shown only in context,
 * "נקה תוצאות מהמפה" clears everything and History "טען למפה" restores everything.
 */
import type { Page } from "@playwright/test";
import { test, expect } from "../support/fixtures";
import { USERS, apiLogin, uid, type BBox } from "../support/api";
import {
  JERUSALEM_BBOX, clearResultsButton, prepareAnalysis, resultBlock, runAndWait, runRow, siteCheckbox,
} from "../support/analysis";
import { showCellSitesLayer } from "../support/cellsites";
import { card } from "../support/form";
import { loadFromHistory } from "../support/history";
import {
  bboxHandle, bboxRect, cellSiteMarkerCount, coverageOverlays, imageOverlays, losLines, setView,
  siteMarkers, viewContains, waitForMap,
} from "../support/map";

const STORE_KEY = "coverageops-storage";   // zustand persist key (frontend/src/store/useStore.ts)

const coverageRows = (page: Page) => page.locator("aside [class*='coverageRow']");
const systemRow = (page: Page, name: string) => page.locator("aside [class*='layerRow']").filter({ hasText: name });
const CELL_LAYER = "אתרי סלולר – מאגר ממשלתי";
const DEM_LAYER = "גבהים (DEM)";

/** Records requests that would only happen if a layer were restored on load. */
function trackLayerRequests(page: Page) {
  const urls: string[] = [];
  page.on("request", (r) => {
    const u = r.url();
    if (/\/api\/coverage\/topo-overlay|\/api\/cell-sites\/?(\?|$)|\/api\/coverage\/result\/\d+\/png/.test(u)) urls.push(u);
  });
  return urls;
}

/** Nothing drawn on the map: no site markers, overlays, cell sites, area rectangle, LOS lines. */
async function expectCleanMap(page: Page) {
  await expect(siteMarkers(page)).toHaveCount(0);
  await expect(imageOverlays(page)).toHaveCount(0);
  await expect(bboxRect(page)).toHaveCount(0);
  await expect(bboxHandle(page)).toHaveCount(0);
  await expect(losLines(page)).toHaveCount(0);
  expect(await cellSiteMarkerCount(page)).toBe(0);
}

/** Layers tab: both system layers off, no coverage entries. */
async function expectLayersTabClean(page: Page, ui: import("../support/fixtures").Ui) {
  await ui.tab("🗂 שכבות");
  await expect(systemRow(page, CELL_LAYER).getByTitle("הצג שכבה")).toBeVisible();
  await expect(systemRow(page, DEM_LAYER).getByTitle("הצג שכבה")).toBeVisible();
  await expect(coverageRows(page)).toHaveCount(0);
}

test.describe("מצב המפה: פתיחה נקייה, סמני אתרים בהקשר, ניקוי ושחזור", () => {
  test("(a) אחרי כניסה המפה ריקה — אין סמני אתרים, שכבות, אתרי סלולר או תא שטח", async ({ page, loginAs, ui, api }) => {
    const a = await api.createSite({ name: `E2E נקי-א ${uid()}`, lat: 31.772, lon: 35.212 });
    const b = await api.createSite({ name: `E2E נקי-ב ${uid()}`, lat: 31.760, lon: 35.230 });
    await loginAs("ADMIN");
    // The sites are loaded (cards in the list) but not drawn
    await expect(card(page, a.name)).toBeVisible();
    await expect(card(page, b.name)).toBeVisible();
    await page.waitForTimeout(500);
    await expectCleanMap(page);
    await expect(page.locator(".leaflet-marker-icon")).toHaveCount(0);      // no marker of any kind
    await expect(page.locator(".leaflet-overlay-pane svg path")).toHaveCount(0);
  });

  test("(b) שכבות סלולר + DEM + ניתוח → רענון → מפה נקייה ושכבות כבויות", async ({ page, loginAs, ui, api }) => {
    const name = `E2E רענון ${uid()}`;
    const site = await api.createSite({ name, lat: 31.772, lon: 35.212 });
    await loginAs("ADMIN");

    await showCellSitesLayer(page, ui);
    await expect.poll(() => cellSiteMarkerCount(page)).toBeGreaterThan(1000);
    await setView(page, 31.77, 35.21, 11);     // DEM overlay is refused for country-wide views
    const topo = page.waitForResponse((r) => r.url().includes("/api/coverage/topo-overlay"));
    await systemRow(page, DEM_LAYER).getByTitle("הצג שכבה").click();
    expect((await topo).status()).toBeLessThan(500);

    await prepareAnalysis(page, ui, [name]);
    await runAndWait(page, [name]);
    await expect(coverageOverlays(page)).toHaveCount(1);
    await expect(siteMarkers(page, site.id)).toHaveCount(1);
    await expect(bboxRect(page)).toHaveCount(1);

    const layerRequests = trackLayerRequests(page);
    await page.reload();
    await waitForMap(page);
    await expect(card(page, name)).toBeVisible();
    await page.waitForTimeout(1500);           // give any restored layer time to (wrongly) appear
    await expectCleanMap(page);
    expect(layerRequests, "no layer data is fetched on load").toEqual([]);

    // Nothing persisted about the map
    const stored = await page.evaluate((k) => JSON.parse(localStorage.getItem(k) ?? "{}"), STORE_KEY);
    expect(stored.state?.token).toBeTruthy();
    expect(stored.state?.mapLayers).toBeUndefined();

    await expectLayersTabClean(page, ui);
    await ui.tab("ניתוח");
    await expect(resultBlock(page, name)).toHaveCount(0);
    await expect(runRow(page, name)).toHaveCount(0);
    await expect(siteCheckbox(page, name)).not.toBeChecked();
    await expect(page.getByText("✔ הוגדר")).toHaveCount(0);
  });

  test("(c) סמן אתר מופיע רק בהקשר: בחירה באשף / צפייה ברשימת האתרים", async ({ page, loginAs, ui, api }) => {
    const name = `E2E בחירה ${uid()}`;
    const site = await api.createSite({ name, lat: 31.772, lon: 35.212 });
    await loginAs("ADMIN");
    await expect(siteMarkers(page)).toHaveCount(0);

    await ui.tab("ניתוח");
    await siteCheckbox(page, name).check();
    await expect(siteMarkers(page, site.id)).toHaveCount(1);
    await expect(siteMarkers(page)).toHaveCount(1);
    await siteCheckbox(page, name).uncheck();
    await expect(siteMarkers(page, site.id)).toHaveCount(0);

    // Viewing the site in the Sites list shows it; leaving that context hides it again
    await ui.tab("אתרי קשר");
    await card(page, name).click();
    await expect(siteMarkers(page, site.id)).toHaveCount(1);
    await ui.tab("ניתוח");
    await expect(siteMarkers(page, site.id)).toHaveCount(0);
  });

  test("(d) ניתוח → סמן + תא שטח + כיסוי; \"נקה תוצאות מהמפה\" מסיר את שלושתם", async ({ page, loginAs, ui, api }) => {
    const name = `E2E ניקוי-מלא ${uid()}`;
    const site = await api.createSite({ name, lat: 31.772, lon: 35.212 });
    await loginAs("ADMIN");
    await prepareAnalysis(page, ui, [name]);
    await runAndWait(page, [name]);

    await expect(siteMarkers(page, site.id)).toHaveCount(1);
    await expect(bboxRect(page)).toHaveCount(1);
    await expect(bboxHandle(page)).toHaveCount(1);
    await expect(losLines(page)).toHaveCount(1);
    await expect(coverageOverlays(page)).toHaveCount(1);

    await clearResultsButton(page).click();
    await ui.expectToast("תוצאות, אתר ותא שטח נוקו מהמפה — לצפייה חוזרת: היסטוריה ← טען למפה");
    await expectCleanMap(page);
    await expect(siteCheckbox(page, name)).not.toBeChecked();
    await expect(page.getByText("✔ הוגדר")).toHaveCount(0);
    await expect(resultBlock(page, name)).toHaveCount(0);
    await expect(runRow(page, name)).toHaveCount(0);
    // The result stays on the server
    expect((await api.coverageHistory()).some((h) => h.site_id === site.id)).toBe(true);
  });

  test("(e) היסטוריה → \"טען למפה\" משחזר סמן, תא שטח וכיסוי ומתמקד בהם; ניקוי מסיר שוב", async ({ page, loginAs, ui, api }) => {
    const name = `E2E שחזור ${uid()}`;
    // The site lies south of the area → the fit must include both
    const site = await api.createSite({ name, lat: 31.735, lon: 35.200 });
    await api.analyze(site.id, JERUSALEM_BBOX);
    await loginAs("ADMIN");
    await setView(page, 32.80, 35.50, 12);                 // far away from the result
    expect(await viewContains(page, JERUSALEM_BBOX)).toBe(false);

    await loadFromHistory(page, ui, name);
    await expect(coverageOverlays(page)).toHaveCount(1);
    await expect(siteMarkers(page, site.id)).toHaveCount(1);
    await expect(bboxRect(page)).toHaveCount(1);
    await expect(losLines(page)).toHaveCount(1);
    await expect(page.getByText("אשף ניתוח כיסוי")).toBeVisible();
    await expect(siteCheckbox(page, name)).toBeChecked();
    await expect(page.getByText("✔ הוגדר")).toBeVisible();
    await expect(resultBlock(page, name)).toBeVisible();
    await expect.poll(() => viewContains(page, JERUSALEM_BBOX)).toBe(true);
    const sitePoint: BBox = { sw_lat: site.lat, sw_lon: site.lon, ne_lat: site.lat, ne_lon: site.lon };
    expect(await viewContains(page, sitePoint)).toBe(true);
    expect(await page.evaluate(() => (window as any).__coverageopsMap.getZoom())).toBeGreaterThanOrEqual(11);

    await clearResultsButton(page).click();
    await expectCleanMap(page);
    await expect(siteCheckbox(page, name)).not.toBeChecked();
  });

  test("(f) טעינת שתי תוצאות שונות מההיסטוריה → שני כיסויים ושני סמני אתרים", async ({ page, loginAs, ui, api }) => {
    const a = await api.createSite({ name: `E2E שתיים-א ${uid()}`, lat: 31.772, lon: 35.212 });
    const b = await api.createSite({ name: `E2E שתיים-ב ${uid()}`, lat: 31.760, lon: 35.230, frequency: 1800 });
    await api.analyze(a.id, JERUSALEM_BBOX);
    await api.analyze(b.id, JERUSALEM_BBOX, "DSM");
    await loginAs("ADMIN");

    await loadFromHistory(page, ui, a.name);
    await loadFromHistory(page, ui, b.name);
    await expect(coverageOverlays(page)).toHaveCount(2);
    await expect(siteMarkers(page, a.id)).toHaveCount(1);
    await expect(siteMarkers(page, b.id)).toHaveCount(1);
    await expect(siteMarkers(page)).toHaveCount(2);
    await expect(siteCheckbox(page, a.name)).toBeChecked();
    await expect(siteCheckbox(page, b.name)).toBeChecked();

    // Hiding one coverage layer while its site is still selected keeps the marker (wizard context)
    await ui.tab("🗂 שכבות");
    await expect(coverageRows(page)).toHaveCount(2);
    await coverageRows(page).filter({ hasText: b.name }).getByTitle("הסתר כיסוי").click();
    await expect(coverageOverlays(page)).toHaveCount(1);
    await expect(siteMarkers(page, b.id)).toHaveCount(1);

    await ui.tab("ניתוח");
    await clearResultsButton(page).click();
    await expectCleanMap(page);
    await expectLayersTabClean(page, ui);
  });

  test("(g) מצב ישן ב-localStorage עם שכבות שמורות — המערכת עדיין נפתחת נקייה", async ({ page, request, ui, api }) => {
    const name = `E2E מצב-ישן ${uid()}`;
    const site = await api.createSite({ name, lat: 31.772, lon: 35.212 });
    const result = await api.analyze(site.id, JERUSALEM_BBOX);
    const u = USERS.ADMIN;
    const auth = await apiLogin(request, u.username, u.password);
    const oldLayers = [
      { id: "cellsites", name: CELL_LAYER, type: "cellular", visible: true },
      { id: "dem", name: DEM_LAYER, type: "dem", visible: true },
      { id: "cellular", name: "OSM ישן", type: "cellular", visible: true },
      {
        id: `coverage-${result.id}`, name: `${name} — DTM`, type: "coverage", visible: true,
        resultId: result.id, bbox: JERUSALEM_BBOX, mode: "DTM", siteName: name, siteId: site.id,
      },
    ];
    // Injected before the app's scripts run, on the first load only (v0 = the old persisted format)
    await page.addInitScript(([key, a, layers]) => {
      if (sessionStorage.getItem("e2e-old-state")) return;
      sessionStorage.setItem("e2e-old-state", "1");
      localStorage.setItem("token", a.access_token);
      localStorage.setItem(key, JSON.stringify({
        state: {
          token: a.access_token,
          user: { id: a.user_id, username: a.username, full_name: a.full_name, role: a.role },
          analysisMode: "DSM", activeBaseMapId: "esri-street", mapLayers: layers,
        },
        version: 0,
      }));
    }, [STORE_KEY, auth, oldLayers] as const);

    const layerRequests = trackLayerRequests(page);
    await page.goto("/");
    await waitForMap(page);
    await expect(card(page, name)).toBeVisible();          // logged in from the old state
    await page.waitForTimeout(1500);
    await expectCleanMap(page);
    expect(layerRequests).toEqual([]);
    await expectLayersTabClean(page, ui);
    await expect(page.locator("aside").getByText("OSM ישן")).toHaveCount(0);
    // Non-map preferences from the old state are kept
    await ui.tab("ניתוח");
    await expect(page.getByRole("button", { name: /תכסית/ })).toHaveClass(/active/);

    // Same layers saved in the current format version → still ignored
    await page.evaluate(([key, layers]) => {
      const st = JSON.parse(localStorage.getItem(key)!);
      st.state.mapLayers = layers;
      localStorage.setItem(key, JSON.stringify(st));
    }, [STORE_KEY, oldLayers] as const);
    await page.reload();
    await waitForMap(page);
    await expect(card(page, name)).toBeVisible();
    await page.waitForTimeout(1500);
    await expectCleanMap(page);
    expect(layerRequests).toEqual([]);
    await expectLayersTabClean(page, ui);
  });

  test("(h) יציאה וכניסה מחדש באותו חלון → מפה נקייה", async ({ page, loginAs, ui, api }) => {
    const name = `E2E כניסה-חדשה ${uid()}`;
    const site = await api.createSite({ name, lat: 31.772, lon: 35.212 });
    await api.analyze(site.id, JERUSALEM_BBOX);
    await loginAs("ADMIN");
    await showCellSitesLayer(page, ui);
    await loadFromHistory(page, ui, name);
    await expect(coverageOverlays(page)).toHaveCount(1);
    await expect(siteMarkers(page, site.id)).toHaveCount(1);

    await page.getByRole("button", { name: "יציאה" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.getByPlaceholder("הקלד שם משתמש").fill(USERS.ADMIN.username);
    await page.getByPlaceholder("••••••••").fill(USERS.ADMIN.password);
    await page.getByRole("button", { name: "כניסה למערכת" }).click();
    await expect(page).toHaveURL(/\/$/);
    await waitForMap(page);
    await ui.tab("אתרי קשר");
    await expect(card(page, name)).toBeVisible();
    await page.waitForTimeout(1000);
    await expectCleanMap(page);
    await expectLayersTabClean(page, ui);
  });
});
