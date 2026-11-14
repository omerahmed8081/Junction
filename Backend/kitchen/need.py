"""Turn the chef's sentence into a structured need.

This is the only place the model is used on the way to a basket, and all it
produces is this small object. It sees the request text and nothing else: no
recipes, no sales, no stock. Every number after this point is computed in code.

parse_need() tries Gemini and falls back to the rule-based parser, so the demo
still runs if the model call fails or there is no key. Whoever owns the agent
track can replace parse_with_gemini() freely: keep the ParsedNeed shape.
"""
from __future__ import annotations

import json
import os
import sys
import re
from datetime import date, timedelta

from pydantic import BaseModel, Field

KNOWN_CLAIMS = {
    "FREE_FROM_LACTOSE": r"lactose[\s-]*free|laktoositon|laktosfri",
    "FREE_FROM_GLUTEN": r"gluten[\s-]*free|gluteeniton|glutenfri",
    "VEGAN": r"\bvegan\b|vegaani",
}
KNOWN_BRANDS = ["valio", "arla", "atria", "fazer", "saarioinen", "hk", "pirkka", "kotimaista"]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
UNIT_WORDS = {
    "l": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L",
    "dl": "DL", "cl": "CL", "ml": "ML",
    "kg": "KG", "kilo": "KG", "kilos": "KG", "g": "G",
    "case": "KI", "cases": "KI", "ki": "KI", "pcs": "ST", "st": "ST", "units": "ST",
}
_UNITS = "|".join(sorted(UNIT_WORDS, key=len, reverse=True))
_NUMBER = r"\d{1,3}(?:[ ,.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
DEFAULT_LEAD_DAYS = 3


class ParsedNeed(BaseModel):
    product_text: str                           # what to search for, in English, without claim or brand words
    quantity: float
    unit: str                                   # L, DL, CL, ML, KG, G, or a sales unit (KI, ST)
    claims: list[str] = Field(default_factory=list)
    pack_size: float | None = None              # "in 10 L packs" -> 10
    pack_size_unit: str = ""
    brand: str | None = None
    # A product number typed straight into the chat ("40 units of 464306").
    # When it is set the agent orders that product and does not pick for you.
    product_id: str | None = None
    requested_date: date
    parser: str = "rules"                       # which parser produced this, for the trace


def _number(text: str) -> float:
    text = text.strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+", text):          # 1,000 or 1.000 is a thousand
        return float(re.sub(r"[,.]", "", text))
    return float(text.replace(",", "."))


def _date(text: str, today: date) -> tuple[date, str]:
    """The delivery date asked for, and the text with the date phrase removed."""
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso:
        return date.fromisoformat(iso.group(1)), text.replace(iso.group(0), " ")
    if re.search(r"\btomorrow\b", text):
        return today + timedelta(days=1), re.sub(r"\b(by|for|on)?\s*tomorrow\b", " ", text)
    # When a message names two days - "by friday not thursday" - the one meant
    # is the one said first, not the one that happens to come first in the week.
    said = [(m.start(), index, name)
            for index, name in enumerate(WEEKDAYS)
            for m in [re.search(rf"\b{name}\b", text)] if m]
    if said:
        _, index, name = min(said)
        ahead = (index - today.weekday()) % 7 or 7              # the next one, never today
        return today + timedelta(days=ahead), re.sub(rf"\b(by|for|on|next)?\s*{name}\b", " ", text)
    return today + timedelta(days=DEFAULT_LEAD_DAYS), text


def parse_with_rules(message: str, today: date | None = None) -> ParsedNeed:
    """No model: enough for requests shaped like '1,000 L lactose-free milk by Tuesday'."""
    today = today or date.today()
    text = " " + message.lower().strip() + " "
    requested, text = _date(text, today)

    # A product number, read before the quantity so neither can be mistaken for
    # the other. Written plainly, or as "#464306" / "id 464306". Six digits, and
    # not immediately followed by a unit, which would make it an amount.
    product_id = None
    found_id = re.search(rf"(?:\bid\s*|\bproduct\s*|#)?\b(\d{{6}})\b(?!\s*(?:{_UNITS})\b)", text)
    if found_id:
        product_id = found_id.group(1)
        text = text.replace(found_id.group(0), " ", 1)

    pack_size, pack_unit = None, ""
    pack = re.search(rf"\bin\s+({_NUMBER})\s*({_UNITS})\s*(?:packs?|boxes|box|cartons?|bags?)\b", text)
    if pack:
        pack_size, pack_unit = _number(pack.group(1)), UNIT_WORDS[pack.group(2)]
        text = text.replace(pack.group(0), " ")

    amount = re.search(rf"({_NUMBER})\s*({_UNITS})\b", text)
    if amount:
        quantity, unit = _number(amount.group(1)), UNIT_WORDS[amount.group(2)]
        text = text.replace(amount.group(0), " ", 1)
    elif product_id:
        # The product is named outright, so a bare number is how many of it.
        bare = re.search(rf"({_NUMBER})", text)
        if not bare:
            raise ValueError(f"How many of {product_id} do you need?")
        quantity, unit = _number(bare.group(1)), "ST"
        text = text.replace(bare.group(0), " ", 1)
    else:
        raise ValueError("I could not find a quantity and unit, for example '1,000 L' or '20 kg'.")

    claims = []
    for claim, pattern in KNOWN_CLAIMS.items():
        if re.search(pattern, text):
            claims.append(claim)
            text = re.sub(pattern, " ", text)

    brand = next((b for b in KNOWN_BRANDS if re.search(rf"\b{b}\b", text)), None)
    if brand:
        text = re.sub(rf"\b{brand}\b", " ", text)

    # Contractions first: "we're low on cream" left "re" behind as a product word,
    # because the filler below matched "we" and the apostrophe was stripped after.
    text = re.sub(r"[\u2019']\s*(re|s|m|ve|ll|d)\b", " ", text)
    text = re.sub(r"\bn[\u2019']?t\b", " ", text)
    filler = (r"\b(i|we|you|they|it|need|needs|needed|want|wants|order|please|of|some|the|a|an"
              r"|by|for|get|gets|got|me|us|to|buy|would|like|am|is|are|was|were|be|been"
              # how people actually ask: "we're low on cream", "running out of butter"
              r"|low|lower|run|runs|running|out|short|more|another|again|on|and|but|so|then|just)\b")
    product = " ".join(re.sub(filler, " ", re.sub(r"[^a-zà-ÿ0-9 ]", " ", text)).split())
    if product_id:
        # The number is the request; whatever words are left are noise.
        product = product_id
    if not product:
        raise ValueError("I could not tell which product you need.")
    return ParsedNeed(
        product_text=product, quantity=quantity, unit=unit, claims=claims,
        pack_size=pack_size, pack_size_unit=pack_unit if pack_size else "",
        brand=brand.title() if brand else None, product_id=product_id,
        requested_date=requested, parser="rules",
    )


PROMPT = """You turn a kitchen's purchase request into JSON. Today is {today} ({weekday}).

A message often asks for more than one thing ("40 L milk and 5 kg rice by Tuesday").
Reply with one JSON object and nothing else, shaped
{{"intent": "add", "items": [ ... ]}}, holding one
entry per product asked for, in the order they were asked for. One product, one entry.
A date or a dietary word that applies to the whole message applies to every entry.

Each entry has exactly these keys:
- "product_text": the product to search for, in English, 1 to 4 words, without quantity, date, brand or dietary words
- "quantity": number
- "unit": one of L, DL, CL, ML, KG, G, KI (cases), ST (single units)
- "claims": list, only from {claims}
- "pack_size": number or null, only if a pack size was asked for ("in 10 L packs" is 10)
- "pack_size_unit": L or KG, or "" when pack_size is null
- "brand": brand name or null
- "product_id": the six-digit product number if the message names one, otherwise null
- "requested_date": the delivery date asked for as YYYY-MM-DD; a weekday means the next one after today; if none is given use {default}

{basket}
Request: {message}"""

# Added to the prompt only when a basket is already open. Without it the model
# has no idea what "these" or "them" refers to.
BASKET_CONTEXT = """
The kitchen already has this basket open, for delivery on {requested_date}:
{lines}

If the message changes THAT basket rather than asking for something new, reply
{{"intent": "change_date", "requested_date": "YYYY-MM-DD"}} - for example "I need
these by Friday not Thursday", "move it to Monday", "can that come a day later".
Otherwise reply {{"intent": "add", "items": [ ... ]}} as described above.
"""


def parse_with_gemini(message: str, today: date | None = None) -> ParsedNeed:
    """The first need in the message. Kept for callers that want exactly one."""
    return parse_many_with_gemini(message, today)[0]


class ChatIntent(BaseModel):
    """What a message was asking for, once there is a basket to refer to."""

    intent: str = "add"                         # "add" | "change_date"
    items: list[ParsedNeed] = Field(default_factory=list)
    requested_date: date | None = None
    parser: str = "rules"


def basket_context(basket) -> str:
    """The open basket, written out so "these" and "them" mean something."""
    if basket is None or not getattr(basket, "lines", None):
        return ""
    lines = "\n".join(
        f"- {l.quantity_sales_units:g} {l.sales_unit} of {l.name} (product {l.product_id})"
        for l in basket.lines)
    return BASKET_CONTEXT.format(requested_date=basket.requested_date, lines=lines)


def parse_many_with_gemini(message: str, today: date | None = None,
                           basket=None) -> list[ParsedNeed]:
    """Every need in one message. One model call. No temperature or top_p: the
    3.5 models ignore them and later ones reject them."""
    from google import genai

    today = today or date.today()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash-lite"
    prompt = PROMPT.format(
        today=today.isoformat(), weekday=WEEKDAYS[today.weekday()].title(),
        claims=sorted(KNOWN_CLAIMS), default=(today + timedelta(days=DEFAULT_LEAD_DAYS)).isoformat(),
        basket=basket_context(basket), message=message.strip(),
    )
    if hasattr(client, "interactions"):
        try:
            text = client.interactions.create(model=model, input=prompt).output_text
        except Exception:  # noqa: BLE001 - older call style below
            text = client.models.generate_content(model=model, contents=prompt).text
    else:
        text = client.models.generate_content(model=model, contents=prompt).text

    return needs_from_reply(text)


def _gemini_text(message: str, today: date | None, basket) -> str:
    """The raw model reply, with the open basket in the prompt."""
    from google import genai

    today = today or date.today()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash-lite"
    prompt = PROMPT.format(
        today=today.isoformat(), weekday=WEEKDAYS[today.weekday()].title(),
        claims=sorted(KNOWN_CLAIMS), default=(today + timedelta(days=DEFAULT_LEAD_DAYS)).isoformat(),
        basket=basket_context(basket), message=message.strip(),
    )
    if hasattr(client, "interactions"):
        try:
            return client.interactions.create(model=model, input=prompt).output_text
        except Exception:  # noqa: BLE001 - older call style below
            return client.models.generate_content(model=model, contents=prompt).text
    return client.models.generate_content(model=model, contents=prompt).text


def intent_from_reply(text: str | None, basket=None) -> ChatIntent:
    """What the model made of the message: a change to the open basket, or needs."""
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    if not match:
        raise ValueError("model reply held no JSON object")
    payload = json.loads(match.group(0))
    if isinstance(payload, dict) and payload.get("intent") == "change_date":
        when = payload.get("requested_date")
        if when and basket is not None:
            return ChatIntent(intent="change_date", requested_date=date.fromisoformat(str(when)),
                              parser="gemini")
        # A date change with nothing to change is not an answer.
        raise ValueError("the model asked to change a date with no basket open")
    return ChatIntent(intent="add", items=needs_from_reply(text), parser="gemini")


def needs_from_reply(text: str | None) -> list[ParsedNeed]:
    """The needs inside a model reply. Separated out so it can be tested
    without calling the model, which is where the surprises live."""
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    if not match:
        raise ValueError("model reply held no JSON object")
    payload = json.loads(match.group(0))
    # Accept a bare object too, so an older prompt or a terse reply still works.
    entries = payload.get("items") if isinstance(payload, dict) else None
    if entries is None:
        entries = [payload]
    if not isinstance(entries, list) or not entries:
        raise ValueError("model reply held no items")

    needs: list[ParsedNeed] = []
    for data in entries[:MAX_ITEMS_PER_MESSAGE]:
        if not isinstance(data, dict):
            continue
        data = dict(data)
        data["claims"] = [c for c in data.get("claims") or [] if c in KNOWN_CLAIMS]
        data["unit"] = str(data.get("unit", "")).upper()
        data["pack_size_unit"] = str(data.get("pack_size_unit") or "").upper()
        data["parser"] = "gemini"
        try:
            need = ParsedNeed(**data)
        except Exception:                     # noqa: BLE001 - one bad entry, keep the rest
            continue
        if need.quantity <= 0 or not need.product_text.strip():
            continue
        needs.append(need)
    if not needs:
        raise ValueError("model reply had no usable quantity or product")
    return needs


MAX_ITEMS_PER_MESSAGE = 10


def date_only_change(message: str, today: date | None = None) -> date | None:
    """A follow-up that only moves the delivery date.

    The rules parser cannot hold a conversation, but it can tell that a message
    names a day and asks for no quantity, which is what "make it Friday" is.
    """
    today = today or date.today()
    text = " " + message.lower().strip() + " "
    if re.search(rf"({_NUMBER})\s*({_UNITS})\b", text):
        return None                              # it asks for an amount: a new need

    # A day has to be named outright. _date falls back to a default lead time
    # when it finds nothing, and "a day later" must not silently become that.
    named_day = (re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
                 or re.search(r"\btomorrow\b", text)
                 or re.search(rf"\b({'|'.join(WEEKDAYS)})\b", text))
    if not named_day:
        return None

    # Either it points at the basket ("these", "instead"), or the whole message
    # is just the day, which after a basket is open means the same thing.
    points_at_basket = re.search(
        r"\b(these|them|those|it|that|this|they|basket|order|delivery|deliver|"
        r"instead|rather|not|change|move|make)\b", text)
    if not points_at_basket and len(text.split()) > 3:
        return None
    return _date(text, today)[0]


def parse_chat(message: str, today: date | None = None, basket=None) -> ChatIntent:
    """What this message means, given whatever basket is already open."""
    if os.environ.get("GEMINI_API_KEY") and os.environ.get("KITCHEN_PARSER", "gemini") != "rules":
        try:
            return _gemini_intent(message, today, basket)
        except Exception as exc:  # noqa: BLE001
            print(f"[kitchen] Gemini unavailable, using the rules parser: "
                  f"{type(exc).__name__}: {exc}"[:300], file=sys.stderr)
    if basket is not None and getattr(basket, "lines", None):
        moved = date_only_change(message, today)
        if moved:
            return ChatIntent(intent="change_date", requested_date=moved, parser="rules")
    return ChatIntent(intent="add", items=[parse_with_rules(message, today)], parser="rules")


def _gemini_intent(message: str, today: date | None, basket) -> ChatIntent:
    """One model call that may answer with a change instead of a list of needs."""
    text = _gemini_text(message, today, basket)
    return intent_from_reply(text, basket)


def parse_needs(message: str, today: date | None = None) -> list[ParsedNeed]:
    """Every product asked for in one message, in the order they were asked for.

    Nobody shops one item at a time. Gemini returns a list; the rules parser
    only ever finds one, which is why a model is worth having here.
    """
    if os.environ.get("GEMINI_API_KEY") and os.environ.get("KITCHEN_PARSER", "gemini") != "rules":
        try:
            return parse_many_with_gemini(message, today)
        except Exception as exc:  # noqa: BLE001 - any model trouble: the rules still give a basket
            print(f"[kitchen] Gemini unavailable, using the rules parser: "
                  f"{type(exc).__name__}: {exc}"[:300], file=sys.stderr)
    return [parse_with_rules(message, today)]


def parse_need(message: str, today: date | None = None) -> ParsedNeed:
    """Gemini when a key is set and the call works, the rules otherwise."""
    if os.environ.get("GEMINI_API_KEY") and os.environ.get("KITCHEN_PARSER", "gemini") != "rules":
        try:
            return parse_with_gemini(message, today)
        except Exception as exc:  # noqa: BLE001 - any model trouble: the rules still give a basket
            # Silence here once cost us a demo: a wrong GEMINI_MODEL fell back to the
            # rules on every message and nothing said so. The reply also carries
            # `parser`, so the trace records which one actually ran.
            print(f"[kitchen] Gemini unavailable, using the rules parser: "
                  f"{type(exc).__name__}: {exc}"[:300], file=sys.stderr)
    return parse_with_rules(message, today)
