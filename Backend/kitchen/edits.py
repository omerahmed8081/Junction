"""Changing a basket by saying so, rather than by pressing a button.

"make it 20", "drop the rice", "only 10 kg of butter". The buttons on the
basket already do all three; this reads the same intentions out of a sentence
and turns them into the `LineEdit` the basket already understands, so there is
one set of rules for changing a basket and not two.

Finding which line a message means is the fiddly half. A chef says "the rice",
not "product 475214", so a line is matched on the words of its name, on a
product number when one is given, and on "it" when the basket holds only one
line and there is nothing else it could mean. When a message could mean two
lines, nothing is changed and the reply asks which - guessing at somebody's
order is worse than asking.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from shared.schemas import Basket, BasketLine, LineEdit
from shared.units import NEED_TO_KILOS, NEED_TO_LITRES, _round_up_to_lot

# "drop the rice", "take the butter off", "no cream after all"
REMOVE = re.compile(
    # "take the rice off" and "take out the butter" put the product in the
    # middle, so the two halves are matched with the words between them.
    r"\b(remove|delete|drop|cancel|scrap|forget|get\s+rid\s+of|poista|peru)\b"
    r"|\btake\b[^.!?]*\b(off|out|away)\b", re.I)

# "make it 20", "change the milk to 60 L", "only 10 kg of butter"
CHANGE = re.compile(
    r"\b(make|change|set|update|increase|decrease|reduce|raise|lower|only|just"
    r"|muuta|vaihda)\b", re.I)

AMOUNT = re.compile(
    r"(\d[\d\s.,]*)\s*(l|dl|cl|ml|kg|g|ki|st|litres?|liters?|kilos?|kilograms?|cases?|units?|pieces?)?\b",
    re.I)

UNIT_WORDS = {
    "l": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L",
    "dl": "DL", "cl": "CL", "ml": "ML",
    "kg": "KG", "kilo": "KG", "kilos": "KG", "kilogram": "KG", "kilograms": "KG", "g": "G",
    "ki": "KI", "case": "KI", "cases": "KI",
    "st": "ST", "unit": "ST", "units": "ST", "piece": "ST", "pieces": "ST",
}

# Words that never identify a product, so they are not matched against names.
NOISE = {
    "the", "a", "an", "of", "to", "it", "that", "this", "them", "these", "those",
    "make", "change", "set", "update", "remove", "delete", "drop", "cancel",
    "only", "just", "please", "and", "my", "order", "basket", "line", "i", "we",
    "want", "need", "to", "from", "down", "up", "instead", "rather", "all",
}


class Ambiguous(Exception):
    """The message could mean more than one line. Ask rather than guess."""

    def __init__(self, names: list[str]) -> None:
        super().__init__("more than one line matches")
        self.names = names


@dataclass
class Change:
    """What a message asked to do to the basket."""

    action: str                                  # "remove" | "quantity"
    line: BasketLine
    quantity_sales_units: float | None = None
    said: str = ""                               # what the chef typed, for the reply
    flags: list[str] = field(default_factory=list)

    def as_edit(self) -> LineEdit:
        if self.action == "remove":
            return LineEdit(product_id=self.line.product_id, remove=True)
        return LineEdit(product_id=self.line.product_id,
                        quantity_sales_units=self.quantity_sales_units)


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[0-9a-zà-ÿ%]+", (text or "").lower()) if w not in NOISE]


def match_line(basket: Basket, message: str) -> BasketLine | None:
    """Which line the message is about, or None when nothing points at one.

    Raises `Ambiguous` when two lines match equally well, because changing the
    wrong line of somebody's order is worse than asking which they meant.
    """
    lines = basket.lines or []
    if not lines:
        return None

    # A product number is unambiguous, so it wins outright.
    numbered = re.search(r"\b(\d{6})\b", message or "")
    if numbered:
        exact = [l for l in lines if l.product_id == numbered.group(1)]
        if exact:
            return exact[0]

    said = set(_words(message))
    scored: list[tuple[int, BasketLine]] = []
    for line in lines:
        name = set(_words(line.name))
        hits = len(said & name)
        if hits:
            scored.append((hits, line))

    if not scored:
        # "make it 20" with one line in the basket can only mean that line.
        if len(lines) == 1 and re.search(r"\b(it|that|this|the\s+order|the\s+basket)\b",
                                         message or "", re.I):
            return lines[0]
        return None

    best = max(s for s, _ in scored)
    winners = [line for score, line in scored if score == best]
    if len(winners) > 1:
        raise Ambiguous([l.name for l in winners])
    return winners[0]


def _sales_units_for(line: BasketLine, value: float, unit: str) -> tuple[float, list[str]]:
    """A spoken amount as a number of sales units.

    "make it 20" with no unit is 20 of whatever the line is sold in. "60 L" has
    to go back through the pack size, and lands on whole packs, which is the
    same rounding the basket used when the line was first built.
    """
    flags: list[str] = []
    unit = (unit or "").upper()
    if not unit or unit in {line.sales_unit, "ST", "KI"}:
        return float(value), flags

    factor = NEED_TO_LITRES.get(unit) or NEED_TO_KILOS.get(unit)
    size = line.size_per_sales_unit
    if not factor or not size:
        # No pack size to divide by: take the number at face value and say so.
        flags.append("unit_ignored")
        return float(value), flags

    wanted = value * factor
    packs = _round_up_to_lot(math.ceil(round(wanted / size, 6)), getattr(line, "allowed_lot_size", 0) or 0)
    if abs(packs * size - wanted) > 1e-6:
        flags.append("rounded_up_to_whole_packs")
    return float(packs), flags


def read(basket: Basket, message: str) -> Change | None:
    """A change to the open basket, or None when the message is not one.

    Deliberately conservative: a message that does not clearly ask to remove a
    line or set an amount is left alone, and handled as a new request.
    """
    if basket is None or not getattr(basket, "lines", None):
        return None
    text = message or ""

    if REMOVE.search(text):
        line = match_line(basket, text)
        return Change("remove", line, said=text.strip()) if line else None

    if not CHANGE.search(text):
        return None

    found = None
    for candidate in AMOUNT.finditer(text):
        number = candidate.group(1).strip()
        # A six-digit product number is not an amount.
        if re.fullmatch(r"\d{6}", number.replace(" ", "")):
            continue
        found = candidate
        break
    if not found:
        return None

    raw = found.group(1).replace(" ", "").replace(",", ".").rstrip(".")
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0:
        return None

    unit = UNIT_WORDS.get((found.group(2) or "").lower(), "")
    line = match_line(basket, text)
    if line is None:
        return None
    quantity, flags = _sales_units_for(line, value, unit)
    return Change("quantity", line, quantity_sales_units=quantity,
                  said=text.strip(), flags=flags)
