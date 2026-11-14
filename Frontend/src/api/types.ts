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
export type MandateView = S["MandateView"];
export type MandateRequest = S["MandateRequest"];
export type InventoryView = S["InventoryView"];
export type StockRow = S["StockRow"];
export type Suggestion = S["Suggestion"];
export type SuggestionsResponse = S["SuggestionsResponse"];
export type ServiceResponse = S["ServiceResponse"];
