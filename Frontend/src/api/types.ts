// Short names for the shapes generated from the kitchen service's OpenAPI document.
// Regenerate schema.d.ts with `npm run api:types` whenever the backend contract changes.
import type { components } from "./schema";

type S = components["schemas"];
export type Basket = S["Basket"];
export type BasketLine = S["BasketLine"];
export type Alternative = S["Alternative"];
export type DeliveryAnswer = S["DeliveryAnswer"];
export type AgentStep = S["AgentStep"];
export type ChatResponse = S["ChatResponse"];
export type LineEdit = S["LineEdit"];
export type OrderResponse = S["OrderResponse"];
export type OrderRecord = S["OrderRecord"];
// agent_name is newer than the checked-in schema.d.ts; regenerating with
// `npm run api:types` against a running kitchen makes the intersection a no-op.
export type MandateView = S["MandateView"] & { agent_name?: string };
export type MandateRequest = S["MandateRequest"];
export type InventoryView = S["InventoryView"];
export type StockRow = S["StockRow"];
export type Suggestion = S["Suggestion"];
export type SuggestionsResponse = S["SuggestionsResponse"];
export type ServiceResponse = S["ServiceResponse"];

// Written by hand rather than generated, so the UI does not wait on a running
// backend to typecheck. Regenerate schema.d.ts when convenient and these can go.
export type RuleMatch = "any" | "name" | "product" | "claim" | "category";

export type Rule = {
  rule_id: number | null;
  name: string;
  match: RuleMatch;
  value: string;
  max_net_per_order: number | null;
  active: boolean;
  /** The rule as a sentence, written by the backend so both sides say it the same way. */
  said: string;
  created_at: string;
};

export type RuleList = { count: number; degraded: boolean; note: string; rules: Rule[] };

export type NewRule = {
  name?: string;
  match: RuleMatch;
  value: string;
  max_net_per_order: number | null;
};

/** One row of either side's audit log. The fields differ by side, so most are optional. */
export type AuditRow = {
  id?: number | null;
  side: "kitchen" | "aimo";
  at: string;
  detail?: string | null;
  reason?: string | null;
  step?: string | null;          // kitchen rows
  action?: string | null;        // Aimo rows
  actor?: string | null;
  approver_role?: string | null;
  security_event?: boolean;
  crossed_gateway?: boolean;
  order_id?: string | null;
  quote_jti?: string | null;
};

export type OrderAuditView = {
  order_id: string;
  count: number;
  kitchen_rows: number;
  aimo_rows: number;
  degraded: boolean;
  records: AuditRow[];
};
