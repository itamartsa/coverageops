import { expect, type Page } from "@playwright/test";
import type { Ui } from "./fixtures";
import { card } from "./form";

/** History tab → "⬆ טען למפה" on the newest result of `siteName`. */
export async function loadFromHistory(page: Page, ui: Ui, siteName: string) {
  await ui.tab("היסטוריה");
  const c = card(page, siteName).first();
  await expect(c).toBeVisible();
  await c.getByRole("button", { name: "⬆ טען למפה" }).click();
  await ui.expectToast(/נטענה למפה/);
}
