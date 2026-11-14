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
    for index, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b", text):
            ahead = (index - today.weekday()) % 7 or 7          # the next one, never today
            return today + timedelta(days=ahead), re.sub(rf"\b(by|for|on|next)?\s*{name}\b", " ", text)
    return today + timedelta(days=DEFAULT_LEAD_DAYS), text


def parse_with_rules(message: str, today: date | None = None) -> ParsedNeed:
    """No model: enough for requests shaped like '1,000 L lactose-free milk by Tuesday'."""
    today = today or date.today()
    text = " " + message.lower().strip() + " "
    requested, text = _date(text, today)

    pack_size, pack_unit = None, ""
    pack = re.search(rf"\bin\s+({_NUMBER})\s*({_UNITS})\s*(?:packs?|boxes|box|cartons?|bags?)\b", text)
    if pack:
        pack_size, pack_unit = _number(pack.group(1)), UNIT_WORDS[pack.group(2)]
        text = text.replace(pack.group(0), " ")

    amount = re.search(rf"({_NUMBER})\s*({_UNITS})\b", text)
    if not amount:
        raise ValueError("I could not find a quantity and unit, for example '1,000 L' or '20 kg'.")
    quantity, unit = _number(amount.group(1)), UNIT_WORDS[amount.group(2)]
    text = text.replace(amount.group(0), " ", 1)

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
    if not product:
        raise ValueError("I could not tell which product you need.")
    return ParsedNeed(
        product_text=product, quantity=quantity, unit=unit, claims=claims,
        pack_size=pack_size, pack_size_unit=pack_unit if pack_size else "",
        brand=brand.title() if brand else None, requested_date=requested, parser="rules",
    )


PROMPT = """You turn a kitchen's purchase request into JSON. Today is {today} ({weekday}).
Reply with one JSON object and nothing else, with exactly these keys:
- "product_text": the product to search for, in English, 1 to 4 words, without quantity, date, brand or dietary words
- "quantity": number
- "unit": one of L, DL, CL, ML, KG, G, KI (cases), ST (single units)
- "claims": list, only from {claims}
- "pack_size": number or null, only if a pack size was asked for ("in 10 L packs" is 10)
- "pack_size_unit": L or KG, or "" when pack_size is null
- "brand": brand name or null
- "requested_date": the delivery date asked for as YYYY-MM-DD; a weekday means the next one after today; if none is given use {default}

Request: {message}"""


def parse_with_gemini(message: str, today: date | None = None) -> ParsedNeed:
    """One model call. No temperature or top_p: the 3.5 models ignore them and later ones reject them."""
    from google import genai

    today = today or date.today()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash-lite"
    prompt = PROMPT.format(
        today=today.isoformat(), weekday=WEEKDAYS[today.weekday()].title(),
        claims=sorted(KNOWN_CLAIMS), default=(today + timedelta(days=DEFAULT_LEAD_DAYS)).isoformat(),
        message=message.strip(),
    )
    if hasattr(client, "interactions"):
        try:
            text = client.interactions.create(model=model, input=prompt).output_text
        except Exception:  # noqa: BLE001 - older call style below
            text = client.models.generate_content(model=model, contents=prompt).text
    else:
        text = client.models.generate_content(model=model, contents=prompt).text

    match = re.search(r"\{.*\}", text or "", flags=re.S)
    if not match:
        raise ValueError("model reply held no JSON object")
    data = json.loads(match.group(0))
    data["claims"] = [c for c in data.get("claims") or [] if c in KNOWN_CLAIMS]
    data["unit"] = str(data.get("unit", "")).upper()
    data["pack_size_unit"] = str(data.get("pack_size_unit") or "").upper()
    data["parser"] = "gemini"
    need = ParsedNeed(**data)
    if need.quantity <= 0 or not need.product_text.strip():
        raise ValueError("model reply had no usable quantity or product")
    return need


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
