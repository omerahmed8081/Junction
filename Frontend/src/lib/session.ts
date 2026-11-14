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

export function rememberBasket(id: string) {
  const known = baskets.read();
  if (!known.includes(id)) baskets.write([id, ...known].slice(0, 30));
}
export function forgetBasket(id: string) { baskets.write(baskets.read().filter((b) => b !== id)); }
export function forgetAllBaskets() { baskets.write([]); }
export function useKnownBaskets(): string[] { return useSyncExternalStore(baskets.subscribe, baskets.read); }

export function logSteps(received: AgentStep[] | undefined, basketId?: string | null) {
  if (!received?.length) return;
  steps.write([...steps.read(), ...received.map((s) => ({ ...s, basketId }))].slice(-300));
}
export function logSecurityEvent(detail: string, basketId?: string | null) {
  steps.write([...steps.read(), { step: "security_event", detail, crossed_gateway: true, at: new Date().toISOString(), basketId, security: true }]);
}
export function clearSteps() { steps.write([]); }
export function useSteps(): LoggedStep[] { return useSyncExternalStore(steps.subscribe, steps.read); }

// The chat transcript, so it survives moving between tabs of the app.
export type ChatMessage = { role: "you" | "agent"; text: string; steps?: AgentStep[] };
const chat = store<ChatMessage[]>(() => sessionStorage, "tta.chat", []);
export function addChatMessage(message: ChatMessage) { chat.write([...chat.read(), message].slice(-60)); }
export function clearChat() { chat.write([]); }
export function useChat(): ChatMessage[] { return useSyncExternalStore(chat.subscribe, chat.read); }
