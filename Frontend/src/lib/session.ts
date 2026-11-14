// What this browser remembers. Four things, and every one of them is read
// somewhere; a store that is written and never read is how a page ends up
// showing yesterday's data, so there are none here.
//
//   * which basket the Order page had open, so coming back returns to it
//   * who is at the screen (chef, manager, owner) - there is no sign-in yet
//   * the chat transcript, so moving between tabs does not lose it
//   * which run of the kitchen's data the two above belong to
//
// The last one is the important one. The kitchen can be reset from a terminal
// (`scripts/clean_slate.py`) or restarted, and the browser hears nothing about
// it, so a transcript about baskets that no longer exist used to sit here
// looking current. Now the kitchen reports a generation, and anything tied to
// a run that has ended goes.
import { useSyncExternalStore } from "react";
import type { AgentStep } from "../api/types";

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

// The basket the Order page was showing, so leaving the page and coming back
// (or opening the app again) returns to it instead of an empty screen.
const CURRENT = "tta.currentBasket";
export function getCurrentBasket(): string | null {
  try { return localStorage.getItem(CURRENT); } catch { return null; }
}
export function setCurrentBasket(id: string | null) {
  try { if (id) localStorage.setItem(CURRENT, id); else localStorage.removeItem(CURRENT); } catch { /* ignore */ }
}

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

// ---------------------------------------------------------------- generation

const GENERATION = "tta.generation";

/**
 * Compare the kitchen's generation with the one this browser last saw, and
 * drop anything that belonged to an earlier run. Returns true if it cleared.
 *
 * Called from the shell with every `/api/kitchen` answer, which is one request
 * the app already makes on every page load. The role and the language are
 * preferences rather than data about a run, so they stay.
 */
export function syncGeneration(serverGeneration: string | undefined | null): boolean {
  if (!serverGeneration) return false;            // an older backend: change nothing
  let seen: string | null = null;
  try { seen = localStorage.getItem(GENERATION); } catch { return false; }
  if (seen === serverGeneration) return false;
  try { localStorage.setItem(GENERATION, serverGeneration); } catch { /* ignore */ }
  if (seen === null) {
    // Never synced before. Two older keys may still be sitting here from a
    // build that wrote them and read them nowhere; drop those. The transcript
    // stays: this also runs the first time any browser loads the app, and a
    // message typed in the moment before this answer arrived is not stale.
    for (const key of ["tta.steps", "tta.baskets"]) {
      try { localStorage.removeItem(key); sessionStorage.removeItem(key); } catch { /* ignore */ }
    }
    return false;
  }
  clearChat();
  setCurrentBasket(null);
  return true;
}
