/**
 * Leaflet helpers. The dev build exposes the map instance as `window.__coverageopsMap`
 * (MapView.tsx, `import.meta.env.DEV` only) so tests can convert lat/lon ↔ screen points.
 */
import { expect, type Page } from "@playwright/test";
import type { BBox } from "./api";

export interface LatLon { lat: number; lon: number }

export async function waitForMap(page: Page) {
  await page.waitForFunction(() => !!(window as any).__coverageopsMap, null, { timeout: 20_000 });
}

/** Jump (no animation) to a view. */
export async function setView(page: Page, lat: number, lon: number, zoom: number) {
  await waitForMap(page);
  await page.evaluate(([la, lo, z]) => {
    const m = (window as any).__coverageopsMap;
    m.setView([la, lo], z, { animate: false });
  }, [lat, lon, zoom] as const);
}

/** Fit the view to a bbox (no animation). */
export async function fitBounds(page: Page, b: BBox, maxZoom = 14) {
  await waitForMap(page);
  await page.evaluate(([bb, mz]) => {
    const m = (window as any).__coverageopsMap;
    m.fitBounds([[bb.sw_lat, bb.sw_lon], [bb.ne_lat, bb.ne_lon]], { animate: false, maxZoom: mz, padding: [40, 40] });
  }, [b, maxZoom] as const);
}

/** Page (viewport) coordinates of a lat/lon. */
export async function pointOf(page: Page, p: LatLon): Promise<{ x: number; y: number }> {
  return page.evaluate(({ lat, lon }) => {
    const m = (window as any).__coverageopsMap;
    const pt = m.latLngToContainerPoint([lat, lon]);
    const r = m.getContainer().getBoundingClientRect();
    return { x: r.left + pt.x, y: r.top + pt.y };
  }, p);
}

/** Lat/lon at a viewport point. */
export async function latLonAt(page: Page, x: number, y: number): Promise<LatLon> {
  return page.evaluate(([px, py]) => {
    const m = (window as any).__coverageopsMap;
    const r = m.getContainer().getBoundingClientRect();
    const ll = m.containerPointToLatLng([px - r.left, py - r.top]);
    return { lat: ll.lat, lon: ll.lng };
  }, [x, y] as const);
}

/** Map centre as lat/lon. */
export async function mapCenter(page: Page): Promise<LatLon> {
  return page.evaluate(() => {
    const c = (window as any).__coverageopsMap.getCenter();
    return { lat: c.lat, lon: c.lng };
  });
}

export async function clickMapAt(page: Page, p: LatLon) {
  const { x, y } = await pointOf(page, p);
  await page.mouse.click(x, y);
}

/** Mouse-drag a rectangle on the map (rect draw mode must be active). */
export async function dragRect(page: Page, b: BBox) {
  const a = await pointOf(page, { lat: b.ne_lat, lon: b.sw_lon });   // top-left
  const z = await pointOf(page, { lat: b.sw_lat, lon: b.ne_lon });   // bottom-right
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move((a.x + z.x) / 2, (a.y + z.y) / 2, { steps: 5 });
  await page.mouse.move(z.x, z.y, { steps: 5 });
  await page.mouse.up();
}

/** Coverage PNG overlays currently on the map (zIndex 300 — the DEM overlay uses 200). */
export function coverageOverlays(page: Page) {
  return page.locator('.leaflet-overlay-pane img.leaflet-image-layer[style*="z-index: 300"]');
}

/** DEM / topography overlay (zIndex 200). */
export function demOverlays(page: Page) {
  return page.locator('.leaflet-overlay-pane img.leaflet-image-layer[style*="z-index: 200"]');
}

/** Vector paths (polylines, rectangles, SVG circle markers) drawn by the default SVG renderer. */
export function svgPaths(page: Page) {
  return page.locator(".leaflet-overlay-pane svg path");
}

/** Civil cell-site markers on the canvas renderer (they have no DOM nodes — counted on the map). */
export async function cellSiteMarkerCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    const m = (window as any).__coverageopsMap;
    let n = 0;
    m.eachLayer((l: any) => {
      if (typeof l.getRadius === "function" && l.options?.renderer && l.options.renderer._ctx !== undefined) n++;
    });
    return n;
  });
}

/** Polylines on the map with a given colour/weight predicate — e.g. link / best-server lines. */
export async function polylineWeights(page: Page): Promise<number[]> {
  return page.evaluate(() => {
    const m = (window as any).__coverageopsMap;
    const out: number[] = [];
    m.eachLayer((l: any) => {
      if (typeof l.getLatLngs === "function" && typeof l.getRadius !== "function"
          && !(typeof l.getBounds === "function" && l.options?.fill)) out.push(l.options.weight);
    });
    return out;
  });
}

export async function expectOverlayCount(page: Page, n: number) {
  await expect(coverageOverlays(page)).toHaveCount(n);
}
