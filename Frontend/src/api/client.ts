// The only file that calls the backend. Every function returns the typed body
// or throws an ApiError the UI can show.
import type {
  Basket, ChatResponse, InventoryView, LineEdit, MandateRequest, MandateView,
  OrderRecord, OrderResponse, ServiceResponse, SuggestionsResponse,
} from "./types";

export class ApiError extends Error {
  status: number;
  /**
   * "network"  = the kitchen service itself did not answer
   * "upstream" = the kitchen answered 502: the gateway or Aimo behind it failed
   * "rule"     = the backend said no (409), "missing" = 404, "invalid" = 422
   */
  kind: "network" | "upstream" | "rule" | "missing" | "invalid" | "server";
  constructor(status: number, message: string, fromProxy = false) {
    super(message);
    this.status = status;
    this.kind = status === 0 || fromProxy || status === 503 || status === 504 ? "network"
      : status === 502 ? "upstream"
      : status === 409 ? "rule" : status === 404 ? "missing" : status === 422 ? "invalid" : "server";
  }
}

function detailText(body: unknown): string {
  const detail = (body as { detail?: unknown })?.detail ?? body;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? JSON.stringify(d)).join("; ");
  if (detail && typeof detail === "object") {
    const d = detail as { detail?: string; reason?: string };
    return d.detail || d.reason || JSON.stringify(detail);
  }
  return "";
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "");
  }
  const text = await response.text();
  let parsed: unknown = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = text; }
  // When the kitchen service is not running, the dev server's proxy answers 502
  // with an empty plain-text body. The services themselves always answer in JSON.
  const json = (response.headers.get("content-type") ?? "").includes("application/json");
  if (!response.ok) throw new ApiError(response.status, detailText(parsed), response.status >= 500 && !json);
  return parsed as T;
}

export const api = {
  chat: (message: string, basketId?: string | null) =>
    request<ChatResponse>("POST", "/api/chat", { message, basket_id: basketId ?? null }),
  basket: (id: string) => request<Basket>("GET", `/api/basket/${id}`),
  editBasket: (id: string, edits: LineEdit[], requestedDate?: string) =>
    request<ChatResponse>("PATCH", `/api/basket/${id}`, { edits, requested_date: requestedDate ?? null }),
  confirm: (id: string) => request<ChatResponse>("POST", `/api/basket/${id}/confirm`),
  // One person uses the system and holds every permission, so the approval is
  // always signed with the most senior role the backend knows.
  approve: (id: string) => request<OrderResponse>("POST", `/api/basket/${id}/approve`, { approver_role: "owner" }),
  orders: () => request<OrderRecord[]>("GET", "/api/orders"),
  mandate: () => request<MandateView>("GET", "/api/mandate"),
  issueMandate: (body: MandateRequest) => request<MandateView>("POST", "/api/mandate", body),
  inventory: () => request<InventoryView>("GET", "/api/inventory"),
  suggestions: () => request<SuggestionsResponse>("GET", "/api/suggestions"),
  draftSuggestion: (productId: string) => request<ChatResponse>("POST", `/api/suggestions/${productId}/basket`),
  logService: (portions: Record<string, number>, serviceDate?: string) =>
    request<ServiceResponse>("POST", "/api/service", { portions, service_date: serviceDate ?? null }),
  resetKitchen: () => request<unknown>("POST", "/api/demo/reset"),
  resetGateway: () => request<unknown>("POST", "/gateway/demo/reset"),
};
