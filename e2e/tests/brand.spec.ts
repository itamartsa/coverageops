/**
 * COVIRADYX branding: name + logo on login, every top bar and the browser tab;
 * the default-credentials hint is gone from the public login page.
 */
import { test, expect } from "../support/fixtures";

test.describe("מיתוג COVIRADYX", () => {
  test("מסך הכניסה מציג את הלוגו ואינו חושף סיסמת ברירת מחדל", async ({ page }) => {
    await page.goto("/login");
    await expect(page).toHaveTitle(/COVIRADYX/);
    const logo = page.getByRole("img", { name: /COVIRADYX/ });
    await expect(logo).toBeVisible();
    expect(await logo.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0)).toBe(true);
    await expect(page.getByText(/ברירת מחדל:/)).toHaveCount(0);
    await expect(page.getByText("12345678")).toHaveCount(0);
    await expect(page.getByText(/CoverageOps/)).toHaveCount(0);
    // Labels are wired to their inputs
    await expect(page.getByLabel("שם משתמש")).toBeVisible();
    await expect(page.getByLabel("סיסמה")).toBeVisible();
    const icon = await page.locator("link[rel=icon]").getAttribute("href");
    expect((await page.request.get(icon!)).ok()).toBe(true);
  });

  for (const path of ["/", "/field", "/admin"]) {
    test(`הסרגל העליון מציג את סמל המערכת — ${path}`, async ({ page, loginAs }) => {
      await loginAs("ADMIN");
      await page.goto(path);
      const mark = page.getByTestId("brand-mark").first();
      await expect(mark).toBeVisible();
      await expect(mark).toHaveAccessibleName("COVIRADYX");
      expect(await mark.locator("img").evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0)).toBe(true);
      await expect(page.getByText(/CoverageOps/)).toHaveCount(0);
    });
  }
});
