/**
 * Shared test fixtures:
 *  - `guard`  (auto): fails the test on uncaught page errors, console errors and API responses ≥ 500.
 *                     Blocked external traffic (map tiles, DEM tiles…) is allow-listed.
 *  - `api`          : REST client logged in as ADMIN, for fast data seeding.
 *  - `loginAs(role)`: logs in through the API and opens the app with that session (no UI).
 *  - `ui`           : small page-object helpers (sidebar tabs, toasts, map).
 */
import { test as base, expect, type Page, type ConsoleMessage } from "@playwright/test";
import { Api, API_URL, USERS, apiLogin, type Role } from "./api";
import { waitForMap } from "./map";

const APP_HOSTS = [new URL(API_URL).host, "localhost:3000", "127.0.0.1:3000"];

/** Console errors that are expected in this sandbox (outbound internet is blocked). */
const DEFAULT_CONSOLE_ALLOW: RegExp[] = [
  /net::ERR_/,                                  // blocked tile / DEM / CDN requests
  /ERR_TUNNEL_CONNECTION_FAILED|ERR_CONNECTION/,
];

function isExternal(url: string | undefined): boolean {
  if (!url) return false;
  try {
    const u = new URL(url);
    return !APP_HOSTS.includes(u.host) && !u.host.startsWith("localhost") && u.protocol.startsWith("http");
  } catch {
    return false;
  }
}

export interface Guard {
  /** Allow errors matching `re` in this test (e.g. an expected 401/400). Applied when the test ends. */
  allow(re: RegExp): void;
  /** Everything collected so far (before the allow-list is applied). */
  problems(): string[];
}

export type Tab = "אתרי קשר" | "הקמת אתר" | "עריכת אתר" | "ניתוח" | "חתך רדיו" | "🎯 קישור נקודתי" | "🗂 שכבות" | "היסטוריה";

export class Ui {
  constructor(readonly page: Page) {}

  sidebar() { return this.page.locator("aside"); }

  tabButton(name: Tab) {
    return this.page.locator("aside nav").getByRole("button", { name, exact: true });
  }

  async tab(name: Tab) {
    await this.tabButton(name).click();
  }

  /** Toast notification container text. */
  toast(text: string | RegExp) {
    return this.page.locator("[class*='toast']").filter({ hasText: text });
  }

  async expectToast(text: string | RegExp) {
    await expect(this.toast(text).first()).toBeVisible();
  }

  /** UI login through the login form. */
  async loginViaForm(username: string, password: string) {
    await this.page.goto("/login");
    await this.page.getByPlaceholder("הקלד שם משתמש").fill(username);
    await this.page.getByPlaceholder("••••••••").fill(password);
    await this.page.getByRole("button", { name: "כניסה למערכת" }).click();
  }
}

type Fixtures = {
  guard: Guard;
  api: Api;
  loginAs: (role: Role) => Promise<void>;
  ui: Ui;
};

export const test = base.extend<Fixtures>({
  guard: [async ({ page }, use, testInfo) => {
    const allow = [...DEFAULT_CONSOLE_ALLOW];
    const problems: string[] = [];
    const allowed = (text: string) => allow.some((re) => re.test(text));

    page.on("pageerror", (err) => {
      problems.push(`pageerror: ${err.message}\n${err.stack ?? ""}`);
    });
    page.on("console", (msg: ConsoleMessage) => {
      if (msg.type() !== "error") return;
      const url = msg.location()?.url;
      if (isExternal(url)) return;                       // blocked basemap / external resources
      problems.push(`console.error: ${msg.text()}${url ? ` (${url})` : ""}`);
    });
    page.on("response", (res) => {
      if (res.status() >= 500 && res.url().startsWith(API_URL)) {
        problems.push(`HTTP ${res.status()} ${res.request().method()} ${res.url()}`);
      }
    });

    await use({ allow: (re) => allow.push(re), problems: () => [...problems] });

    const remaining = problems.filter((p) => !allowed(p));
    if (remaining.length) {
      await testInfo.attach("page-problems.txt", { body: remaining.join("\n\n"), contentType: "text/plain" });
    }
    expect(remaining, "uncaught page errors / console errors / API 5xx during the test").toEqual([]);
  }, { auto: true }],

  api: async ({}, use) => {
    const api = await Api.as("ADMIN");
    await use(api);
    await api.dispose();
  },

  loginAs: async ({ page, request }, use) => {
    await use(async (role: Role) => {
      const u = USERS[role];
      const auth = await apiLogin(request, u.username, u.password);
      await page.goto("/login");
      await page.evaluate((a) => {
        localStorage.setItem("token", a.access_token);
        localStorage.setItem("coverageops-storage", JSON.stringify({
          state: {
            token: a.access_token,
            user: { id: a.user_id, username: a.username, full_name: a.full_name, role: a.role },
          },
          version: 0,
        }));
      }, auth);
      await page.goto("/");
      await waitForMap(page);
    });
  },

  ui: async ({ page }, use) => {
    await use(new Ui(page));
  },
});

export { expect };
