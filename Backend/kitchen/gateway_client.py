"""The kitchen's only door to Aimo: plain HTTP calls to the gateway service.

Nothing in kitchen/ imports from gateway/. The two services share shapes
(shared/schemas.py) and nothing else, so either could be replaced by someone
else's implementation of the same OpenAPI contract.
"""
from __future__ import annotations

import os
from datetime import date

import httpx

from shared.schemas import (
    CategoryListResponse,
    CheckDeliveryItem,
    CheckDeliveryRequest,
    CheckDeliveryResponse,
    CreateQuoteRequest,
    CreateQuoteResponse,
    OrderRecord,
    PlaceOrderRequest,
    QuoteLineRequest,
    SearchRequest,
    SearchResponse,
)


class GatewayError(RuntimeError):
    """The gateway could not be reached, or answered in a way we cannot use."""


class GatewayRefused(GatewayError):
    """The gateway said no, with a reason (HTTP 409).

    Not a failure: the request was well formed and the state said no. The reason
    is a stable code such as quote_already_used or over_order_limit, and
    `security_event` marks the ones that belong in the audit log rather than in a
    retry.
    """

    def __init__(self, reason: str, detail: str, security_event: bool = False,
                 payload: dict | None = None) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail
        self.security_event = security_event
        self.payload = payload or {}

    def rejection_model(self):
        """The refusal as the shared OrderRejected shape, for an API response."""
        from shared.schemas import OrderRejected

        return OrderRejected(reason=self.reason, detail=self.detail,
                             security_event=self.security_event)


class GatewayClient:
    """`http` can be any httpx.Client; tests pass one bound to the gateway app."""

    def __init__(self, base_url: str | None = None, http: httpx.Client | None = None, timeout: float = 30.0) -> None:
        self._http = http or httpx.Client(
            base_url=base_url or os.environ.get("GATEWAY_URL", "http://localhost:8001"),
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, payload: dict) -> dict:
        try:
            response = self._http.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise GatewayError(f"gateway not reachable at {path}: {exc}") from exc
        if response.status_code == 409:
            raise _refusal(path, response)
        if response.status_code != 200:
            raise GatewayError(f"{path} returned {response.status_code}: {response.text[:200]}")
        return response.json()

    def _get(self, path: str) -> dict:
        try:
            response = self._http.get(path)
        except httpx.HTTPError as exc:
            raise GatewayError(f"gateway not reachable at {path}: {exc}") from exc
        if response.status_code != 200:
            raise GatewayError(f"{path} returned {response.status_code}: {response.text[:200]}")
        return response.json()

    def categories(self) -> CategoryListResponse:
        response = self._http.get("/gateway/categories")
        if response.status_code != 200:
            raise GatewayError(f"/gateway/categories returned {response.status_code}")
        return CategoryListResponse(**response.json())

    def search_products(self, request: SearchRequest) -> SearchResponse:
        return SearchResponse(**self._post("/gateway/search_products", request.model_dump(mode="json")))

    def check_delivery(self, requested_date: date, items: list[CheckDeliveryItem]) -> CheckDeliveryResponse:
        request = CheckDeliveryRequest(requested_date=requested_date, items=items)
        return CheckDeliveryResponse(**self._post("/gateway/check_delivery", request.model_dump(mode="json")))

    def create_quote(self, agent_id: str, requested_date: date,
                     lines: list[QuoteLineRequest]) -> CreateQuoteResponse:
        """Ask Aimo to sign a price confirmation for exactly these lines.

        A 409 means nothing could be quoted; the detail says which line and why,
        and GatewayRefused carries it through so the caller can explain it.
        """
        payload = CreateQuoteRequest(agent_id=agent_id, requested_date=requested_date,
                                     lines=lines).model_dump(mode="json")
        return CreateQuoteResponse(**self._post("/gateway/create_quote", payload))

    def place_order(self, request: PlaceOrderRequest) -> OrderRecord:
        """Send the quote, the mandate and the human approval.

        A 409 is a refusal with a reason, not a transport failure: the gateway
        checked the quote, the mandate, the limits, the approval and the delivery
        again, and one of them said no.
        """
        return OrderRecord(**self._post("/gateway/place_order", request.model_dump(mode="json")))

    def order(self, order_id: str) -> OrderRecord:
        return OrderRecord(**self._get(f"/gateway/orders/{order_id}"))


def _refusal(path: str, response: "httpx.Response") -> GatewayRefused:
    """Turn the gateway's 409 body into something the chat can explain."""
    try:
        detail = response.json().get("detail")
    except Exception:       # noqa: BLE001
        detail = None
    if isinstance(detail, dict):
        return GatewayRefused(
            reason=str(detail.get("reason") or "refused"),
            detail=str(detail.get("detail") or detail.get("reason") or "the gateway refused"),
            security_event=bool(detail.get("security_event")),
            payload=detail,
        )
    return GatewayRefused(reason="refused", detail=str(detail or response.text[:200]))
