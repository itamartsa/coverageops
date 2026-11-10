/**
 * Field reception — react-query hooks shared by the /field page, the TopBar badge and the main map.
 *
 * Polling (spec 4): /devices/live every 10 s, /summary, /alerts?status=open and /devices every 30 s,
 * a device's commands every 10 s while one is still open. `refetchIntervalInBackground: false`
 * (react-query's default, set explicitly) — a hidden browser tab does not poll.
 * Query keys: ["field", <resource>, …params]; mutations invalidate the affected "field" keys.
 * Time windows are part of the key as the window NAME ("24h"); from/to are computed at fetch time,
 * so a poll never creates a new cache entry.
 */
import { useQuery } from "@tanstack/react-query";
import { fieldApi, type FieldOperator, type FieldWindow } from "../services/api";
import type { FieldFilters, FieldWindowKey } from "../store/useStore";
import { CELL_OPERATORS } from "./cellSites";

const WINDOW_MS: Record<string, number> = {
  "1h": 3600_000, "24h": 86400_000, "7d": 7 * 86400_000, "30d": 30 * 86400_000,
};

export const WINDOW_LABEL_HE: Record<FieldWindowKey, string> = {
  "1h": "שעה אחרונה", "24h": "24 שעות", "7d": "7 ימים", "30d": "30 ימים",
};

/** {from, to} ISO for a window name ending now (optionally capped, e.g. 7 days for a track). */
export function windowRange(key: string, capMs?: number): FieldWindow {
  const to = new Date();
  const span = Math.min(WINDOW_MS[key] ?? WINDOW_MS["24h"], capMs ?? Infinity);
  return { from: new Date(to.getTime() - span).toISOString(), to: to.toISOString() };
}

export const POLL = { live: 10_000, summary: 30_000, alerts: 30_000, devices: 30_000, commands: 10_000 };
const BG = { refetchIntervalInBackground: false } as const;

export function useOperators() {
  return useQuery({
    queryKey: ["field", "operators"],
    queryFn: fieldApi.operators,
    staleTime: 10 * 60_000,
  });
}

/** Operator display name (Hebrew) from operator_key "425-01". */
export function operatorName(key: string | null | undefined, ops?: FieldOperator[]): string {
  if (!key) return "—";
  return ops?.find((o) => o.operator_key === key)?.name_he ?? key;
}

/** Operator colour — the existing CELL_OPERATORS palette via field_operator_map.cell_sites_operator. */
export function operatorColor(key: string | null | undefined, ops?: FieldOperator[]): string {
  const op = ops?.find((o) => o.operator_key === key)?.cell_sites_operator ?? "OTHER";
  return (CELL_OPERATORS[op] ?? CELL_OPERATORS.OTHER).color;
}

export function useSummary(f: FieldFilters, isViewer: boolean) {
  const group = isViewer ? null : f.groupId;
  const op = f.operators.join(",") || null;
  return useQuery({
    queryKey: ["field", "summary", f.window, group, op],
    queryFn: () => fieldApi.summary({ ...windowRange(f.window), group_id: group, operator: op }),
    refetchInterval: POLL.summary, ...BG,
  });
}

/** Open alerts (ADMIN/OPERATOR). The TopBar badge shares this cache entry. */
export function useOpenAlerts(enabled: boolean) {
  return useQuery({
    queryKey: ["field", "alerts", "open"],
    queryFn: () => fieldApi.alerts({ status: "open", limit: 500 }),
    enabled, refetchInterval: POLL.alerts, ...BG,
  });
}

export function useDevices(enabled: boolean, groupId: number | null) {
  return useQuery({
    queryKey: ["field", "devices", groupId],
    queryFn: () => fieldApi.devices({ group_id: groupId }),
    enabled, refetchInterval: POLL.devices, ...BG,
  });
}

export function useLive(enabled: boolean) {
  return useQuery({
    queryKey: ["field", "live"],
    queryFn: () => fieldApi.live(),
    enabled, refetchInterval: POLL.live, ...BG,
  });
}

export function useGroups(enabled: boolean) {
  return useQuery({ queryKey: ["field", "groups"], queryFn: fieldApi.groups, enabled, staleTime: 60_000 });
}

/** Warning + critical count for the TopBar badge (info alerts are not counted — spec 7.3). */
export function badgeCount(alerts: { severity: string }[] | undefined): number {
  return (alerts ?? []).filter((a) => a.severity === "warning" || a.severity === "critical").length;
}
