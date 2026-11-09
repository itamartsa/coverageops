import fs from "node:fs";
import { test, expect } from "../support/fixtures";
import { uid } from "../support/api";
import { clickMapAt, fitBounds, polylineWeights, setView, svgPaths } from "../support/map";

const TX = { lat: 31.772, lon: 35.212 };
const RX = { lat: 31.790, lon: 35.240 };
const TEL_AVIV = { lat: 32.08, lon: 34.78 };

const linkTab = "🎯 קישור נקודתי" as const;
const resultCard = (page: import("@playwright/test").Page) => page.locator("aside [class*='_result_']");
const serverRows = (page: import("@playwright/test").Page) => page.locator("aside [class*='_server_']");
const historyRows = (page: import("@playwright/test").Page) => page.locator("aside [class*='histRow']");

async function pickReceiver(page: import("@playwright/test").Page, p: { lat: number; lon: number }) {
  await page.getByRole("button", { name: /📍 בחר מיקום/ }).click();
  await expect(page.getByText("בחירת מיקום המקלט - לחץ על המפה")).toBeVisible();
  await clickMapAt(page, p);
  await expect(page.locator('div[title="מקלט — גרור לשינוי מיקום"]')).toBeVisible();
}

test.describe("קישור נקודתי", () => {
  test("קישור מאתר שלי: חישוב, כרטיס תוצאה, תרשים, קו במפה, גרירה, דוח, היסטוריה וניקוי", async ({ page, loginAs, ui, api }) => {
    const name = `E2E קישור ${uid()}`;
    await api.createSite({ name, ...TX, frequency: 900, tx_power: 43, ant_height: 20 });
    const profiles = await api.deviceProfiles();
    const vehicle = profiles.find((p) => !p.is_default)!;

    await loginAs("ADMIN");
    await ui.tab(linkTab);
    await expect(page.getByLabel("האתרים שלי")).toBeChecked();
    const run = page.getByRole("button", { name: "▶ חשב קישור" });
    await expect(run).toBeDisabled();

    await page.locator("aside select").nth(0).selectOption({ label: `${name} (900 MHz)` });
    await expect(page.locator("aside [class*='txInfo']")).toContainText("900 MHz · 43 dBm · אנטנה 20 מ'");
    await page.locator("aside select").nth(1).selectOption({ label: vehicle.name_he });
    await expect(page.locator("aside").getByText(/גובה [\d.]+ מ' · רגישות −\d+ dBm/)).toBeVisible();

    await fitBounds(page, { sw_lat: 31.76, sw_lon: 35.20, ne_lat: 31.80, ne_lon: 35.25 }, 14);
    await pickReceiver(page, RX);
    await expect(page.locator("aside [class*='coords']")).toContainText(/31\.(78|79)\d+°N, 35\.(23|24)\d+°E/);
    await expect(run).toBeEnabled();

    const pathsBefore = await svgPaths(page).count();
    const req = page.waitForRequest("**/api/link/");
    await run.click();
    const body = (await req).postDataJSON();
    expect(body.transmitter.kind).toBe("site");
    expect(body.receiver.device_profile_id).toBe(vehicle.id);

    // Result card
    const r = resultCard(page);
    await expect(r).toContainText("תוצאת קישור");
    await expect(r).toContainText(name);
    await expect(r.locator("[class*='bigVal']").first()).toHaveText(/^−\d+(\.\d)? dBm$/);   // RSSI
    await expect(r.locator("[class*='bigVal']").nth(1)).toHaveText(/^[+−]?\d+\.\d dB$/);    // margin
    await expect(r.locator("[class*='levelPill']")).toHaveText(/מצוין|טוב|בינוני|חלש|שולי|אין קליטה/);
    await expect(r.locator("[class*='_chip_']").filter({ hasText: /👁 LOS|⛰ NLOS/ })).toHaveCount(1);
    await expect(r.locator("[class*='warnBox']")).toContainText("ללא נתוני טופוגרפיה");
    await expect(r).toContainText("⚠ סימולציה");

    // Terrain profile chart (SVG): LOS + Fresnel paths; no DEM here → terrain gaps are flagged
    const chart = r.locator('svg[aria-label="פרופיל גובה לאורך הקישור"]');
    await expect(chart).toBeVisible();
    expect(await chart.locator("path").count()).toBeGreaterThanOrEqual(3);
    await expect(r.locator("[class*='chartLegend']")).toContainText("קו ראייה");
    await expect(r.locator("[class*='chartLegend']")).toContainText("טרן");
    const gapNote = r.getByText("פערים = אין נתוני גובה");
    if (!(await gapNote.count())) expect(await chart.locator("path[class*='chartTerrain']").count()).toBeGreaterThan(0);

    // Link line (+ casing + tx marker) on the map
    await expect.poll(() => svgPaths(page).count()).toBeGreaterThanOrEqual(pathsBefore + 3);
    await expect(page.getByText(/⚠ המקלט הוזז/)).toHaveCount(0);

    // Word report
    const dl = page.waitForEvent("download");
    await r.getByRole("button", { name: "⬇ דוח Word" }).click();
    const download = await dl;
    expect(download.suggestedFilename()).toMatch(/^link_\d+_.+\.docx$/);
    expect(fs.readFileSync(await download.path()).subarray(0, 2).toString("latin1")).toBe("PK");
    await ui.expectToast("הדוח הורד בהצלחה");

    // Drag the receiver → result marked stale
    const rxIcon = page.locator('div[title="מקלט — גרור לשינוי מיקום"]');
    const box = (await rxIcon.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + 60, box.y + 50, { steps: 8 });
    await page.mouse.up();
    await expect(page.getByText("⚠ המקלט הוזז מאז החישוב — יש לחשב מחדש")).toBeVisible();

    // History list survives a reload; clicking a row loads it back
    await expect(historyRows(page).filter({ hasText: name })).toHaveCount(1);
    await page.reload();
    await ui.tab(linkTab);
    const row = historyRows(page).filter({ hasText: name });
    await expect(row).toHaveCount(1);
    await expect(resultCard(page)).toHaveCount(0);
    await row.click();
    await expect(resultCard(page)).toContainText(name);
    await expect.poll(() => svgPaths(page).count()).toBeGreaterThanOrEqual(3);

    // "✕ נקה תוצאות מהמפה" removes the link line / markers from the map
    await page.getByRole("button", { name: "✕ נקה תוצאות מהמפה" }).click();
    await expect(resultCard(page)).toHaveCount(0);
    await expect(svgPaths(page)).toHaveCount(0);
    expect(await polylineWeights(page)).toEqual([]);
    await expect(page.locator(".leaflet-marker-icon").filter({ hasText: "▲" })).toHaveCount(0);
  });

  test("מחיקת קישור מהיסטוריה (עם אישור)", async ({ page, loginAs, ui, api }) => {
    const name = `E2E קישור-מחיקה ${uid()}`;
    const site = await api.createSite({ name, ...TX });
    await api.runLink(site.id, RX);
    await loginAs("ADMIN");
    await ui.tab(linkTab);
    const row = historyRows(page).filter({ hasText: name });
    await row.getByTitle("מחק").click();
    await row.getByRole("button", { name: "מחק", exact: true }).click();
    await ui.expectToast("ניתוח נמחק");
    await expect(historyRows(page).filter({ hasText: name })).toHaveCount(0);
  });

  test("מי משרת את המכשיר: רשימה מדורגת, הדגשה, ומעבר לניתוח קישור מלא", async ({ page, loginAs, ui }) => {
    await loginAs("OPERATOR");
    await ui.tab(linkTab);
    await page.getByRole("button", { name: "📶 מי משרת את המכשיר" }).click();
    await expect(page.getByText("1. משדר")).toHaveCount(0);
    const find = page.getByRole("button", { name: "▶ מצא אתרים משרתים" });
    await expect(find).toBeDisabled();

    await setView(page, TEL_AVIV.lat, TEL_AVIV.lon, 14);
    await pickReceiver(page, TEL_AVIV);
    await find.click();
    await ui.expectToast(/נמצאו 5 אתרים משרתים/);

    const rows = serverRows(page);
    await expect(rows).toHaveCount(5);
    for (let i = 0; i < 5; i++) await expect(rows.nth(i).locator("[class*='rank']")).toHaveText(String(i + 1));
    await expect(rows.first()).toContainText(/−\d+\.\d dBm/);
    await expect(page.locator("aside").getByText(/נבדקו \d+ אתרים/)).toBeVisible();
    // ranked by RSSI (descending)
    const rssi = await rows.locator("[class*='serverRssi']").allInnerTexts();
    const vals = rssi.map((t) => Number(t.replace("−", "-").replace(/[^\d.-]/g, "")));
    expect([...vals].sort((a, b) => b - a)).toEqual(vals);

    // Lines receiver → each server on the map
    await expect.poll(async () => (await polylineWeights(page)).length).toBe(5);
    expect(await polylineWeights(page)).not.toContain(7);

    // Clicking a row highlights it (row + thick line on the map); clicking again un-highlights
    await rows.nth(1).click();
    await expect(page.locator("aside [class*='serverHl']")).toHaveCount(1);
    await expect(rows.nth(1)).toHaveClass(/serverHl/);
    await expect.poll(async () => (await polylineWeights(page)).filter((w) => w === 7).length).toBe(1);
    await rows.nth(1).click();
    await expect(page.locator("aside [class*='serverHl']")).toHaveCount(0);

    // "נתח קישור מלא" switches to link mode and runs it
    const opText = await rows.first().locator("[class*='opBadge']").innerText();
    const linkReq = page.waitForResponse((r) => r.url().endsWith("/api/link/") && r.request().method() === "POST");
    await rows.first().getByRole("button", { name: "🎯 נתח קישור מלא" }).click();
    expect((await linkReq).status()).toBe(200);
    await expect(page.getByText("1. משדר")).toBeVisible();
    await expect(page.getByLabel("אתר סלולרי אזרחי")).toBeChecked();
    await expect(resultCard(page)).toContainText("תוצאת קישור");
    await expect(resultCard(page)).toContainText("⚠ משוער:");
    await expect(page.locator("aside [class*='txInfo']")).toContainText(opText.split(" ")[0]);
    await expect(serverRows(page)).toHaveCount(0);

    // Back to best-servers mode: its lines return, the link line hides
    await page.getByRole("button", { name: "📶 מי משרת את המכשיר" }).click();
    await expect(serverRows(page)).toHaveCount(5);
    await page.getByRole("button", { name: "✕ נקה תוצאות מהמפה" }).click();
    await expect(serverRows(page)).toHaveCount(0);
    await expect.poll(async () => (await polylineWeights(page)).length).toBe(0);
  });

  test("מי משרת — סינון לפי מפעיל", async ({ page, loginAs, ui }) => {
    await loginAs("OPERATOR");
    await ui.tab(linkTab);
    await page.getByRole("button", { name: "📶 מי משרת את המכשיר" }).click();
    await page.locator("aside label").filter({ hasText: "מפעיל" }).locator("select").selectOption("PELEPHONE");
    await page.locator("aside label").filter({ hasText: "מספר תוצאות" }).locator("input").fill("3");
    await setView(page, TEL_AVIV.lat, TEL_AVIV.lon, 14);
    await pickReceiver(page, TEL_AVIV);
    const req = page.waitForRequest("**/api/link/best-servers");
    await page.getByRole("button", { name: "▶ מצא אתרים משרתים" }).click();
    const body = (await req).postDataJSON();
    expect(body.operator).toBe("PELEPHONE");
    expect(body.top_n).toBe(3);
    await expect(serverRows(page)).toHaveCount(3);
    for (const t of await serverRows(page).locator("[class*='opBadge']").allInnerTexts()) {
      expect(t).toContain("פלאפון");
    }
  });

  test("הסרת המקלט ובחירה מחדש", async ({ page, loginAs, ui }) => {
    await loginAs("OPERATOR");
    await ui.tab(linkTab);
    await setView(page, TEL_AVIV.lat, TEL_AVIV.lon, 13);
    await pickReceiver(page, TEL_AVIV);
    await page.locator("aside").getByTitle("הסר את המקלט").click();
    await expect(page.locator('div[title="מקלט — גרור לשינוי מיקום"]')).toHaveCount(0);
    await expect(page.getByRole("button", { name: "📍 בחר מיקום במפה" })).toBeVisible();
  });
});
