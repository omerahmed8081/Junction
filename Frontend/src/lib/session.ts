// What this browser remembers, because the backend has no endpoint for it yet:
//   * which baskets were started here (for the Approvals list)
//   * every agent step received (for the Activity page)
// Both are small external stores that React reads with useSyncExternalStore.
import { useSyncExternalStore } from "react";
import type { AgentStep } from "../api/types";

export type LoggedStep = AgentStep & { basketId?: string | null; security?: boolean };

function store<T>(storage: () => Storage, key: string, fallback: T) {
  const listeners = new Set<() => void>();
  let cache: T | undefined;
  const read = (): T => {
    if (cache !== undefined) return cache;
    try { cache = JSON.parse(storage().getItem(key) || "null") ?? fallback; } catch { cache = fallback; }
    return cache as T;
  };
  const write = (value: T) => {
    cache = value;
    try { storage().setItem(key, JSON.stringify(value)); } catch { /* storage full or blocked */ }
    listeners.forEach((l) => l());
  };
  const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };
  return { read, write, subscribe };
}

const baskets = store<string[]>(() => localStorage, "tta.baskets", []);
const steps = store<LoggedStep[]>(() => sessionStorage, "tta.steps", []);

// The basket the Order page was showing, so leaving the page and coming back
// (or opening the app again) returns to it instead of an empty screen.
const CURRENT = "tta.currentBasket";
export function getCurrentBasket(): string | null {
  try { return localStorage.getItem(CURRENT); } catch { return null; }
}
export function setCurrentBasket(id: string | null) {
  try { if (id) localStorage.setItem(CURRENT, id); else localStorage.removeItem(CURRENT); } catch { /* ignore */ }
}

export function rememberBasket(id: string) {
  const known = baskets.read();
  if (!known.includes(id)) baskets.write([id, ...known].slice(0, 30));
}
export function forgetBasket(id: string) { baskets.write(baskets.read().filter((b) => b !== id)); }
export function forgetAllBaskets() { baskets.write([]); setCurrentBasket(null); }
export function useKnownBaskets(): string[] { return useSyncExternalStore(baskets.subscribe, baskets.read); }

export function logSteps(received: AgentStep[] | undefined, basketId?: string | null) {
  if (!received?.length) return;
  steps.write([...steps.read(), ...received.map((s) => ({ ...s, basketId }))].slice(-300));
}
export function logSecurityEvent(detail: string, basketId?: string | null) {
  steps.write([...steps.read(), { step: "security_event", detail, facts: "", for_chef: true, crossed_gateway: true, at: new Date().toISOString(), basketId, security: true }]);
}
export function clearSteps() { steps.write([]); }
export function useSteps(): LoggedStep[] { return useSyncExternalStore(steps.subscribe, steps.read); }

// Who is using the screen: chef, manager or owner. There is no sign-in yet, so
// the person picks it in the header and this browser remembers it. Every
// request sends it (X-Kitchen-Role) and the backend decides what it allows.
export type Role = "chef" | "manager" | "owner";
export const ROLES: Role[] = ["chef", "manager", "owner"];
export const RANK: Record<string, number> = { chef: 1, manager: 2, owner: 3 };
const role = store<Role>(() => localStorage, "tta.role", "chef");
export function getRole(): Role { const r = role.read(); return ROLES.includes(r) ? r : "chef"; }
export function setRole(r: Role) { role.write(r); }
export function useRole(): Role { useSyncExternalStore(role.subscribe, role.read); return getRole(); }
/** Whether `who` may approve a basket whose policy needs `level`. */
export function canApprove(who: Role, level: string | null | undefined): boolean {
  if (level === "blocked") return false;
  return RANK[who] >= (RANK[level ?? "chef"] ?? 1);
}

// The chat transcript, so it survives moving between tabs of the app.
export type ChatMessage = { role: "you" | "agent"; text: string; steps?: AgentStep[] };
const chat = store<ChatMessage[]>(() => sessionStorage, "tta.chat", []);
export function addChatMessage(message: ChatMessage) { chat.write([...chat.read(), message].slice(-60)); }
export function clearChat() { chat.write([]); }
export function useChat(): ChatMessage[] { return useSyncExternalStore(chat.subscribe, chat.read); }
