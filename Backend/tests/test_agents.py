"""Who the gateway thinks is calling (gateway/agents.py).

Aimo prices by contract, so the key a request carries decides which customer's
prices it gets and which mandate it may ride. These tests pin that mapping.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gateway import agents  # noqa: E402


def with_env(value: str | None):
    """Set AGENT_KEYS for one test, and put back whatever was there."""
    before = os.environ.get("AGENT_KEYS")
    if value is None:
        os.environ.pop("AGENT_KEYS", None)
    else:
        os.environ["AGENT_KEYS"] = value
    return before


def restore(before: str | None) -> None:
    if before is None:
        os.environ.pop("AGENT_KEYS", None)
    else:
        os.environ["AGENT_KEYS"] = before


def test_the_two_demo_agents_are_available_without_configuration():
    before = with_env(None)
    try:
        kitchen = agents.lookup(agents.DEMO_KITCHEN_KEY)
        second = agents.lookup(agents.DEMO_SECOND_CLIENT_KEY)
        assert kitchen and second
        assert kitchen.agent_id == "agent:kitchen-demo" and kitchen.customer_id == "123"
        assert second.agent_id == "agent:second-client"
        # The second client must be a different customer, or the demo proves nothing.
        assert second.customer_id != kitchen.customer_id
    finally:
        restore(before)


def test_no_key_and_unknown_keys_are_nobody():
    before = with_env(None)
    try:
        assert agents.lookup(None) is None
        assert agents.lookup("") is None
        assert agents.lookup("guess") is None
        # A near miss is still a miss.
        assert agents.lookup(agents.DEMO_KITCHEN_KEY.upper()) is None
    finally:
        restore(before)


def test_surrounding_whitespace_in_a_key_is_ignored():
    before = with_env(None)
    try:
        assert agents.lookup(f"  {agents.DEMO_KITCHEN_KEY}  ") is not None
    finally:
        restore(before)


def test_keys_come_from_the_environment_when_set():
    before = with_env("k1|agent:one|500|Restaurant One, k2|agent:two|501|Restaurant Two")
    try:
        one = agents.lookup("k1")
        assert one and one.customer_id == "500" and one.label == "Restaurant One"
        assert agents.lookup("k2").agent_id == "agent:two"
        # Configuring keys replaces the demo ones rather than adding to them.
        assert agents.lookup(agents.DEMO_KITCHEN_KEY) is None
    finally:
        restore(before)


def test_a_label_is_optional_and_falls_back_to_the_agent_id():
    before = with_env("k1|agent:one|500")
    try:
        assert agents.lookup("k1").label == "agent:one"
    finally:
        restore(before)


def test_a_malformed_entry_is_skipped_and_the_rest_still_load():
    before = with_env("broken, k1|agent:one|500, |agent:blank|502, k3|agent:three|503")
    try:
        assert agents.lookup("broken") is None
        assert agents.lookup("k1") is not None
        assert agents.lookup("k3") is not None
        assert agents.lookup("") is None
    finally:
        restore(before)


def test_a_new_key_is_picked_up_without_a_restart():
    before = with_env("k1|agent:one|500")
    try:
        assert agents.lookup("k9") is None
        os.environ["AGENT_KEYS"] = "k1|agent:one|500, k9|agent:nine|509"
        assert agents.lookup("k9").customer_id == "509"
    finally:
        restore(before)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(passed)
