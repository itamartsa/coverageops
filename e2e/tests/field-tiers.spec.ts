/**
 * The frontend tier module (frontend/src/lib/fieldTiers.ts) must agree with the server's single
 * source of truth: GET /api/field/meta/tiers and the shared vectors file
 * backend/tests/fixtures/field_tier_vectors.json. There is no frontend unit-test runner, so the
 * module is imported in the page through the Vite dev server (dev builds only) — spec 10.4.
 */
import fs from "node:fs";
import path from "node:path";
import { test, expect } from "../support/fixtures";

const VECTORS = JSON.parse(fs.readFileSync(
  path.join(__dirname, "..", "..", "backend", "tests", "fixtures", "field_tier_vectors.json"), "utf-8"));

test("fieldTiers.ts מתאים ל-/meta/tiers ולכל וקטורי הרמות המשותפים", async ({ page, loginAs, api }) => {
  await loginAs("VIEWER");
  const meta = await (await api.get("/api/field/meta/tiers")).json();

  const result = await page.evaluate(async (vectors: any[]) => {
    const m = await import("/src/lib/fieldTiers.ts" as string);
    return {
      tiers: Object.values(m.TIERS).map((t: any) => ({ tier: t.tier, key: t.key, label_he: t.label_he, min_dbm: t.min_dbm, token: t.token })),
      unclassified: { key: m.UNCLASSIFIED.key, label_he: m.UNCLASSIFIED.label_he, token: m.UNCLASSIFIED.token },
      vectors: vectors.map((v) => ({ id: v.id, expected: v.expected_tier,
        got: m.classifyTier(v.network_type, v.rsrp_dbm, v.service_state) })),
    };
  }, VECTORS.vectors);

  // Same tiers, keys, labels, thresholds and colour tokens as the server
  const server = meta.tiers.map((t: any) => ({ tier: t.tier, key: t.key, label_he: t.label_he, min_dbm: t.min_dbm, token: t.token }));
  expect(result.tiers.sort((a: any, b: any) => b.tier - a.tier)).toEqual(server);
  expect(result.unclassified).toEqual(meta.unclassified);
  // Thresholds of the vectors file
  for (const [t, dbm] of Object.entries(VECTORS.tier_thresholds_dbm)) {
    expect(server.find((x: any) => String(x.tier) === t)?.min_dbm).toBe(dbm);
  }
  // Every shared vector classifies identically
  const wrong = result.vectors.filter((v: any) => v.got !== v.expected);
  expect(wrong).toEqual([]);
  expect(result.vectors.length).toBe(VECTORS.vectors.length);
});
