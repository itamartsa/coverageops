/**
 * Shared paths / env for the e2e servers (used by playwright.config.ts, global-setup.ts and start-backend.cjs).
 * CommonJS on purpose: loadable from the TS config and from plain `node` on any platform.
 */
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const E2E_DIR = path.resolve(__dirname, "..");
const REPO_DIR = path.resolve(E2E_DIR, "..");
const BACKEND_DIR = path.join(REPO_DIR, "backend");
const FRONTEND_DIR = path.join(REPO_DIR, "frontend");

/** Temp folder holding the throw-away SQLite DB + results dir (wiped at the start of every run). */
const E2E_TMP_DIR = process.env.E2E_TMP_DIR || path.join(os.tmpdir(), "coverageops-e2e");

const API_PORT = Number(process.env.E2E_API_PORT || 8000);
const WEB_PORT = Number(process.env.E2E_WEB_PORT || 3000);
const API_URL = `http://localhost:${API_PORT}`;
const WEB_URL = `http://localhost:${WEB_PORT}`;

/** Windows: backend\.venv\Scripts\python.exe when it exists, else `python`. Linux/macOS: `python`. */
function pythonExecutable() {
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON;
  if (process.platform === "win32") {
    const venv = path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe");
    return fs.existsSync(venv) ? venv : "python";
  }
  return "python";
}

function backendEnv() {
  // SQLAlchemy wants forward slashes, also on Windows (sqlite:///C:/Users/...)
  const dbPath = path.join(E2E_TMP_DIR, "e2e.db").replace(/\\/g, "/");
  return {
    ...process.env,
    DATABASE_URL: `sqlite:///${dbPath}`,
    SECRET_KEY: "e2e",
    RESULTS_DIR: path.join(E2E_TMP_DIR, "results"),
    PYTHONIOENCODING: "utf-8",   // seed.py prints emoji — keeps Windows consoles happy
    PYTHONUTF8: "1",
  };
}

module.exports = {
  E2E_DIR, REPO_DIR, BACKEND_DIR, FRONTEND_DIR, E2E_TMP_DIR,
  API_PORT, WEB_PORT, API_URL, WEB_URL, pythonExecutable, backendEnv,
};
