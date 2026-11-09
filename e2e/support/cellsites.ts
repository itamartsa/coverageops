/**
 * Civil cell-site layer helpers. Markers are drawn on a <canvas>, so a marker is clicked by
 * projecting its lat/lon to a screen point (window.__coverageopsMap, dev build only).
 */
import { expect, type Page } from "@playwright/test";
import type { Api, CellSiteRow } from "./api";
import type { Ui } from "./fixtures";
import { clickMapAt, setView } from "./map";

function distM(a: CellSiteRow, b: CellSiteRow) {
  const dLat = (a.lat - b.lat) * 111_320;
  const dLon = (a.lon - b.lon) * 111_320 * Math.cos((a.lat * Math.PI) / 180);
  return Math.hypot(dLat, dLon);
}

/**
 * Active cell sites with no other site within `minGapM` (so a click hits exactly that marker).
 * Deterministic order; `filter` narrows the candidates (e.g. multi-band sites).
 */
export async function isolatedCellSites(
  api: Api, count: number, filter: (c: CellSiteRow) => boolean = () => true, minGapM = 250,
): Promise<CellSiteRow[]> {
  const all = await api.cellSites();
  const out: CellSiteRow[] = [];
  const candidates = all
    .filter((c) => c.status === "active" && c.height_source === "estimated" && filter(c))
    // inland, central Israel — well inside the map's maxBounds
    .filter((c) => c.lat > 31.2 && c.lat < 32.9 && c.lon > 34.85 && c.lon < 35.6)
    .sort((a, b) => a.id - b.id);
  for (const c of candidates) {
    if (all.every((o) => o.id === c.id || distM(c, o) > minGapM)) out.push(c);
    if (out.length >= count) break;
  }
  expect(out.length, "isolated cell sites available").toBe(count);
  return out;
}

/** Turns the civil cell-sites layer on (🗂 שכבות) and waits for the markers. */
export async function showCellSitesLayer(page: Page, ui: Ui) {
  await ui.tab("🗂 שכבות");
  const row = page.locator("aside [class*='layerRow']").filter({ hasText: "אתרי סלולר – מאגר ממשלתי" });
  await row.getByTitle("הצג שכבה").click();
  await ui.expectToast(/מוצגים [\d,]+ אתרי סלולר/);
}

/** Zooms to the site, clicks its canvas marker and returns the popup root. */
export async function openCellSitePopup(page: Page, site: CellSiteRow) {
  await setView(page, site.lat, site.lon, 17);
  await page.waitForTimeout(150);   // canvas redraw after the view change
  await clickMapAt(page, site);
  const popup = page.locator(`.leaflet-popup-content [data-cellsite-id="${site.id}"]`);
  await expect(popup).toBeVisible();
  return popup;
}
