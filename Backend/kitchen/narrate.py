"""Turn a step into something a chef would actually read.

Two different people read this log.

A chef wants a short story: what they asked for, what was bought, what it cost,
what needs their attention. They do not know what a signature, a mandate or a
quote is, and they should not have to. So the sentences here use kitchen words -
"locked in the price", "you approved it" - and the steps that only make sense to
an engineer are marked `for_chef = False` so the screen can leave them out until
somebody asks for them.

Aimo's side of the log is the opposite and stays that way: full requests,
responses, timings and token ids, in gateway/audit.py.

Each step keeps its machine label and its terse facts either way. Only the
wording and the visibility change here, so nothing is lost - the detail is one
toggle away.
"""
from __future__ import annotations

import re
from datetime import date

# Claim codes as a person would say them.
CLAIMS = {
    "FREE_FROM_LACTOSE": "lactose-free",
    "FREE_FROM_GLUTEN": "gluten-free",
    "VEGAN": "vegan",
    "VEGETARIAN": "vegetarian",
    "ORGANIC": "organic",
}

# What a chef sees without asking. The rest is there, just not in the way.
FOR_CHEF = {
    "parse_need", "rank", "split_need", "suggest_cover", "reorder",
    "message", "policy_preview", "confirm", "sign_approval", "order_placed", "named_product", "change_date", "edit_basket", "respec", "rule_added", "rule_removed", "auto_order",
    "order_refused", "order_adjusted", "reprice",
}

TEMPLATES = {
    # what was asked for
    "message": "You typed: {}.",
    "parse_need": "You asked for {}.",
    "split_need": "No single product could cover that on its own, so it was split across more than one: {}.",

    # choosing
    "rank": "Compared what Aimo can actually deliver and picked the best value: {}.",
    "search_products": "Looked through Aimo's catalogue: {}.",
    "check_delivery": "Checked today's price and the delivery date with Aimo: {}.",
    "convert": "Worked out how many packs that comes to: {}.",
    "reprice": "Checked the prices again, because the basket changed: {}.",
    "suggest_cover": "Looked for something to make up the shortfall: {}.",
    "reorder": "Built this from what you ordered last time: {}.",
    "change_date": "You moved the whole basket to another delivery day: {}.",
    "edit_basket": "You changed the basket by asking: {}.",
    "respec": "You asked for a different product on that line: {}.",
    "auto_order": "No one had to approve this, because your own rule already did: {}.",
    "rule_added": "You set a standing rule: {}.",
    "rule_removed": "You switched a standing rule off: {}.",
    "named_product": "You named a product by number, so that is the one used: {}.",

    # the kitchen's own rules
    "policy_preview": "Checked your own ordering rules: {}.",
    "policy_binding": "Checked your ordering rules against the final price: {}.",

    # trust, in kitchen words
    "create_quote": "Asked Aimo to lock in this price: {}.",
    "confirm": "Aimo locked in the price, so it cannot change while you decide: {}.",
    "verify_quote": "Confirmed the price really came from Aimo and had not been altered: {}.",
    "sign_approval": "{}.",

    # the order
    "place_order": "Sent the order to Aimo: {}.",
    "order_placed": "Aimo accepted the order: {}.",
    "order_refused": "Aimo did not accept the order: {}.",
    "order_adjusted": "Aimo's answer had changed, so the basket was adjusted to what it will actually accept: {}.",
}

BARE = {
    "verify_quote": "Confirmed the price really came from Aimo and had not been altered.",
    "sign_approval": "You approved this basket.",
    "order_placed": "Aimo accepted the order.",
    "confirm": "Aimo locked in the price, so it cannot change while you decide.",
}

# Why a product was dropped, as a person would say it.
REASONS = {
    "size_in_KG_but_need_in_L": "sold by weight, not volume",
    "size_in_L_but_need_in_KG": "sold by volume, not weight",
    "past_closing_time": "today's cut-off had passed",
    "past_cutoff": "the cut-off for that day had passed",
    "sales_block": "not available to order",
    "no_price": "no price from Aimo",
    "partial_only": "not enough in stock",
    "unknown_size": "pack size unclear",
    "not_deliverable": "cannot be delivered that day",
    "stock_changed": "the stock had moved since the price was locked in",
    "quote_expired": "the locked-in price had expired",
    "over_order_limit": "it is over your limit for a single order",
    "over_daily_limit": "it is over your budget for today",
    "over_weekly_limit": "it is over your limit for the week",
}

# Fragments that read badly word for word.
PHRASES = {
    "within the chef limit": "no extra approval needed",
    "chef: no extra approval needed": "no extra approval needed",
    " dropped ": " set aside because ",
    "orderable": "could be ordered",
}

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def _spoken_date(match: re.Match) -> str:
    """2026-11-17 -> Tuesday 17 November. A chef reads days, not ISO dates."""
    try:
        when = date.fromisoformat(match.group(0))
    except ValueError:
        return match.group(0)
    return f"{WEEKDAYS[when.weekday()]} {when.day} {MONTHS[when.month - 1]}"


def humanise(text: str) -> str:
    """Strip the machine out of a line of facts without losing any of them."""
    out = text or ""
    for code, said in CLAIMS.items():
        out = out.replace(code, said)
    for code, said in REASONS.items():
        out = out.replace(code, said)
    # A quote id means nothing to a chef; an order id does, so that one stays.
    out = re.sub(r"\bquote\s+q_[0-9a-f]+", "this basket", out)
    out = re.sub(r"\bq_[0-9a-f]+\b", "this basket", out)
    # A date, but never one inside a timestamp (2026-11-14T18:49 stays put).
    out = re.sub(r"\d{4}-\d{2}-\d{2}(?![T:\d])", _spoken_date, out)
    out = re.sub(r"\bEUR\s*([\d,]+\.?\d*)", r"€\1", out)
    # The list and quote marks around what was searched for help nobody.
    out = out.replace("[", "").replace("]", "").replace("'", "")
    # Tidy the spacing around commas, but never inside a number: this used to
    # turn "1,000 L" into "1, 000 L".
    out = re.sub(r"(?<!\d)\s*,\s*(?!\d)", ", ", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    for fragment, said in PHRASES.items():
        out = out.replace(fragment, said)
    # "chef: no extra approval needed" -> drop the role prefix, it repeats the rule.
    out = re.sub(r"^(chef|manager|owner):\s*", "", out)
    return out.strip(" ,:")


def is_for_chef(step: str) -> bool:
    """Whether this row belongs in the short version."""
    return step in FOR_CHEF


def sentence(step: str, detail: str = "") -> str:
    """One line, in words a chef would use. Never raises, never empty."""
    facts = humanise(detail).rstrip(".")
    if not facts:
        return BARE.get(step) or _readable(step) + "."
    template = TEMPLATES.get(step)
    if template is None:
        return f"{_readable(step)}: {facts}."
    return template.format(facts)


def _readable(step: str) -> str:
    words = (step or "step").replace("_", " ").strip()
    return words[:1].upper() + words[1:]
