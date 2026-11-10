/**
 * Field-reception dashboard (/field) — runs against the synthetic demo fleet seeded at backend
 * start (scripts/start-backend.cjs → field_simulator --seed-demo).
 */
import { test, expect } from "../support/fixtures";
import { deviceByLabel, fieldAudit, fieldTab, noHorizontalScroll, openField } from "../support/field";

test.describe("קליטה בשטח — מנהל", () => {
  test("כניסה מהסרגל העליון: תג התראות, אריחי KPI, טבלת מכשירים וחזרה למפה", async ({ page, loginAs }) => {
    await loginAs("ADMIN");
    const nav = page.getByTestId("nav-field");
    await expect(nav.getByTestId("field-alert-badge")).toHaveText(/^\d+$/);
    await nav.click();
    await expect(page).toHaveURL(/\/field/);
    const kpis = page.getByTestId("kpi-tiles");
    await expect(kpis).toContainText("RSRP חציון · LTE");
    await expect(kpis).toContainText("SS-RSRP חציון · 5G NR");
    await expect(kpis).toContainText("התראות פתוחות");
    await expect(page.getByTestId("device-table").locator("tr[data-device-row]").first()).toBeVisible();
    await expect(page.getByTestId("fleet-map").locator(".fdm-b").first()).toBeVisible();
    await expect(page.getByTestId("fleet-map-legend")).toContainText("RSRP LTE (dBm)");
    // Back to the coverage map
    await page.getByRole("button", { name: "מפת כיסוי" }).click();
    await expect(page).toHaveURL(/\/$/);
  });

  test("פתיחת מכשיר, פקודה 'דגימה עכשיו' (כולל 409 על פקודה כפולה) ומסלול שנרשם ביומן", async ({ page, loginAs, api, guard }) => {
    const dev = await deviceByLabel(api, "דמו · ניידת 1 · כביש 1");
    await loginAs("ADMIN");
    await openField(page, "devices");
    await page.getByTestId("device-table").getByRole("button", { name: "דמו · ניידת 1 · כביש 1" }).click();
    await expect(page).toHaveURL(new RegExp(`device=${dev.id}`));
    const drawer = page.getByTestId("device-drawer");
    await expect(drawer.getByRole("heading", { name: "דמו · ניידת 1 · כביש 1" })).toBeVisible();
    await expect(drawer.getByTestId("device-ts-chart")).toBeVisible();

    const cmds = drawer.getByTestId("device-commands");
    await cmds.getByRole("button", { name: /דגימה עכשיו/ }).click();
    await expect(cmds.locator("li[data-command-status='queued']").first()).toContainText("נשלחה · ממתינה למכשיר");
    // A second identical command is refused by the server (409 command_pending) — shown, not duplicated
    guard.allow(/409/);
    await cmds.getByRole("button", { name: /דגימה עכשיו/ }).click();
    await expect(cmds.getByRole("status")).toContainText("כבר קיימת פקודה פתוחה");
    const list = await (await api.get(`/api/field/devices/${dev.id}/commands`)).json();
    expect(list.filter((c: any) => c.type === "sample_now" && c.status === "queued")).toHaveLength(1);
    expect((await fieldAudit(api, "FIELD_COMMAND_ISSUED")).some((e) => e.detail?.device_id === dev.id)).toBe(true);

    // Track: notice shown, request audit-logged on the server
    await expect(drawer.getByTestId("track-audit-note")).toContainText("נרשמים ביומן הפעילות");
    const trackRes = page.waitForResponse((r) => r.url().includes(`/devices/${dev.id}/track`));
    await drawer.getByRole("button", { name: /הצג מסלול/ }).click();
    expect((await trackRes).status()).toBe(200);
    await expect(drawer).toContainText(/מסלול: \d+ נקודות/);
    expect((await fieldAudit(api, "FIELD_TRACK_VIEW")).some((e) => e.detail?.device_id === dev.id)).toBe(true);
  });

  test("מכשיר אישי מושהה: כפתורי הפקודות מושבתים עם הסבר", async ({ page, loginAs, api }) => {
    const dev = await deviceByLabel(api, "דמו · אישי · מושהה");
    await loginAs("ADMIN");
    await openField(page, "devices", `&device=${dev.id}`);
    const cmds = page.getByTestId("device-commands");
    await expect(cmds.getByRole("button", { name: /דגימה עכשיו/ })).toBeDisabled();
    await expect(cmds).toContainText("מכשיר אישי מושהה — פקודות לא מבוצעות");
    // Paused devices are listed as paused, not offline
    await expect(page.getByTestId("device-table").locator(`tr[data-device-row='${dev.id}']`)).toContainText("מושהה ע״י המשתמש");
  });

  test("יצירת קוד צירוף מציגה את הקוד ואת ה-QR פעם אחת", async ({ page, loginAs }) => {
    await loginAs("ADMIN");
    await openField(page, "admin");
    await page.getByRole("button", { name: "+ קוד צירוף" }).click();
    const form = page.getByTestId("enroll-form");
    await form.getByRole("radio", { name: /ORG — מכשיר ארגוני/ }).check();
    await form.getByLabel(/תווית/).fill("E2E קוד");
    await page.getByRole("button", { name: "צור קוד" }).click();
    const result = page.getByTestId("enroll-result");
    await expect(result.getByTestId("enroll-code")).toHaveText(/^[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{2}$/);
    const code = (await result.getByTestId("enroll-code").innerText()).trim();
    const qr = result.getByTestId("enroll-qr");
    await expect(qr).toBeVisible();
    expect(await qr.getAttribute("src")).toMatch(/^data:image\/svg\+xml/);
    expect(await qr.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
    await expect(result).toContainText("הקוד מוצג פעם אחת בלבד");
    await page.getByRole("button", { name: "סגור", exact: true }).last().click();
    // Listed by its hint only — the plain code is never shown again
    const codes = page.getByTestId("enrollment-codes");
    await expect(codes).toContainText(code.replace(/-/g, "").slice(-4));
    await expect(codes).toContainText("E2E קוד");
    await expect(page.getByText(code, { exact: true })).toHaveCount(0);
  });

  test("לוח דרישות מערכת ליחידות הקצה: Android 11 ופריטי 'לאימות' מסומנים", async ({ page, loginAs }) => {
    await loginAs("OPERATOR");
    await openField(page);
    await page.getByTestId("requirements-btn").click();
    const panel = page.getByTestId("requirements-panel");
    await expect(panel).toContainText("Android 11 (API 30)");
    const toVerify = panel.locator("[data-req-status='לאימות']");
    expect(await toVerify.count()).toBeGreaterThan(0);
    await expect(toVerify.first()).toContainText("לאימות");
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
  });

  test("אישור וסגירה של התראה", async ({ page, loginAs, api }) => {
    const open = await (await api.get("/api/field/alerts?status=open")).json();
    expect(open.length).toBeGreaterThan(1);
    await loginAs("OPERATOR");
    await openField(page, "alerts");
    const list = page.getByTestId("alerts-list");
    const row = list.locator(`li[data-alert-id='${open[0].id}']`);
    await row.getByRole("button", { name: "אשר" }).click();
    await expect(row).toHaveAttribute("data-alert-status", "acked");
    await expect(row).toContainText("אושרה");
    const row2 = list.locator(`li[data-alert-id='${open[1].id}']`);
    await row2.getByRole("button", { name: "סגור" }).click();
    await expect(row2).toHaveCount(0);          // default filter = open + acked
    const after = await (await api.get(`/api/field/alerts?status=resolved`)).json();
    expect(after.some((a: any) => a.id === open[1].id)).toBe(true);
  });

  test("390px: אין גלילה אופקית בלשוניות הדאשבורד", async ({ page, loginAs }) => {
    await loginAs("ADMIN");
    await page.setViewportSize({ width: 390, height: 860 });
    for (const tab of ["overview", "devices", "analysis", "alerts", "admin"] as const) {
      await openField(page, tab);
      await page.waitForTimeout(500);
      expect(await noHorizontalScroll(page), `tab ${tab}`).toBe(true);
    }
  });
});

test.describe("קליטה בשטח — צופה", () => {
  test("צופה רואה נתונים מצרפיים בלבד: אין טבלת מכשירים, התראות או סמני מכשיר", async ({ page, loginAs }) => {
    const deviceCalls: string[] = [];
    page.on("request", (r) => {
      if (/\/api\/field\/(devices|alerts|audit|enrollment-codes)/.test(r.url())) deviceCalls.push(r.url());
    });
    await loginAs("VIEWER");
    await expect(page.getByTestId("field-alert-badge")).toHaveCount(0);
    await page.getByTestId("nav-field").click();
    await expect(page.getByTestId("kpi-tiles")).toBeVisible();
    await expect(page.getByTestId("viewer-note")).toContainText("תצוגה מצרפית בלבד");
    await expect(page.getByTestId("fleet-map-card")).toContainText("ללא מיקומי מכשירים");
    await expect(page.getByTestId("device-table")).toHaveCount(0);
    await expect(page.getByTestId("alerts-preview")).toHaveCount(0);
    await expect(page.getByTestId("kpi-tiles")).not.toContainText("התראות פתוחות");
    await expect(page.locator(".fdm-b")).toHaveCount(0);
    await expect(fieldTab(page, "מכשירים")).toHaveCount(0);
    await expect(fieldTab(page, "התראות")).toHaveCount(0);
    await expect(fieldTab(page, "ניתוח")).toBeVisible();

    // Aggregates work for VIEWER
    await fieldTab(page, "ניתוח").click();
    await expect(page.getByTestId("tier-stack-chart")).toBeVisible();
    await expect(page.getByTestId("dead-zones-list")).toBeVisible();

    // A device-level URL shows the one-line reason, never an error
    await openField(page, "devices");
    await expect(page.getByTestId("viewer-forbidden")).toContainText("זמינים למנהל ולמפעיל");
    expect(deviceCalls, "VIEWER must not request device-level endpoints").toEqual([]);
  });
});

test.describe("קליטה בשטח — שכבה במפה הראשית", () => {
  test("הפעלה וכיבוי של שכבת 'מדידות שטח' עם מקרא RSRP נפרד", async ({ page, loginAs, ui }) => {
    await loginAs("OPERATOR");
    await ui.tab("🗂 שכבות");
    const row = page.locator("aside [class*='layerRow']").filter({ hasText: "מדידות שטח" });
    await expect(row).toBeVisible();
    const heat = page.waitForResponse((r) => r.url().includes("/api/field/heatmap"));
    await row.getByTitle("הצג שכבה").click();
    expect((await heat).status()).toBe(200);
    const legend = page.getByTestId("field-heat-legend");
    await expect(legend).toContainText("מדידות שטח");
    await expect(legend).toContainText("RSRP LTE dBm");
    await expect(legend).toContainText("שונה ממקרא החיזוי (RSSI)");
    await expect(page.getByTestId("field-heat-canvas")).toHaveCount(1);
    await expect(page.getByTestId("field-heat-info")).toContainText("מקרא מדידות · RSRP dBm");
    // Measure switch → NR request
    const nr = page.waitForResponse((r) => r.url().includes("measure=nr_ss_rsrp"));
    await page.getByTestId("field-heat-info").getByLabel("מדד").selectOption("nr_ss_rsrp");
    expect((await nr).status()).toBe(200);
    await expect(legend).toContainText("SS-RSRP NR dBm");

    await row.getByTitle("הסתר שכבה").click();
    await expect(legend).toHaveCount(0);
    await expect(page.getByTestId("field-heat-canvas")).toHaveCount(0);
  });
});
