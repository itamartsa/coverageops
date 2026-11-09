/**
 * Cross-cutting smoke: every tab renders for every role without page errors, console errors
 * or API 5xx (asserted by the auto `guard` fixture), and the guard itself really catches errors.
 */
import { test, expect } from "../support/fixtures";
import type { Role } from "../support/api";
import type { Tab } from "../support/fixtures";

const TABS: Tab[] = ["אתרי קשר", "הקמת אתר", "ניתוח", "חתך רדיו", "🎯 קישור נקודתי", "🗂 שכבות", "היסטוריה"];

for (const role of ["ADMIN", "OPERATOR", "VIEWER"] as Role[]) {
  test(`כל הלשוניות נטענות ללא שגיאות — ${role}`, async ({ page, loginAs, ui }) => {
    await loginAs(role);
    for (const t of TABS) {
      if (role === "VIEWER" && t === "הקמת אתר") continue;
      await ui.tab(t);
      await expect(ui.tabButton(t)).toHaveClass(/active/);
      await expect(page.locator("aside [class*='content']")).not.toBeEmpty();
    }
    // Hover over the map → coordinate read-out + (debounced) point-elevation request
    const box = (await page.locator(".leaflet-container").boundingBox())!;
    const elev = page.waitForResponse((r) => r.url().includes("/api/coverage/point-elevation"));
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.move(box.x + box.width / 2 + 10, box.y + box.height / 2 + 5);
    await expect(page.locator("#coord-display")).toContainText(/N \d+\.\d{5}°/);
    expect((await elev).status()).toBe(200);
  });
}

test("גלאי השגיאות תופס שגיאת דף", async ({ page, loginAs, guard }) => {
  await loginAs("OPERATOR");
  await page.evaluate(() => setTimeout(() => { throw new Error("e2e-guard-self-test"); }, 0));
  await expect.poll(() => guard.problems().some((p) => p.includes("e2e-guard-self-test"))).toBe(true);
  guard.allow(/e2e-guard-self-test/);   // caught as expected — don't fail this test for it
});
