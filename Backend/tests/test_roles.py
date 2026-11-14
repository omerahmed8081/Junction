"""Who may place what: chef, manager and owner.

* A chef places orders up to the chef limit; above it a manager must approve.
* A manager places orders up to the manager limit; above it the owner must approve.
* The owner approves anything the mandate allows, and is the only one who can
  change the mandate (the agent's spending limit).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient  # noqa: E402

from kitchen import agent, ordering  # noqa: E402
from kitchen import main as kitchen_main  # noqa: E402
from kitchen.ordering import OrderingError  # noqa: E402
from shared.schemas import Basket, BasketLine  # noqa: E402
from tests.test_ordering import NOW, StubGateway, drafted  # noqa: E402


def _policy_for(net: float):
    basket = Basket(basket_id="b_test", lines=[BasketLine.model_construct(product_id="p", flags=[], delivery=None)],
                    net_total=net)
    return agent.preview_policy(basket)


def test_the_amount_decides_who_must_approve():
    assert _policy_for(400).level == "chef"
    assert _policy_for(800).level == "manager"
    owner = _policy_for(1600)
    assert owner.level == "owner" and owner.reasons == ["Over the EUR 1500 manager limit"]


def _refused(state, role, gateway, kitchen) -> str:
    try:
        ordering.approve(state, role, gateway, kitchen, now=NOW)
    except OrderingError as exc:
        return str(exc)
    raise AssertionError(f"{role} placed an order above their limit")


def test_a_manager_can_place_what_a_chef_cannot():
    state, gateway, kitchen = drafted()               # EUR 1,010: manager level
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    assert "needs manager approval" in _refused(state, "chef", gateway, kitchen)
    result = ordering.approve(state, "manager", gateway, kitchen, now=NOW)
    assert result.outcome == "ordered" and result.order.approver_role == "manager"


def test_above_the_manager_limit_only_the_owner_can_approve():
    state, gateway, kitchen = drafted()
    ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    old = agent.MANAGER_LIMIT_EUR
    agent.MANAGER_LIMIT_EUR = 1000.0                  # EUR 1,010 is now above it
    try:
        ordering.confirm(state.basket, gateway, kitchen, now=NOW)
    finally:
        agent.MANAGER_LIMIT_EUR = old
    assert state.basket.policy.level == "owner" and state.basket.policy.binding
    assert "needs owner approval" in _refused(state, "manager", gateway, kitchen)
    assert "needs owner approval" in _refused(state, "chef", gateway, kitchen)
    result = ordering.approve(state, "owner", gateway, kitchen, now=NOW)
    assert result.outcome == "ordered" and result.order.approver_role == "owner"


def test_only_the_owner_can_change_the_agents_spending_limit(monkeypatch):
    monkeypatch.setattr(kitchen_main, "_save", lambda: None)
    client = TestClient(kitchen_main.app)           # no lifespan: no database or gateway needed
    for role in ("chef", "manager", None):
        headers = {"X-Kitchen-Role": role} if role else {}
        r = client.post("/api/mandate", json={"max_net_per_order": 9999}, headers=headers)
        assert r.status_code == 403, role
    r = client.post("/api/mandate", json={"max_net_per_order": 2500}, headers={"X-Kitchen-Role": "owner"})
    assert r.status_code == 200
    body = r.json()
    assert body["limits"]["max_net_per_order"] == 2500
    assert body["role_limits"] == {"chef": agent.CHEF_LIMIT_EUR, "manager": agent.MANAGER_LIMIT_EUR}


def test_an_unknown_role_header_is_refused(monkeypatch):
    monkeypatch.setattr(kitchen_main, "_save", lambda: None)
    client = TestClient(kitchen_main.app)
    r = client.post("/api/mandate", json={}, headers={"X-Kitchen-Role": "intern"})
    assert r.status_code == 422


def test_the_basket_list_filters_by_status(monkeypatch):
    state, gateway, kitchen = drafted()
    monkeypatch.setattr(kitchen_main, "baskets", {state.basket.basket_id: state})
    client = TestClient(kitchen_main.app)
    assert [b["basket_id"] for b in client.get("/api/baskets?status=draft").json()] == [state.basket.basket_id]
    assert client.get("/api/baskets?status=quoted").json() == []
    assert client.get("/api/baskets").json()[0]["created_by"] == "chef"


def test_a_new_basket_belongs_to_whoever_started_it(monkeypatch):

    monkeypatch.setattr(kitchen_main, "_save", lambda: None)
    monkeypatch.setattr(kitchen_main, "gateway", StubGateway())
    monkeypatch.setattr(kitchen_main, "baskets", {})
    client = TestClient(kitchen_main.app)
    r = client.post("/api/chat", json={"message": "1,000 L lactose-free milk by Tuesday"},
                    headers={"X-Kitchen-Role": "manager"})
    assert r.status_code == 200, r.text
    basket = r.json()["basket"]
    assert basket["created_by"] == "manager"
    # A chef adding to the manager's draft does not take it over.
    r = client.post("/api/chat", json={"message": "100 L lactose-free milk", "basket_id": basket["basket_id"]},
                    headers={"X-Kitchen-Role": "chef"})
    assert r.json()["basket"]["created_by"] == "manager"


def test_only_the_owner_can_set_or_switch_off_standing_rules(monkeypatch):
    """A rule lets orders through without approval, so it is a spending decision too."""
    monkeypatch.setattr(kitchen_main, "_save", lambda: None)
    client = TestClient(kitchen_main.app)
    rule = {"name": "milk", "match": "name", "value": "milk", "max_net_per_order": 200}
    for role in ("chef", "manager", None):
        headers = {"X-Kitchen-Role": role} if role else {}
        assert client.post("/api/rules", json=rule, headers=headers).status_code == 403, role
        assert client.delete("/api/rules/1", headers=headers).status_code == 403, role
