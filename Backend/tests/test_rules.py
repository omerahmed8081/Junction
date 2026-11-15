"""The kitchen's standing rules (kitchen/rules.py).

A rule can only ever relax an approval the kitchen would ask for itself. The
tests that matter are the ones pinning what a rule may *not* do.
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from kitchen import rules  # noqa: E402
from shared.schemas import Basket, BasketLine, DeliveryAnswer  # noqa: E402


def a_line(product_id="464306", name="Valio Eila milk 1 l lactose free", net=100.0,
           claims=None, category="", can_fulfil="full", flags=None) -> BasketLine:
    return BasketLine(
        product_id=product_id, name=name, need_value=10, need_unit="L",
        sales_unit="ST", base_unit="L", base_units_per_sales_unit=1.0,
        quantity_sales_units=10, line_net=net, price_per_sales_unit=net / 10,
        claims=claims or [], category=category, flags=flags or [],
        delivery=DeliveryAnswer(can_fulfil=can_fulfil))


def a_basket(*lines, net=None) -> Basket:
    lines = list(lines) or [a_line()]
    basket = Basket(basket_id="b_1", requested_date=date(2026, 11, 17), lines=lines)
    basket.net_total = net if net is not None else sum(l.line_net for l in lines)
    return basket


def milk_rule(**over) -> rules.Rule:
    fields = {"name": "milk", "match": "name", "value": "milk", "max_net_per_order": 200.0}
    fields.update(over)
    return rules.Rule(**fields)


# ---- what a rule does --------------------------------------------------

def test_a_rule_clears_a_basket_it_covers():
    assert rules.applies([milk_rule()], a_basket(a_line(net=150)), []) is not None


def test_a_ceiling_is_a_ceiling():
    assert rules.applies([milk_rule()], a_basket(a_line(net=250)), []) is None


def test_a_rule_with_no_ceiling_covers_any_amount():
    rule = milk_rule(max_net_per_order=None)
    assert rules.applies([rule], a_basket(a_line(net=9_000)), []) is not None


def test_matching_on_a_product_number():
    rule = rules.Rule(match="product", value="464306", max_net_per_order=500)
    assert rules.applies([rule], a_basket(a_line(product_id="464306")), []) is not None
    assert rules.applies([rule], a_basket(a_line(product_id="999999")), []) is None


def test_matching_on_a_claim():
    rule = rules.Rule(match="claim", value="FREE_FROM_LACTOSE", max_net_per_order=500)
    assert rules.applies([rule], a_basket(a_line(claims=["FREE_FROM_LACTOSE"])), []) is not None
    assert rules.applies([rule], a_basket(a_line(claims=["VEGAN"])), []) is None


def test_matching_anything_under_a_ceiling():
    rule = rules.Rule(match="any", max_net_per_order=50)
    assert rules.applies([rule], a_basket(a_line(net=40)), []) is not None
    assert rules.applies([rule], a_basket(a_line(net=60)), []) is None


# ---- what a rule must NOT do -------------------------------------------

def test_every_line_has_to_match_not_just_one():
    """A rule about milk does not wave through a basket that also holds rice:
    the chef never said anything about the rice."""
    basket = a_basket(a_line(name="milk", net=50),
                      a_line(product_id="475214", name="AGRINO rice", net=20))
    assert rules.applies([milk_rule()], basket, []) is None


def test_a_rule_never_clears_a_reason_about_the_order_being_wrong():
    for reason in ["Partial delivery accepted",
                   "Product differs from what was asked for",
                   "Pack size disagrees with the price data"]:
        rule = milk_rule(max_net_per_order=None)
        assert rules.applies([rule], a_basket(a_line(net=10)), [reason]) is None, reason


def test_a_rule_does_clear_a_reason_about_size():
    """Being over the chef's limit is exactly what a rule is for."""
    assert rules.applies([milk_rule()], a_basket(a_line(net=150)),
                         ["Over the EUR 500 chef limit"]) is not None


def test_an_inactive_rule_does_nothing():
    assert rules.applies([milk_rule(active=False)], a_basket(a_line(net=10)), []) is None


def test_an_empty_basket_is_never_cleared():
    basket = Basket(basket_id="b_2", requested_date=date(2026, 11, 17), lines=[])
    assert rules.applies([milk_rule(max_net_per_order=None)], basket, []) is None


def test_no_rules_means_no_change():
    assert rules.applies([], a_basket(), []) is None


# ---- how a rule reads --------------------------------------------------

def test_a_rule_says_itself_in_a_sentence():
    assert "up to EUR 200.00 net" in milk_rule().said()
    assert "without asking" in milk_rule().said()
    assert "any order" in rules.Rule(match="any", max_net_per_order=50).said()
    assert "lactose" in rules.Rule(match="claim", value="FREE_FROM_LACTOSE").said()


# ---- the store ---------------------------------------------------------

def test_rules_are_switched_off_rather_than_deleted():
    """A rule in force when an order went through has to stay readable."""
    store = rules.RuleStore(pool=None)
    kept = store.add(milk_rule())
    assert kept.rule_id is not None
    assert len(store.all()) == 1
    assert store.remove(kept.rule_id) is True
    assert store.all() == []                       # gone from the active list
    assert len(store.all(active_only=False)) == 1  # still on the record


def test_removing_a_rule_that_is_not_there():
    assert rules.RuleStore(pool=None).remove(999) is False


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(passed)


# ------------------------------- what a rule change may do to other baskets


@pytest.fixture
def app(tmp_path):
    """The kitchen app with its own empty baskets and no standing rules.

    Everything it touches is module state shared with every other test file, so
    all of it is put back afterwards. A first version of these tests cleared
    `rules.store._rules`, which does not exist - the attribute is `_memory` -
    so the guard silently did nothing and a leftover rule changed what
    `preview_policy` returned in three other files when they happened to run
    after this one.

    The state file is redirected too: these tests call endpoints, endpoints
    call `_save()`, and the developer's own `.state/kitchen.json` is not the
    test suite's to overwrite.
    """
    os.environ["TTA_SKIP_DOTENV"] = "1"
    from fastapi.testclient import TestClient
    import kitchen.main as main
    from kitchen import storage

    held = (rules.store._memory.copy(), rules.store._next,
            dict(main.baskets), storage.STATE_FILE)
    rules.store._memory.clear()
    main.baskets.clear()
    storage.STATE_FILE = tmp_path / "kitchen.json"
    try:
        yield TestClient(main.app), main
    finally:
        saved_rules, saved_next, saved_baskets, saved_file = held
        rules.store._memory.clear()
        rules.store._memory.update(saved_rules)
        rules.store._next = saved_next
        main.baskets.clear()
        main.baskets.update(saved_baskets)
        storage.STATE_FILE = saved_file


def _put(main, basket_id: str, status: str, quote: str | None = "a.signed.quote"):
    """A basket sitting in memory at a given point in its life."""
    from kitchen.agent import BasketState

    basket = a_basket()
    basket.basket_id = basket_id
    basket.status = status
    basket.quote_id, basket.quote_jws = ("q_1", quote) if quote else (None, None)
    basket.quote_verified = quote is not None
    main.baskets[basket_id] = BasketState(basket)
    return basket


OWNER = {"X-Kitchen-Role": "owner"}
A_RULE = {"name": "milk", "match": "name", "value": "milk", "max_net_per_order": 200.0}


def test_adding_a_rule_leaves_a_signed_basket_signed(app):
    """It used to run the full refresh over every basket, which sets the status
    back to "draft" and throws the quote away - so a basket waiting in
    Approvals dropped out of the list, unsigned, mid-demo."""
    client, main = app
    with client:
        waiting = _put(main, "b_wait", "pending_approval")
        client.post("/api/rules", json=A_RULE, headers=OWNER).raise_for_status()

    assert waiting.status == "pending_approval", "the basket fell out of Approvals"
    assert waiting.quote_jws == "a.signed.quote", "Aimo's signed price was discarded"
    assert waiting.quote_verified is True


def test_adding_a_rule_leaves_an_order_ordered(app):
    """The worse half: an order Aimo had already accepted came back as an
    editable draft, which could be confirmed and ordered a second time."""
    client, main = app
    with client:
        placed = _put(main, "b_done", "ordered")
        client.post("/api/rules", json=A_RULE, headers=OWNER).raise_for_status()

    assert placed.status == "ordered", "a placed order became an editable draft again"
    assert placed.quote_jws == "a.signed.quote"


def test_removing_a_rule_leaves_them_alone_too(app):
    client, main = app
    with client:
        made = client.post("/api/rules", json=A_RULE, headers=OWNER)
        made.raise_for_status()
        rule_id = made.json()["rule_id"]
        waiting = _put(main, "b_wait", "quoted")
        placed = _put(main, "b_done", "ordered")
        client.delete(f"/api/rules/{rule_id}", headers=OWNER).raise_for_status()

    assert (waiting.status, placed.status) == ("quoted", "ordered")
    assert waiting.quote_jws == "a.signed.quote"


def test_a_draft_does_follow_the_new_rule(app):
    """The point of the loop in the first place: a draft on screen should show
    that a rule now covers it, without the chef reloading."""
    client, main = app
    with client:
        draft = _put(main, "b_draft", "draft", quote=None)
        before = draft.policy.level if draft.policy else None
        client.post("/api/rules", json=A_RULE, headers=OWNER).raise_for_status()

    assert draft.policy is not None
    assert draft.policy.level == "auto_ok", f"the draft was not re-judged (was {before})"


def test_a_blocked_basket_is_not_quietly_unblocked(app):
    """Blocked is a decision, not a preview. A rule can only relax an approval,
    and must never reopen something stopped on purpose."""
    client, main = app
    with client:
        stopped = _put(main, "b_stop", "blocked")
        client.post("/api/rules", json=A_RULE, headers=OWNER).raise_for_status()

    assert stopped.status == "blocked"


# ------------------------------------------ what may still be edited (A3)


def _edit(client, basket_id: str, quantity: float = 20):
    return client.patch(f"/api/basket/{basket_id}",
                        json={"edits": [{"product_id": "464306", "quantity": quantity}]})


def test_an_order_already_placed_cannot_be_edited(app):
    """It had no status check at all, so an order sitting with Aimo could be
    changed through the API."""
    client, main = app
    with client:
        placed = _put(main, "b_done", "ordered")
        response = _edit(client, "b_done")

    assert response.status_code == 409, response.status_code
    assert "ordered" in response.json()["detail"]
    assert placed.status == "ordered", "the edit went through anyway"


def test_a_blocked_basket_cannot_be_edited_back_into_life(app):
    """Blocked means a signature would not verify, or a security event. An edit
    runs _refresh, which would have set it back to 'draft'."""
    client, main = app
    with client:
        stopped = _put(main, "b_stop", "blocked")
        response = _edit(client, "b_stop")

    assert response.status_code == 409
    assert stopped.status == "blocked"


def test_a_rejected_basket_cannot_be_edited(app):
    client, main = app
    with client:
        _put(main, "b_no", "rejected")
        assert _edit(client, "b_no").status_code == 409


def test_a_signed_basket_can_still_be_edited(app):
    """Deliberately still allowed: the chef saw Aimo's price and changed their
    mind. That is what confirming before approving is for, and the edit voids
    the quote rather than being refused."""
    client, main = app
    with client:
        quoted = _put(main, "b_quote", "quoted")
        response = _edit(client, "b_quote")

    assert response.status_code != 409, "editing after seeing the price was refused"


# ------------------------------------------------- the same rule, twice (B)


def test_the_same_rule_twice_is_refused(app):
    client, _ = app
    with client:
        assert client.post("/api/rules", json=A_RULE, headers=OWNER).status_code == 200
        second = client.post("/api/rules", json=A_RULE, headers=OWNER)
        assert second.status_code == 409, second.status_code
        assert "already have" in second.json()["detail"]
        assert len(client.get("/api/rules").json()["rules"]) == 1


def test_the_label_is_not_what_makes_a_rule_different(app):
    """Two rules named differently that match the same thing with the same
    ceiling do exactly the same work; a list holding both cannot be reasoned
    about."""
    client, _ = app
    with client:
        client.post("/api/rules", json={**A_RULE, "name": "milk weekdays"},
                    headers=OWNER).raise_for_status()
        second = client.post("/api/rules", json={**A_RULE, "name": "milk weekends"},
                             headers=OWNER)
        assert second.status_code == 409


def test_case_and_spacing_do_not_make_a_new_rule(app):
    """The name matcher lowercases, so 'Milk' and 'milk ' are one rule."""
    client, _ = app
    with client:
        client.post("/api/rules", json=A_RULE, headers=OWNER).raise_for_status()
        second = client.post("/api/rules", json={**A_RULE, "value": "  Milk "},
                             headers=OWNER)
        assert second.status_code == 409


def test_a_different_ceiling_is_a_different_rule(app):
    """Not a duplicate: the owner is saying something new about how much."""
    client, _ = app
    with client:
        client.post("/api/rules", json=A_RULE, headers=OWNER).raise_for_status()
        other = client.post("/api/rules", json={**A_RULE, "max_net_per_order": 500.0},
                            headers=OWNER)
        assert other.status_code == 200
        assert len(client.get("/api/rules").json()["rules"]) == 2


def test_a_different_product_is_a_different_rule(app):
    client, _ = app
    with client:
        client.post("/api/rules", json=A_RULE, headers=OWNER).raise_for_status()
        other = client.post("/api/rules", json={**A_RULE, "value": "cream"}, headers=OWNER)
        assert other.status_code == 200


def test_a_rule_switched_off_can_be_added_again(app):
    """Rules are deactivated rather than deleted, so an order made under one
    stays readable. The owner must still be able to turn it back on."""
    client, _ = app
    with client:
        made = client.post("/api/rules", json=A_RULE, headers=OWNER)
        made.raise_for_status()
        client.delete(f"/api/rules/{made.json()['rule_id']}", headers=OWNER).raise_for_status()

        again = client.post("/api/rules", json=A_RULE, headers=OWNER)
        assert again.status_code == 200, "a switched-off rule could not be re-added"
        assert len(client.get("/api/rules").json()["rules"]) == 1


def test_two_any_rules_with_one_ceiling_are_the_same_rule(app):
    """`any` ignores the value, so the value must not make them look different."""
    client, _ = app
    with client:
        blanket = {"name": "small orders", "match": "any", "value": "",
                   "max_net_per_order": 50.0}
        client.post("/api/rules", json=blanket, headers=OWNER).raise_for_status()
        second = client.post("/api/rules", json={**blanket, "name": "petty cash"},
                             headers=OWNER)
        assert second.status_code == 409


# --------------------------------------------- how a rule reads on the screen


def test_a_rule_reads_as_a_sentence_not_a_python_list():
    """The policy step used to format the reasons list straight into an
    f-string, so the chef's log carried brackets and quote marks that
    `narrate.humanise` then stripped back out."""
    from datetime import date

    from kitchen import narrate
    from kitchen.agent import _policy_detail
    from shared.schemas import Basket, PolicyDecision

    rule = rules.Rule(name="milk", match="name", value="milk", max_net_per_order=100.0)
    basket = Basket(basket_id="b_1", requested_date=date(2026, 11, 16))
    basket.policy = PolicyDecision(
        level="auto_ok", binding=False,
        reasons=[f"Your rule allows it: {rule.said_short()}, no approval needed"])

    line = narrate.sentence("policy_preview", _policy_detail(basket))
    assert line == ("Checked your own ordering rules: Your rule allows it: "
                    "milk up to €100.00, no approval needed.")
    for machinery in ("[", "]", "auto_ok", "'"):
        assert machinery not in line, f"{machinery!r} is still in the chef's line"


def test_a_rule_that_needs_a_person_still_names_who():
    from datetime import date

    from kitchen import narrate
    from kitchen.agent import _policy_detail
    from shared.schemas import Basket, PolicyDecision

    basket = Basket(basket_id="b_1", requested_date=date(2026, 11, 16))
    basket.policy = PolicyDecision(level="manager", binding=False,
                                   reasons=["Over the EUR 500 chef limit"])
    assert "manager" in _policy_detail(basket), "the level was dropped from the facts"


def test_the_short_form_of_every_kind_of_rule():
    """`said_short` is read beside words that already say it is a rule, so it
    must not repeat 'Approve ... without asking'."""
    for match, value, expected in (
        ("name", "milk", "milk up to EUR 100.00"),
        ("product", "464306", "product 464306 up to EUR 100.00"),
        ("category", "12345", "category 12345 up to EUR 100.00"),
        ("claim", "FREE_FROM_LACTOSE", "free from lactose up to EUR 100.00"),
        ("any", "", "any order up to EUR 100.00"),
    ):
        rule = rules.Rule(match=match, value=value, max_net_per_order=100.0)
        assert rule.said_short() == expected, rule.said_short()
        assert "without asking" not in rule.said_short()

    no_ceiling = rules.Rule(match="name", value="milk")
    assert no_ceiling.said_short() == "milk, any amount"
