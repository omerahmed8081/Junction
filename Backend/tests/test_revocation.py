"""Stopping an agent (gateway/revocations.py, the DELETE on /api/mandate).

A mandate is a signed statement that stands on its own, so "stop the agent"
cannot be something the kitchen only does to its own copy: whoever holds the
token could keep ordering until it expired. These tests pin the parts that make
it real - Aimo refusing it, nobody else being able to ask, and the kitchen not
letting go of its copy until Aimo has agreed.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("TTA_SKIP_DOTENV", "1")

from fastapi.testclient import TestClient  # noqa: E402

import gateway.main as gateway_main  # noqa: E402
import kitchen.main as kitchen_main  # noqa: E402
from gateway import agents  # noqa: E402
from gateway.revocations import RevocationList  # noqa: E402
from kitchen.gateway_client import GatewayError  # noqa: E402
from kitchen.ordering import KitchenSigner, OrderingError  # noqa: E402
from test_orders import CUSTOMER, make_mandate, order, refused  # noqa: E402

NOW = datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc)


# ---- the list itself ---------------------------------------------------

def test_a_revoked_mandate_is_known_and_others_are_not():
    revoked = RevocationList(pool=None)
    revoked.revoke("m_stopped", CUSTOMER)
    assert revoked.is_revoked("m_stopped") is True
    assert revoked.is_revoked("m_other") is False
    assert revoked.is_revoked("") is False


def test_revoking_twice_is_not_an_error():
    """The kitchen may retry; the answer has to be the same either way."""
    revoked = RevocationList(pool=None)
    revoked.revoke("m_1", CUSTOMER, reason="first")
    revoked.revoke("m_1", CUSTOMER, reason="again")
    assert revoked.is_revoked("m_1") and len(revoked) == 1


def test_a_database_that_is_down_never_turns_into_a_false_revocation():
    """Answering "revoked" because the database is unreachable would refuse
    every order in the building. Unknown means not revoked, loudly."""

    class Broken:
        def connection(self, timeout=None):
            raise RuntimeError("database is away")

    revoked = RevocationList(pool=Broken())
    assert revoked.is_revoked("m_never_seen") is False
    assert revoked.degraded is True
    # ...but one revoked in this process is still refused, database or not.
    revoked.revoke("m_stopped", CUSTOMER)
    assert revoked.is_revoked("m_stopped") is True


def test_a_customer_only_sees_their_own():
    revoked = RevocationList(pool=None)
    revoked.revoke("m_ours", "123")
    revoked.revoke("m_theirs", "456")
    assert [r["mandate_jti"] for r in revoked.for_customer("123")] == ["m_ours"]
    assert [r["mandate_jti"] for r in revoked.for_customer("456")] == ["m_theirs"]


# ---- Aimo refusing it --------------------------------------------------

def test_an_order_on_a_revoked_mandate_is_refused_as_a_security_event():
    revoked = RevocationList(pool=None)
    mandate = make_mandate()
    accepted = order(mandate=mandate)                  # the same mandate, before
    assert accepted.status == "placed"

    from shared.signing import read_payload_unverified
    revoked.revoke(read_payload_unverified(mandate)["jti"], CUSTOMER)
    reason, security = refused(mandate=mandate, key="idem-2", revoked=revoked)
    assert reason == "mandate_revoked" and security


def test_another_customers_revocation_does_not_stop_us():
    revoked = RevocationList(pool=None)
    revoked.revoke("m_somebody_elses", "456")
    record = order(mandate=make_mandate(), revoked=revoked)
    assert record.status == "placed"


# ---- the kitchen's own copy -------------------------------------------

def test_the_kitchen_does_not_hand_itself_a_new_mandate_after_stopping():
    """`mandate is None` means "none yet" and is answered by signing one. After
    a revocation that would start the agent again a second later."""
    kitchen = KitchenSigner()
    kitchen.issue_mandate(now=NOW)
    kitchen.revoke()
    try:
        kitchen.mandate_in_force(now=NOW)
    except OrderingError as exc:
        assert "stopped" in str(exc)
    else:
        raise AssertionError("a stopped agent was handed a fresh mandate")
    # The owner starting it again is the only way back.
    assert kitchen.issue_mandate(now=NOW) is not None and kitchen.stopped is False


# ---- the endpoint ------------------------------------------------------

class FakeGateway:
    """Records the revoke call, or refuses it."""

    def __init__(self, fail: bool = False):
        self.asked: list[tuple[str, str]] = []
        self.fail = fail

    def revoke_mandate(self, mandate_jws: str, reason: str = "") -> dict:
        if self.fail:
            raise GatewayError("the gateway is not reachable")
        self.asked.append((mandate_jws, reason))
        return {"mandate_jti": "m_x", "revoked_at": NOW.isoformat(), "degraded": False}


class Kitchen:
    """The kitchen app with its gateway and saving replaced."""

    def __init__(self, gateway):
        self.gateway = gateway

    def __enter__(self):
        self.before = (kitchen_main.gateway, kitchen_main._save, kitchen_main.kitchen)
        kitchen_main.gateway = self.gateway
        kitchen_main._save = lambda: None
        kitchen_main.kitchen = KitchenSigner()
        kitchen_main.kitchen.issue_mandate()
        self.client = TestClient(kitchen_main.app)
        return self

    def __exit__(self, *exc):
        kitchen_main.gateway, kitchen_main._save, kitchen_main.kitchen = self.before
        return False


OWNER = {"X-Kitchen-Role": "owner"}


def test_only_the_owner_can_stop_the_agent():
    with Kitchen(FakeGateway()) as k:
        for role in ("chef", "manager", None):
            headers = {"X-Kitchen-Role": role} if role else {}
            assert k.client.delete("/api/mandate", headers=headers).status_code == 403, role
        assert kitchen_main.kitchen.stopped is False      # nothing happened
        assert k.gateway.asked == []


def test_stopping_tells_aimo_and_then_lets_go():
    with Kitchen(FakeGateway()) as k:
        was = kitchen_main.kitchen.mandate.payload.jti
        r = k.client.delete("/api/mandate", headers=OWNER)
        assert r.status_code == 200 and r.json()["mandate_id"] == was
        assert k.gateway.asked, "Aimo was never told"
        assert kitchen_main.kitchen.stopped is True
        # ...and the screen says there is none, rather than inventing one.
        assert k.client.get("/api/mandate").status_code == 404


def test_if_aimo_cannot_be_told_the_mandate_stays_in_force():
    """Letting go first would leave a mandate the kitchen has forgotten and
    Aimo still accepts: the screen says stopped, the token keeps working."""
    with Kitchen(FakeGateway(fail=True)) as k:
        before = kitchen_main.kitchen.mandate.payload.jti
        r = k.client.delete("/api/mandate", headers=OWNER)
        assert r.status_code == 502
        assert kitchen_main.kitchen.stopped is False
        assert kitchen_main.kitchen.mandate.payload.jti == before
        assert k.client.get("/api/mandate").status_code == 200


def test_stopping_twice_says_so_without_asking_aimo_again():
    with Kitchen(FakeGateway()) as k:
        k.client.delete("/api/mandate", headers=OWNER)
        again = k.client.delete("/api/mandate", headers=OWNER)
        assert again.status_code == 200 and again.json()["mandate_id"] is None
        assert len(k.gateway.asked) == 1


def test_issuing_a_new_mandate_starts_the_agent_again():
    with Kitchen(FakeGateway()) as k:
        k.client.delete("/api/mandate", headers=OWNER)
        made = k.client.post("/api/mandate", json={"max_net_per_order": 1500}, headers=OWNER)
        assert made.status_code == 200
        assert kitchen_main.kitchen.stopped is False
        assert k.client.get("/api/mandate").status_code == 200


# ---- the gateway's endpoint -------------------------------------------

def test_one_customer_cannot_call_back_anothers_mandate():
    """403 specifically, not 422: the mandate has to verify first, or this
    would be passing because the gateway could not read it at all."""
    from kitchen import keys as kitchen_keys_mod
    from shared.signing import Verifier, read_payload_unverified

    from gateway import keys as gateway_keys

    # Addressed to the gateway this test talks to. Another test file points the
    # kitchen's GATEWAY_URL at a fake host at import, and a mandate addressed
    # there is refused on audience - a 422, which would hide the 403 this test
    # is about.
    kitchen_before = kitchen_keys_mod.GATEWAY_URL
    kitchen_keys_mod.GATEWAY_URL = gateway_keys.gateway_issuer()
    try:
        signer = KitchenSigner()
        mandate = signer.issue_mandate()
    finally:
        kitchen_keys_mod.GATEWAY_URL = kitchen_before
    jti = read_payload_unverified(mandate.jws)["jti"]

    # The gateway registers kitchen keys over HTTP at startup; in a test there
    # is nothing to fetch, so hand it this kitchen's public key directly.
    before = gateway_main.kitchen_keys
    gateway_main.kitchen_keys = lambda: Verifier([kitchen_keys_mod.kitchen_keypair().public_jwk])
    try:
        client = TestClient(gateway_main.app)
        theirs = client.post("/gateway/revoke_mandate", json={"mandate_jws": mandate.jws},
                             headers={"X-API-Key": agents.DEMO_SECOND_CLIENT_KEY})
        assert theirs.status_code == 403, theirs.text
        assert gateway_main.revocations.revoked.is_revoked(jti) is False

        # The customer it belongs to can, which proves the 403 was about them
        # and not about the token.
        ours = client.post("/gateway/revoke_mandate", json={"mandate_jws": mandate.jws},
                           headers={"X-API-Key": agents.DEMO_KITCHEN_KEY})
        assert ours.status_code == 200, ours.text
        assert gateway_main.revocations.revoked.is_revoked(jti) is True
    finally:
        gateway_main.kitchen_keys = before


def test_something_that_is_not_a_mandate_is_not_revocable():
    client = TestClient(gateway_main.app)
    for token in ("", "not.a.token", "a.b.c"):
        r = client.post("/gateway/revoke_mandate", json={"mandate_jws": token},
                        headers={"X-API-Key": agents.DEMO_KITCHEN_KEY})
        assert r.status_code == 422, (token, r.status_code)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(passed)
