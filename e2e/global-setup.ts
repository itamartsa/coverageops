/**
 * Runs once after the web servers are up:
 *  - makes sure the OPERATOR / VIEWER test accounts exist (created through the admin API if missing,
 *    e.g. when reusing a backend that was seeded differently),
 *  - removes leftovers of earlier runs (only relevant with reuseExistingServer — a normal run starts
 *    from a freshly wiped SQLite DB, see scripts/start-backend.cjs).
 */
import { Api, USERS, type Role } from "./support/api";

export default async function globalSetup() {
  const admin = await Api.as("ADMIN");
  try {
    const existing = await admin.users();
    for (const role of ["OPERATOR", "VIEWER"] as Role[]) {
      const u = USERS[role];
      if (!existing.some((x) => x.username === u.username)) {
        await admin.createUser({ ...u, role });
      }
    }

    // Leftovers from previous runs against a reused backend
    for (const s of await admin.listSites()) {
      if (s.name.startsWith("E2E")) await admin.deleteSite(s.id);
    }
    for (const u of await admin.users()) {
      if (u.username.startsWith("e2e_")) await admin.delete(`/api/users/${u.id}`);
    }
    for (const p of await admin.deviceProfiles()) {
      if (p.key.startsWith("e2e_") && !p.is_default) await admin.delete(`/api/device-profiles/${p.id}`);
    }
  } finally {
    await admin.dispose();
  }
}
