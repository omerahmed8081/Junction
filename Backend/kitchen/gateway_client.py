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
    SearchRequest,
    SearchResponse,
)


class GatewayError(RuntimeError):
    pass


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
