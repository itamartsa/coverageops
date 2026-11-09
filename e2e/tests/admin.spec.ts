import { test, expect } from "../support/fixtures";
import { API_URL, USERS, uid } from "../support/api";
import { fieldByLabel } from "../support/form";

test.describe("ניהול (מנהל)", () => {
  test.beforeEach(async ({ page, loginAs }) => {
    await loginAs("ADMIN");
    await page.goto("/admin");
    await expect(page.getByText("ניהול משתמשים").first()).toBeVisible();
  });

  const profilesSection = (page: import("@playwright/test").Page) =>
    page.locator("section").filter({ has: page.getByRole("heading", { name: /פרופילי מכשיר/ }) });
  const usersTable = (page: import("@playwright/test").Page) => page.locator("main table").first();
  const modal = (page: import("@playwright/test").Page) => page.locator("[class*='_modal_']");

  test("פרופילי מכשיר: יצירה (כולל אימות), עריכה ומחיקה", async ({ page, api }) => {
    const key = `e2e_${uid()}`.toLowerCase().replace(/[^a-z0-9_]/g, "");
    const sec = profilesSection(page);
    await sec.getByRole("button", { name: "➕ פרופיל חדש" }).click();
    const m = modal(page);
    await expect(m.getByRole("heading", { name: "➕ פרופיל מכשיר חדש" })).toBeVisible();

    await m.getByPlaceholder("טלפון נייד").fill("מכשיר E2E");
    await m.getByPlaceholder("handheld").fill("Bad Key");
    await m.getByRole("button", { name: "הוסף פרופיל" }).click();
    await expect(m.getByText("מפתח: אותיות לטיניות קטנות, ספרות, _ או - בלבד")).toBeVisible();

    await m.getByPlaceholder("handheld").fill(key);
    await fieldByLabel(m, "רגישות (dBm)").fill("-20");
    await m.getByRole("button", { name: "הוסף פרופיל" }).click();
    await expect(m.getByText("רגישות: −150 עד −30 dBm")).toBeVisible();

    await fieldByLabel(m, "גובה (מ' AGL)").fill("3");
    await fieldByLabel(m, "רגישות (dBm)").fill("-98");
    await fieldByLabel(m, "רווח (dBi)").fill("2.5");
    await fieldByLabel(m, "תיאור").fill("נוצר בבדיקה");
    await m.getByRole("button", { name: "הוסף פרופיל" }).click();
    await expect(m).toHaveCount(0);

    const row = sec.locator("tr").filter({ hasText: key });
    await expect(row).toContainText("מכשיר E2E");
    await expect(row).toContainText("-98");
    await expect(row).toContainText("2.5");
    await expect(row).toContainText("נוצר בבדיקה");
    expect((await api.deviceProfiles()).some((p) => p.key === key)).toBe(true);

    // Edit
    await row.getByTitle("ערוך").click();
    await expect(m.getByRole("heading", { name: /עריכת פרופיל – מכשיר E2E/ })).toBeVisible();
    await m.getByPlaceholder("טלפון נייד").fill("מכשיר E2E מעודכן");
    await m.getByRole("button", { name: "שמור שינויים" }).click();
    await expect(m).toHaveCount(0);
    await expect(sec.locator("tr").filter({ hasText: key })).toContainText("מכשיר E2E מעודכן");

    // Delete (with confirm)
    await sec.locator("tr").filter({ hasText: key }).getByTitle("מחק").click();
    await sec.locator("tr").filter({ hasText: key }).getByRole("button", { name: "מחק", exact: true }).click();
    await expect(sec.locator("tr").filter({ hasText: key })).toHaveCount(0);
    expect((await api.deviceProfiles()).some((p) => p.key === key)).toBe(false);
  });

  test("לא ניתן למחוק את פרופיל ברירת המחדל — הודעת השרת מוצגת", async ({ page, api, guard }) => {
    guard.allow(/status of 400/);
    const def = (await api.deviceProfiles()).find((p) => p.is_default)!;
    const sec = profilesSection(page);
    const row = sec.locator("tr").filter({ hasText: def.key });
    await expect(row).toContainText("★ ברירת מחדל");
    await row.getByTitle("מחק").click();
    await row.getByRole("button", { name: "מחק", exact: true }).click();
    await expect(sec.getByText("לא ניתן למחוק את פרופיל ברירת המחדל")).toBeVisible();
    await expect(sec.locator("tr").filter({ hasText: def.key })).toHaveCount(1);
  });

  test("משתמשים: יצירה, כפילות, עריכה, השהיה ומחיקה", async ({ page, request, guard }) => {
    guard.allow(/status of 409/);   // duplicate username
    const username = `e2e_${uid()}`;
    const table = usersTable(page);

    await page.getByRole("button", { name: "➕ משתמש חדש" }).click();
    const m = modal(page);
    await m.getByRole("button", { name: "הוסף משתמש" }).click();
    await expect(m.getByText("יש למלא את כל השדות")).toBeVisible();

    await m.getByPlaceholder("ישראל ישראלי").fill("משתמש בדיקה");
    await m.getByPlaceholder("israel_i").fill(username);
    await m.getByPlaceholder("לפחות 6 תווים").fill("Test1234!");
    await fieldByLabel(m, "תפקיד").selectOption("VIEWER");
    await m.getByRole("button", { name: "הוסף משתמש" }).click();
    await expect(m).toHaveCount(0);

    const row = table.locator("tr").filter({ hasText: `@${username}` });
    await expect(row).toContainText("משתמש בדיקה");
    await expect(row).toContainText("צופה");
    await expect(row).toContainText("✅ פעיל");

    // Duplicate username → server message in the modal
    await page.getByRole("button", { name: "➕ משתמש חדש" }).click();
    await m.getByPlaceholder("ישראל ישראלי").fill("כפול");
    await m.getByPlaceholder("israel_i").fill(username);
    await m.getByPlaceholder("לפחות 6 תווים").fill("Test1234!");
    await m.getByRole("button", { name: "הוסף משתמש" }).click();
    await expect(m.getByText("שם משתמש כבר קיים")).toBeVisible();
    await m.getByRole("button", { name: "ביטול" }).click();

    // Search filter
    await page.getByPlaceholder("🔍 חיפוש לפי שם או שם משתמש...").fill(username);
    await expect(table.locator("tbody tr")).toHaveCount(1);
    await page.getByPlaceholder("🔍 חיפוש לפי שם או שם משתמש...").fill("");

    // Edit: name + role
    await row.getByTitle("ערוך").click();
    await expect(m.getByRole("heading", { name: `✏️ עריכת משתמש – ${username}` })).toBeVisible();
    await fieldByLabel(m, "שם מלא").fill("משתמש בדיקה מעודכן");
    await fieldByLabel(m, "תפקיד").selectOption("OPERATOR");
    await m.getByRole("button", { name: "שמור שינויים" }).click();
    await expect(m).toHaveCount(0);
    await expect(row).toContainText("משתמש בדיקה מעודכן");
    await expect(row).toContainText("מפעיל");

    // Suspend → login refused
    await row.getByRole("button", { name: "✅ פעיל" }).click();
    await expect(row).toContainText("🔒 מושהה");
    const login = await request.post(`${API_URL}/api/auth/login`, { form: { username, password: "Test1234!" } });
    expect(login.status()).toBe(403);

    // Delete (with confirm)
    await row.getByTitle("מחק").click();
    await row.getByRole("button", { name: "מחק", exact: true }).click();
    await expect(table.locator("tr").filter({ hasText: `@${username}` })).toHaveCount(0);

    // An admin cannot delete themselves (no delete button on the own row)
    await expect(table.locator("tr").filter({ hasText: `@${USERS.ADMIN.username}` }).getByTitle("מחק")).toHaveCount(0);
  });

  test("חזרה לאפליקציה מדף הניהול", async ({ page }) => {
    await page.getByRole("button", { name: "← חזרה לאפליקציה" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator(".leaflet-container")).toBeVisible();
  });
});
