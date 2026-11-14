"""The kitchen's own standing rules: what may go through without a person.

"Milk under 200 euro, don't ask me." A rule is the kitchen saying, in advance,
that a particular kind of order is routine enough that nobody needs to look at
it. It sits *inside* the mandate, never outside: a rule can only ever relax an
approval the kitchen would otherwise ask for itself, and Aimo still enforces the
signed mandate on top. The worst a wrong rule can do is skip a click.

These stay in the kitchen's schema and Aimo never reads them. What Aimo enforces
is the mandate - limits and categories, signed. Shipping the rule list to Aimo
would hand the wholesaler the customer's internal approval policy, which is the
one thing the whole design is trying not to do.

A rule never overrides a reason to stop. If a line is only partly deliverable,
or the product differs from what was asked for, or the pack size disagrees with
the price, a person still looks - no matter what the rules say.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# Reasons a rule may never wave through: they are about the order being wrong,
# not about it being large.
NEVER_AUTO = {
    "Partial delivery accepted",
    "Product differs from what was asked for",
    "Pack size disagrees with the price data",
}

MATCHES = ("any", "category", "product", "claim", "name")


@dataclass
class Rule:
    """One standing instruction, as a person would say it."""

    rule_id: int | None = None
    name: str = ""
    match: str = "any"                   # any | category | product | claim | name
    value: str = ""                      # the category code, product id, claim or word
    max_net_per_order: float | None = None
    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def said(self) -> str:
        """The rule in a sentence, for the rules screen and the audit log."""
        what = {
            "any": "any order",
            "category": f"anything in category {self.value}",
            "product": f"product {self.value}",
            "claim": f"anything marked {self.value.lower().replace('_', ' ')}",
            "name": f"anything called {self.value!r}",
        }.get(self.match, "any order")
        if self.max_net_per_order is None:
            return f"Approve {what} without asking."
        return f"Approve {what} up to EUR {self.max_net_per_order:,.2f} net without asking."

    def as_json(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "name": self.name, "match": self.match,
                "value": self.value, "max_net_per_order": self.max_net_per_order,
                "active": self.active, "said": self.said(),
                "created_at": self.created_at.isoformat()}


def _line_matches(rule: Rule, line: Any) -> bool:
    if rule.match == "any":
        return True
    if rule.match == "product":
        return str(getattr(line, "product_id", "")) == rule.value
    if rule.match == "category":
        return str(getattr(line, "category", "") or "") == rule.value
    if rule.match == "claim":
        claims = [str(c).upper() for c in (getattr(line, "claims", None) or [])]
        return rule.value.upper() in claims
    if rule.match == "name":
        return rule.value.lower() in str(getattr(line, "name", "")).lower()
    return False


def covers(rule: Rule, basket: Any) -> bool:
    """Whether this rule covers the whole basket.

    Every line has to match. A rule about milk does not wave through a basket
    that also holds something else, because the chef never said anything about
    the something else.
    """
    if not rule.active or not getattr(basket, "lines", None):
        return False
    if rule.max_net_per_order is not None and (basket.net_total or 0) > rule.max_net_per_order:
        return False
    return all(_line_matches(rule, line) for line in basket.lines)


def applies(rules: Iterable[Rule], basket: Any, reasons: list[str]) -> Rule | None:
    """The rule that lets this basket through, or None.

    `reasons` are the policy's own reasons for wanting a person. A rule may
    clear a reason about size; it may never clear one about the order being
    wrong.
    """
    if any(reason in NEVER_AUTO for reason in reasons):
        return None
    for rule in rules:
        if covers(rule, basket):
            return rule
    return None


CREATE = """
INSERT INTO kitchen.rules (name, match_kind, match_value, max_net_per_order, active)
VALUES (%(name)s, %(match)s, %(value)s, %(max_net_per_order)s, %(active)s)
RETURNING id, created_at
"""

SELECT = """
SELECT id, name, match_kind, match_value, max_net_per_order, active, created_at
FROM kitchen.rules
WHERE active OR NOT %(active_only)s
ORDER BY id
"""

DEACTIVATE = "UPDATE kitchen.rules SET active = false WHERE id = %(id)s"


class RuleStore:
    """The kitchen's rules, in its own schema, with a memory fallback."""

    def __init__(self, pool: Any = None) -> None:
        self._pool = pool
        self._memory: "OrderedDict[int, Rule]" = OrderedDict()
        self._next = 1
        self.degraded = False

    def set_pool(self, pool: Any) -> None:
        self._pool = pool

    def add(self, rule: Rule) -> Rule:
        if self._pool is not None:
            try:
                with self._pool.connection(timeout=3.0) as conn:
                    row = conn.execute(CREATE, {
                        "name": rule.name, "match": rule.match, "value": rule.value,
                        "max_net_per_order": rule.max_net_per_order, "active": rule.active,
                    }).fetchone()
                rule.rule_id, rule.created_at = row[0], row[1]
                self.degraded = False
                return rule
            except Exception as exc:                 # noqa: BLE001
                self.degraded = True
                print(f"[kitchen] rule kept in memory only: {type(exc).__name__}: {exc}")
        rule.rule_id = self._next
        self._next += 1
        self._memory[rule.rule_id] = rule
        return rule

    def all(self, active_only: bool = True) -> list[Rule]:
        if self._pool is not None and not self.degraded:
            try:
                with self._pool.connection(timeout=3.0) as conn:
                    rows = conn.execute(SELECT, {"active_only": active_only}).fetchall()
                return [Rule(rule_id=r[0], name=r[1], match=r[2], value=r[3],
                             max_net_per_order=float(r[4]) if r[4] is not None else None,
                             active=r[5], created_at=r[6]) for r in rows]
            except Exception as exc:                 # noqa: BLE001
                self.degraded = True
                print(f"[kitchen] could not read rules: {type(exc).__name__}: {exc}")
        return [r for r in self._memory.values() if r.active or not active_only]

    def remove(self, rule_id: int) -> bool:
        if self._pool is not None and not self.degraded:
            try:
                with self._pool.connection(timeout=3.0) as conn:
                    conn.execute(DEACTIVATE, {"id": rule_id})
                return True
            except Exception as exc:                 # noqa: BLE001
                self.degraded = True
                print(f"[kitchen] could not remove rule {rule_id}: {exc}")
        rule = self._memory.get(rule_id)
        if rule is None:
            return False
        rule.active = False
        return True


store = RuleStore()
