import { test, expect } from "../support/fixtures";
import { uid } from "../support/api";
import { card, fieldByLabel } from "../support/form";
import { clickMapAt, coverageOverlays, setView } from "../support/map";
import { JERUSALEM_BBOX } from "../support/analysis";
import { loadFromHistory } from "../support/history";

test.describe("אתרי קשר", () => {
  test("הקמת אתר דרך הטופס — כולל בחירת מיקום מהמפה", async ({ page, loginAs, ui, api }) => {
    const name = `E2E אתר טופס ${uid()}`;
    await loginAs("OPERATOR");
    await ui.tab("הקמת אתר");

    await page.getByPlaceholder("לדוגמה: אתר גבעה 27").fill(name);
    await setView(page, 31.77, 35.21, 12);
    await page.getByRole("button", { name: "📍 בחר מיקום מהמפה" }).click();
    await expect(page.getByText("בחירת מיקום - לחץ על המפה")).toBeVisible();
    await clickMapAt(page, { lat: 31.7712, lon: 35.2134 });
    await ui.expectToast("מיקום נקלט");
    await expect(page.getByPlaceholder("31.7683")).toHaveValue(/^31\.77\d+/);
    await expect(page.getByPlaceholder("35.2137")).toHaveValue(/^35\.21\d+/);

    await fieldByLabel(page, "גובה אנטנה מעל קרקע").fill("25");
    await fieldByLabel(page, "תדר סלולר").selectOption("1800");
    await fieldByLabel(page, "סף קליטה").fill("-100");
    await page.getByPlaceholder("הערה חופשית").fill("נוצר בבדיקה");

    const markersBefore = await page.locator(".leaflet-marker-icon").count();
    await page.getByRole("button", { name: "+ הוסף אתר קשר" }).click();
    await ui.expectToast(`אתר "${name}" נוסף`);

    // Back on the list, with the new card + a map marker
    await expect(ui.tabButton("אתרי קשר")).toBeVisible();
    const c = card(page, name);
    await expect(c).toBeVisible();
    await expect(c).toContainText("1800 MHz");
    await expect(c).toContainText("25מ' AGL");
    await expect(c).toContainText("-100 dBm");
    await expect(c).toContainText("נוצר בבדיקה");
    await expect(c).not.toContainText("ברירת מחדל");
    // The new site is the one being viewed → exactly its marker is shown
    await expect(page.locator(".leaflet-marker-icon")).toHaveCount(markersBefore + 1);
    const created = (await api.listSites()).find((s) => s.name === name)!;
    await expect(page.locator(`[data-testid="site-marker-${created.id}"]`)).toHaveCount(1);
  });

  test("שגיאות אימות בטופס הקמת אתר", async ({ page, loginAs, ui, guard }) => {
    guard.allow(/status of 422/);   // server-side validation response
    await loginAs("OPERATOR");
    await ui.tab("הקמת אתר");
    const submit = page.getByRole("button", { name: "+ הוסף אתר קשר" });

    // 1. no name
    await submit.click();
    await ui.expectToast("נא להזין שם אתר");

    // 2. name, no coordinates
    await page.getByPlaceholder("לדוגמה: אתר גבעה 27").fill(`E2E חסר ${uid()}`);
    await submit.click();
    await ui.expectToast("נא להזין קואורדינטות תקינות");

    // 3. latitude out of range
    await page.getByPlaceholder("31.7683").fill("95");
    await page.getByPlaceholder("35.2137").fill("35.2");
    await submit.click();
    await expect(ui.toast("נא להזין קואורדינטות תקינות")).toHaveCount(2);

    // 4. server-side validation (name longer than 128 chars) → readable message, not "[object Object]"
    await page.getByPlaceholder("31.7683").fill("31.7");
    await page.getByPlaceholder("לדוגמה: אתר גבעה 27").fill("E2E" + "א".repeat(140));
    await submit.click();
    await ui.expectToast(/128/);
    await expect(ui.toast("[object Object]")).toHaveCount(0);

    // Nothing was created; still on the form
    await expect(page.getByRole("button", { name: "+ הוסף אתר קשר" })).toBeVisible();
  });

  test("עריכת אתר קיים", async ({ page, loginAs, ui, api }) => {
    const name = `E2E לעריכה ${uid()}`;
    await api.createSite({ name, lat: 31.6, lon: 35.1, frequency: 900 });
    await loginAs("ADMIN");

    const c = card(page, name);
    await c.getByRole("button", { name: "✏️ ערוך" }).click();
    await expect(ui.tabButton("עריכת אתר")).toBeVisible();
    await expect(page.getByPlaceholder("לדוגמה: אתר גבעה 27")).toHaveValue(name);

    const newName = `${name} מעודכן`;
    await page.getByPlaceholder("לדוגמה: אתר גבעה 27").fill(newName);
    await fieldByLabel(page, "תדר סלולר").selectOption("2600");
    await fieldByLabel(page, "הספק שידור").fill("40");
    await page.getByRole("button", { name: "💾 שמור שינויים" }).click();
    await ui.expectToast(`אתר "${newName}" עודכן`);

    const updated = card(page, newName);
    await expect(updated).toBeVisible();
    await expect(updated).toContainText("2600 MHz");
    await expect(updated).toContainText("40 dBm");
    await expect(ui.tabButton("הקמת אתר")).toBeVisible();   // edit mode left

    // Persisted on the server
    const s = (await api.listSites()).find((x) => x.name === newName);
    expect(s?.frequency).toBe(2600);
    expect(s?.tx_power).toBe(40);
  });

  test("ביטול עריכה משאיר את האתר ללא שינוי", async ({ page, loginAs, ui, api }) => {
    const name = `E2E ביטול ${uid()}`;
    await api.createSite({ name, lat: 31.6, lon: 35.1 });
    await loginAs("ADMIN");
    await card(page, name).getByRole("button", { name: "✏️ ערוך" }).click();
    await page.getByPlaceholder("לדוגמה: אתר גבעה 27").fill("שם אחר");
    await page.getByRole("button", { name: "❌ ביטול עריכה" }).click();
    await expect(card(page, name)).toBeVisible();
    await ui.tab("הקמת אתר");
    await expect(page.getByPlaceholder("לדוגמה: אתר גבעה 27")).toHaveValue("");
  });

  test("מחיקת אתר", async ({ page, loginAs, ui, api }) => {
    const name = `E2E למחיקה ${uid()}`;
    const site = await api.createSite({ name, lat: 31.5, lon: 35.0 });
    await loginAs("ADMIN");
    // Site markers are shown only in context: none on open, the viewed site's marker after a click
    const marker = page.locator(`[data-testid="site-marker-${site.id}"]`);
    await expect(marker).toHaveCount(0);
    await card(page, name).click();
    await expect(marker).toHaveCount(1);
    await card(page, name).getByRole("button", { name: "✕ מחק" }).click();
    await ui.expectToast("אתר נמחק");
    await expect(card(page, name)).toHaveCount(0);
    await expect(marker).toHaveCount(0);
    await expect(page.locator('[data-testid^="site-marker-"]')).toHaveCount(0);
    expect((await api.listSites()).some((s) => s.id === site.id)).toBe(false);
  });

  test("מחיקת אתר מסירה גם את שכבות הכיסוי שלו מהמפה", async ({ page, loginAs, ui, api }) => {
    const name = `E2E מחיקה+כיסוי ${uid()}`;
    const site = await api.createSite({ name, lat: 31.772, lon: 35.212 });
    await api.analyze(site.id, JERUSALEM_BBOX);
    await loginAs("ADMIN");
    await loadFromHistory(page, ui, name);
    await expect(coverageOverlays(page)).toHaveCount(1);

    await ui.tab("אתרי קשר");
    await card(page, name).getByRole("button", { name: "✕ מחק" }).click();
    await ui.expectToast("אתר נמחק");
    // The backend deleted the site's results (cascade) → their layers must leave the map too
    await expect(coverageOverlays(page)).toHaveCount(0);
    await ui.tab("🗂 שכבות");
    await expect(page.locator("aside [class*='coverageRow']")).toHaveCount(0);
  });

  test("תגיות ברשימה: אתר משוער (מקודם מאתר סלולרי) וגובה ברירת מחדל", async ({ page, loginAs, api }) => {
    // Promote a civil cell site → analysis site with estimated params
    const cs = (await api.cellSites()).find((c) => c.status === "active" && c.operator === "CELLCOM")!;
    const res = await api.post(`/api/cell-sites/${cs.id}/promote`, {});
    expect([200, 201]).toContain(res.status());
    const promoted = await res.json();

    const plain = `E2E ללא גובה ${uid()}`;
    await api.post("/api/sites/", { name: plain, lat: 31.4, lon: 35.0, frequency: 900, tx_power: 43, rx_threshold: -90, max_radius: 30 });

    await loginAs("ADMIN");
    const p = card(page, promoted.name);
    await expect(p).toBeVisible();
    await expect(p).toContainText("⚠ משוער (אתר סלולרי)");

    const d = card(page, plain);
    await expect(d).toContainText("(ברירת מחדל)");
    await expect(d).not.toContainText("משוער (אתר סלולרי)");

    await api.deleteSite(promoted.id);
  });
});
