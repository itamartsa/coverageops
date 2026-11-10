/**
 * Analysis tab: the methodology / per-level panel opens either at the top (ⓘ in the header)
 * or right below the colour legend ("ⓘ פירוט מלא…") — one instance at a time.
 */
import type { Page } from "@playwright/test";
import { test, expect } from "../support/fixtures";

const BOTTOM_OPEN_LABEL = "ⓘ פירוט מלא של כל רמה וכיצד מחושב הכיסוי ▾";
const BOTTOM_CLOSE_LABEL = "▴ הסתר פירוט";

const infoPanels = (page: Page) => page.getByTestId("coverage-info-panel");
const topInfoButton = (page: Page) => page.getByRole("button", { name: "מידע על שיטת החישוב" });
const bottomInfoButton = (page: Page) => page.locator("aside button[class*='infoMoreBtn']");
const legendBox = (page: Page) => page.locator("aside [class*='_legend_']");
const stepOne = (page: Page) => page.locator("aside [class*='stepCard']").filter({ hasText: "1. בחירת מוקדי קשר" });

const LEVEL_LABELS = ["מצוין", "טוב", "בינוני", "חלש", "שולי", "אין"];

async function top(l: import("@playwright/test").Locator) {
  const b = await l.boundingBox();
  expect(b, "element has a layout box").not.toBeNull();
  return b!;
}

test.describe("לשונית ניתוח: פירוט רמות ושיטת החישוב", () => {
  test.beforeEach(async ({ loginAs, ui }) => {
    await loginAs("ADMIN");
    await ui.tab("ניתוח");
  });

  test("הכפתור התחתון פותח את הפירוט מתחת לסולם הצבעים, בתוך התצוגה", async ({ page }) => {
    await expect(page.getByText("לחץ ⓘ למעלה")).toHaveCount(0);       // the old static note is gone
    const btn = bottomInfoButton(page);
    await expect(btn).toHaveText(BOTTOM_OPEN_LABEL);
    await expect(btn).toHaveAttribute("aria-expanded", "false");
    await expect(infoPanels(page)).toHaveCount(0);

    await btn.click();
    const panel = infoPanels(page);
    await expect(panel).toHaveCount(1);
    await expect(panel).toHaveAttribute("data-position", "bottom");
    await expect(btn).toHaveText(BOTTOM_CLOSE_LABEL);
    await expect(btn).toHaveAttribute("aria-expanded", "true");

    // Scrolled into view (smooth): its title is visible without the user scrolling
    await expect(panel).toBeInViewport();
    await expect(panel.getByText("כיצד מחושב ניתוח הכיסוי?")).toBeInViewport();
    // Located right below the legend and the toggle, after the wizard steps — measured in one
    // frame, since the sidebar may still be scrolling
    const pos = await page.evaluate(() => {
      const r = (sel: string) => document.querySelector(sel)!.getBoundingClientRect();
      return {
        legendBottom: r("aside [class*='_legend_']").bottom,
        btnBottom: r("aside button[class*='infoMoreBtn']").bottom,
        stepTop: r("aside [class*='stepCard']").top,
        panelTop: r("[data-testid='coverage-info-panel']").top,
      };
    });
    expect(pos.panelTop).toBeGreaterThanOrEqual(pos.btnBottom);
    expect(pos.btnBottom).toBeGreaterThan(pos.legendBottom);
    expect(pos.panelTop).toBeGreaterThan(pos.stepTop);
    await expect(legendBox(page)).toHaveCount(1);

    // Same content as the top panel: methodology + full per-level list
    await expect(panel).toContainText("מודל הפצה");
    await expect(panel).toContainText("סולם עוצמות האות — פירוט");
    const rows = panel.locator("[class*='infoLegendRow']");
    await expect(rows).toHaveCount(LEVEL_LABELS.length);
    for (const [i, label] of LEVEL_LABELS.entries()) {
      await expect(rows.nth(i)).toContainText(label);
    }
    await expect(panel).toContainText("קליטה מלאה — שיחה/נתונים ברמה הגבוהה ביותר");

    // Toggling the same control closes it
    await btn.click();
    await expect(infoPanels(page)).toHaveCount(0);
    await expect(btn).toHaveText(BOTTOM_OPEN_LABEL);
  });

  test("ⓘ העליון פותח למעלה; פאנל אחד בלבד בכל רגע; ✕ סוגר", async ({ page }) => {
    await topInfoButton(page).click();
    await expect(infoPanels(page)).toHaveCount(1);
    await expect(infoPanels(page)).toHaveAttribute("data-position", "top");
    await expect(topInfoButton(page)).toHaveAttribute("aria-expanded", "true");
    expect((await top(infoPanels(page))).y).toBeLessThan((await top(stepOne(page))).y);
    await expect(bottomInfoButton(page)).toHaveText(BOTTOM_OPEN_LABEL);

    // Bottom control moves it to the bottom — still a single panel
    await bottomInfoButton(page).click();
    await expect(infoPanels(page)).toHaveCount(1);
    await expect(infoPanels(page)).toHaveAttribute("data-position", "bottom");
    await expect(topInfoButton(page)).toHaveAttribute("aria-expanded", "false");

    // ...and the top ⓘ moves it back
    await topInfoButton(page).click();
    await expect(infoPanels(page)).toHaveCount(1);
    await expect(infoPanels(page)).toHaveAttribute("data-position", "top");
    await expect(bottomInfoButton(page)).toHaveText(BOTTOM_OPEN_LABEL);

    // ✕ closes (top)
    await infoPanels(page).getByRole("button", { name: "סגור פירוט" }).click();
    await expect(infoPanels(page)).toHaveCount(0);

    // ✕ closes (bottom) and restores the label
    await bottomInfoButton(page).click();
    await expect(infoPanels(page)).toHaveAttribute("data-position", "bottom");
    await infoPanels(page).getByRole("button", { name: "סגור פירוט" }).click();
    await expect(infoPanels(page)).toHaveCount(0);
    await expect(bottomInfoButton(page)).toHaveText(BOTTOM_OPEN_LABEL);

    // Top ⓘ toggles closed too
    await topInfoButton(page).click();
    await expect(infoPanels(page)).toHaveCount(1);
    await topInfoButton(page).click();
    await expect(infoPanels(page)).toHaveCount(0);
  });

  test("מקלדת: Enter פותח וסוגר את הפירוט התחתון", async ({ page }) => {
    const btn = bottomInfoButton(page);
    await btn.focus();
    await expect(btn).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(infoPanels(page)).toHaveCount(1);
    await expect(infoPanels(page)).toHaveAttribute("data-position", "bottom");
    await expect(btn).toBeFocused();
    await expect(btn).toHaveText(BOTTOM_CLOSE_LABEL);
    await page.keyboard.press("Enter");
    await expect(infoPanels(page)).toHaveCount(0);
    await expect(btn).toHaveText(BOTTOM_OPEN_LABEL);
    // Space works as for any button
    await page.keyboard.press("Space");
    await expect(infoPanels(page)).toHaveCount(1);
  });
});
