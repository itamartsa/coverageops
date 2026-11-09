import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

// eslint-disable-next-line @typescript-eslint/no-var-requires
const env = require("./scripts/env.cjs") as typeof import("./scripts/env.cjs");

/**
 * CoverageOps end-to-end suite.
 * Starts the FastAPI backend (fresh SQLite DB in the OS temp folder) and the Vite dev
 * server, then drives the real UI in Chromium. One worker: all tests share one DB.
 */
export default defineConfig({
  testDir: "./tests",
  outputDir: "./test-results",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  globalSetup: "./global-setup.ts",

  use: {
    baseURL: env.WEB_URL,
    locale: "he-IL",
    timezoneId: "Asia/Jerusalem",
    viewport: { width: 1440, height: 900 },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off",
  },

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
        // Linux without a UTF-8 locale: Chromium replaces non-ASCII download names
        // (e.g. "חתך_רדיו_….docx") with "download" — give the browser a UTF-8 locale.
        launchOptions: process.platform === "linux" && !/utf-?8/i.test(process.env.LC_ALL || process.env.LANG || "")
          ? { env: { ...process.env, LANG: "C.UTF-8", LC_ALL: "C.UTF-8" } as Record<string, string> }
          : {},
      },
    },
  ],

  webServer: [
    {
      // Wipes + seeds the temp SQLite DB, then runs uvicorn (see scripts/start-backend.cjs)
      command: "node scripts/start-backend.cjs",
      cwd: __dirname,
      url: `http://127.0.0.1:${env.API_PORT}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npx vite --port ${env.WEB_PORT} --strictPort`,
      cwd: path.join(__dirname, "..", "frontend"),
      url: env.WEB_URL,
      env: { VITE_API_URL: env.API_URL },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
