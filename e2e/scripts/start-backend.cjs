#!/usr/bin/env node
/**
 * Starts the CoverageOps backend for the e2e run (playwright.config.ts → webServer[0]):
 *   1. wipes the e2e temp folder → fresh SQLite DB + results dir on every run,
 *   2. seeds the default users (python -m app.scripts.seed) and the field-reception demo fleet
 *      (python -m app.scripts.field_simulator --seed-demo),
 *   3. runs uvicorn in the foreground until Playwright stops it.
 */
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { backendEnv, BACKEND_DIR, E2E_TMP_DIR, pythonExecutable, API_PORT } = require("./env.cjs");

fs.rmSync(E2E_TMP_DIR, { recursive: true, force: true });
fs.mkdirSync(path.join(E2E_TMP_DIR, "results"), { recursive: true });

const py = pythonExecutable();
const env = backendEnv();
console.log(`[e2e] backend: ${py} · DB ${env.DATABASE_URL}`);

const seed = spawnSync(py, ["-m", "app.scripts.seed"], { cwd: BACKEND_DIR, env, stdio: "inherit" });
if (seed.status !== 0) {
  console.error(`[e2e] seeding failed (exit ${seed.status}) — python: ${py}`);
  process.exit(seed.status ?? 1);
}

// Field reception: synthetic demo fleet (~23 devices, 8 h history ending now, alerts, aggregates)
// for the /field specs. Clearly-labelled synthetic data ("דמו · …"), see app/services/field_demo.py.
const demo = spawnSync(py, ["-m", "app.scripts.field_simulator", "--seed-demo"], { cwd: BACKEND_DIR, env, stdio: "inherit" });
if (demo.status !== 0) {
  console.error(`[e2e] field demo seeding failed (exit ${demo.status}) — python: ${py}`);
  process.exit(demo.status ?? 1);
}

const server = spawn(
  py,
  ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(API_PORT)],
  { cwd: BACKEND_DIR, env, stdio: "inherit" },
);
const stop = () => { if (server.exitCode === null) server.kill(); };
process.on("SIGINT", () => { stop(); process.exit(0); });
process.on("SIGTERM", () => { stop(); process.exit(0); });
process.on("exit", stop);
server.on("exit", (code) => process.exit(code ?? 0));
