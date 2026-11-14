"""Who is calling the gateway: an API key, and the customer it belongs to.

Aimo prices by contract, so the same product is not the same price for every
customer. That makes the caller's identity part of the answer, not decoration:
a quote is only meaningful once we know whose prices it was built from.

Every call carries `X-API-Key`. There is no anonymous mode, because an agent we
cannot name is an agent we cannot price for. A key maps to one agent and one
customer; an order is then accepted only if the mandate presented names that
same pair, so a stolen key still cannot spend another customer's money.

Keys come from `AGENT_KEYS` as `key|agent_id|customer_id|label`, comma
separated. The two demo agents below are the default so a fresh checkout runs:
the kitchen, and the second client that has a key but no mandate.

In production this is a table, and the customer id is what the gateway uses to
look up that customer's contract pricing with Aimo.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

DEMO_KITCHEN_KEY = "demo-kitchen-key"
DEMO_SECOND_CLIENT_KEY = "demo-second-client-key"

# Read from the same environment the kitchen reads, so the customer the kitchen
# signs a mandate for and the customer this key maps to cannot drift apart. They
# did once: the mandate was then refused as mandate_wrong_customer, with search
# and quoting still working, which is a confusing way to find out.
DEMO_AGENT_ID = os.environ.get("AGENT_ID", "agent:kitchen-demo")
DEMO_CUSTOMER_ID = os.environ.get("AIMO_CUSTOMER_ID", "123")
DEMO_AGENT_NAME = os.environ.get("AGENT_NAME", "Kitchen agent")


def _defaults() -> tuple[str, ...]:
    return (
        f"{DEMO_KITCHEN_KEY}|{DEMO_AGENT_ID}|{DEMO_CUSTOMER_ID}|{DEMO_AGENT_NAME}",
        f"{DEMO_SECOND_CLIENT_KEY}|agent:second-client|"
        f"{os.environ.get('SECOND_CLIENT_CUSTOMER_ID', '456')}|Second client (no mandate)",
    )


DEFAULT_AGENTS = _defaults()


def fingerprint(api_key: str) -> str:
    """Twelve hex characters: enough to tell two agents apart in a log, useless
    for signing in as either."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Caller:
    """The agent behind one request."""

    agent_id: str
    customer_id: str
    label: str
    # A short hash of the key, for the audit log. The key itself is never stored.
    key_fingerprint: str = ""

    def __str__(self) -> str:                    # for logs and the trace
        return f"{self.agent_id} (customer {self.customer_id})"


def _parse(spec: str) -> dict[str, Caller]:
    registry: dict[str, Caller] = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 3 or not all(parts[:3]):
            print(f"[gateway] ignoring malformed AGENT_KEYS entry: {entry[:40]!r}")
            continue
        key, agent_id, customer_id = parts[0], parts[1], parts[2]
        label = parts[3] if len(parts) > 3 else agent_id
        registry[key] = Caller(agent_id=agent_id, customer_id=customer_id, label=label,
                               key_fingerprint=fingerprint(key))
    return registry


def registry() -> dict[str, Caller]:
    """Read every call, so a key added to the environment needs no restart."""
    # _defaults() is read each time, so changing AGENT_ID or AIMO_CUSTOMER_ID in
    # the environment takes effect without editing this file.
    return _parse(os.environ.get("AGENT_KEYS") or ",".join(_defaults()))


def lookup(api_key: str | None) -> Caller | None:
    """The caller for this key, or None when the key is missing or unknown."""
    if not api_key:
        return None
    return registry().get(api_key.strip())
