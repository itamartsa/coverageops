import { test, expect } from "../support/fixtures";
import { USERS } from "../support/api";

test.describe("אימות והרשאות", () => {
  test("כניסה מוצלחת דרך הטופס מציגה את המפה ואת פרטי המשתמש", async ({ page, ui }) => {
    await ui.loginViaForm(USERS.OPERATOR.username, USERS.OPERATOR.password);
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("button", { name: "יציאה" })).toBeVisible();
    await expect(page.getByText(USERS.OPERATOR.full_name)).toBeVisible();
    await expect(page.locator(".leaflet-container")).toBeVisible();
  });

  test("כניסה כושלת מציגה הודעת שגיאה ונשארת במסך הכניסה", async ({ page, ui, guard }) => {
    guard.allow(/status of 401/);   // the failed login itself
    await ui.loginViaForm(USERS.OPERATOR.username, "wrong-password");
    await expect(page.getByText("שם משתמש או סיסמה שגויים")).toBeVisible();
    await expect(page).toHaveURL(/\/login$/);
    // The typed username survives (no full page reload)
    await expect(page.getByPlaceholder("הקלד שם משתמש")).toHaveValue(USERS.OPERATOR.username);
  });

  test("יציאה מחזירה למסך הכניסה ומונעת גישה חוזרת", async ({ page, loginAs }) => {
    await loginAs("OPERATOR");
    await page.getByRole("button", { name: "יציאה" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    expect(await page.evaluate(() => localStorage.getItem("token"))).toBeNull();
  });

  test("נתיב מוגן ללא התחברות מפנה למסך הכניסה", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("button", { name: "כניסה למערכת" })).toBeVisible();
  });

  test("משתמש צופה אינו רואה כפתורי עריכה/הרצה", async ({ page, loginAs, ui }) => {
    await loginAs("VIEWER");
    await expect(page.getByText("VIEWER")).toBeVisible();
    await expect(page.getByRole("button", { name: /ניהול משתמשים/ })).toHaveCount(0);
    await expect(ui.tabButton("הקמת אתר")).toHaveCount(0);

    await ui.tab("ניתוח");
    await expect(page.getByRole("button", { name: /הרץ חישוב/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /הקם אתר חדש/ })).toHaveCount(0);

    await ui.tab("חתך רדיו");
    await expect(page.getByRole("button", { name: /הרץ חתך רדיו/ })).toHaveCount(0);

    await ui.tab("🎯 קישור נקודתי");
    await expect(page.getByRole("button", { name: /חשב קישור/ })).toHaveCount(0);

    // Viewer may not reach the admin page
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/$/);
  });

  test("מפעיל רואה כפתורי עריכה והרצה אך לא את דף הניהול", async ({ page, loginAs, ui }) => {
    await loginAs("OPERATOR");
    await expect(ui.tabButton("הקמת אתר")).toBeVisible();
    await expect(page.getByRole("button", { name: /ניהול משתמשים/ })).toHaveCount(0);
    await ui.tab("ניתוח");
    await expect(page.getByRole("button", { name: /הרץ חישוב/ })).toBeVisible();
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/$/);
  });

  test("מנהל רואה את דף הניהול", async ({ page, loginAs }) => {
    await loginAs("ADMIN");
    await page.getByRole("button", { name: /ניהול משתמשים/ }).click();
    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.getByText("ניהול משתמשים").first()).toBeVisible();
    await expect(page.getByText("@" + USERS.VIEWER.username)).toBeVisible();
    await expect(page.getByRole("heading", { name: /פרופילי מכשיר/ })).toBeVisible();
  });
});
