import type { Locator, Page } from "@playwright/test";

/**
 * The app's forms use <label> + sibling <input>/<select> without htmlFor,
 * so getByLabel() can't resolve them — find the control that follows the label.
 */
export function fieldByLabel(scope: Page | Locator, label: string): Locator {
  return scope.locator(
    `xpath=.//label[contains(normalize-space(.), "${label}")]/following-sibling::*[self::input or self::select or self::textarea][1]`,
  );
}

/** Sidebar card (sites list / history) containing `text`. */
export function card(page: Page, text: string | RegExp): Locator {
  return page.locator("aside [class*='_card_']").filter({ hasText: text });
}

/** Sites-list "🎯 מיקוד" button of the card containing `text` (aria-label is constant; state = aria-pressed). */
export function focusButton(page: Page, text: string | RegExp): Locator {
  return card(page, text).getByRole("button", { name: "מיקוד על האתר במפה" });
}
