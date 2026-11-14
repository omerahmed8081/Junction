"""Unit conversion: turn a need ("1,000 L") into a quantity of sales units.

CONTRACT - agreed hour 0, do not change the signature without telling the team.

    convert(product, need_value, need_unit, derived_size_per_sales_unit=None) -> Conversion

One function, used by the agent, the basket and the quote, so every quantity in
the product comes from the same code. See docs/CONTRACTS.md.

Size of one pack is found in this order (first hit wins):
  1. structured      synkkaData.unitConversions[BASE_UNIT_OR_EACH], unit checked
  2. structured_case the CASE entry, divided by the case unit's sizeInBaseUnits
  3. name            the product name, only if it holds exactly one size and no multiplier
  4. derived         catalogPrice / comparisonPrice from the price API (hour 0 finding)
  5. unknown         never guessed: the basket asks for a quantity in cases instead

When a derived size is passed in and an earlier step already answered, the two are
compared: more than 2% apart raises the flag "size_disagrees_with_price".
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# Units we understand. Anything else (H87 pieces, MTR metres, PTN, PR, combined
# units like "KGM H87") is not a size: we fall through rather than guess.
TO_LITRES = {"LTR": 1.0, "DLT": 0.1, "CLT": 0.01, "MLT": 0.001}
TO_KILOS = {"KGM": 1.0, "GRM": 0.001}

# Units as the need is expressed, normalised to litres or kilograms.
NEED_TO_LITRES = {"L": 1.0, "DL": 0.1, "CL": 0.01, "ML": 0.001}
NEED_TO_KILOS = {"KG": 1.0, "G": 0.001}

# Sizes written into product names, e.g. "10 l", "2,5 dl", "720 g".
_NAME_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|cl|dl|l|g|kg)\b", re.IGNORECASE)
_NAME_TO_LITRES = {"ml": 0.001, "cl": 0.01, "dl": 0.1, "l": 1.0}
_NAME_TO_KILOS = {"g": 0.001, "kg": 1.0}
# A name like "4x1,5kg", "100x2 cl" or "25pcs" states a multipack, so the number
# next to the unit is not the size of one pack. 1,168 products look like this.
_NAME_MULTIPLIER = re.compile(r"\d\s*[x×]\s*\d|\d\s*(?:pcs|kpl|pss|kg/)", re.IGNORECASE)

# Units that can be the case level in units[]. If a product has more than one and
# its only size sits on the CASE entry, we cannot tell which one that entry means.
CASE_UNITS = {"KI", "CS"}


@dataclass
class Conversion:
    """What the basket line and the quote line both carry."""

    ok: bool
    reason: str = ""                       # why not, when ok is False
    quantity_sales_units: float = 0.0      # what we order
    sales_unit: str = ""                   # e.g. "KI"
    base_unit: str = ""                    # e.g. "ST"
    base_units_per_sales_unit: float = 1.0
    size_per_base_unit: float | None = None   # in litres or kilograms
    size_per_sales_unit: float | None = None
    size_unit: str = ""                    # "L" or "KG"
    size_source: str = "unknown"           # structured | structured_case | name | derived | unknown
    delivered_amount: float = 0.0          # in the need's unit
    overshoot: float = 0.0                 # delivered minus needed, same unit
    need_value: float = 0.0
    need_unit: str = ""
    flags: list[str] = field(default_factory=list)


def _usable(entry: dict[str, Any] | None) -> tuple[float, str] | None:
    """A size and its kind ("L" or "KG") from one unitConversions entry.

    The unit decides what the number means, whichever field it sits in: 154
    products store a mass in the volume field, and volume also holds H87
    (pieces) and MTR (metres), which are not sizes at all.
    """
    if not entry:
        return None
    for key in ("volume", "netWeight"):
        raw = entry.get(key) or {}
        unit, value = raw.get("unit"), raw.get("value")
        if not unit or not value:
            continue
        if unit in TO_LITRES:
            return value * TO_LITRES[unit], "L"
        if unit in TO_KILOS:
            return value * TO_KILOS[unit], "KG"
    return None


def _size_from_name(product: dict[str, Any]) -> tuple[float, str] | None:
    """The size of one base unit, read from the product name.

    Only when the name holds exactly one size and no multiplier; otherwise the
    number belongs to a multipack and would be wrong.
    """
    for name in (n.get("value", "") for n in product["synkkaData"].get("names", [])):
        if not name or _NAME_MULTIPLIER.search(name):
            continue
        found = {
            (float(m.group(1).replace(",", ".")), m.group(2).lower())
            for m in _NAME_SIZE.finditer(name)
        }
        if len(found) != 1:
            continue
        value, unit = found.pop()
        if unit in _NAME_TO_LITRES:
            return value * _NAME_TO_LITRES[unit], "L"
        return value * _NAME_TO_KILOS[unit], "KG"
    return None


def base_units_per_sales_unit(product: dict[str, Any]) -> float:
    sales_unit = product.get("salesUnit")
    for unit in product.get("units") or []:
        if unit.get("unitId") == sales_unit:
            return float(unit.get("sizeInBaseUnits") or 1.0)
    return 1.0


def _case_size_in_base_units(product: dict[str, Any]) -> float | None:
    cases = [u for u in (product.get("units") or []) if u.get("unitId") in CASE_UNITS]
    if len(cases) != 1:
        return None          # none, or ambiguous (12 products have both KI and CS)
    return float(cases[0].get("sizeInBaseUnits") or 1.0)


def pack_size(
    product: dict[str, Any], derived_size_per_sales_unit: float | None = None
) -> tuple[float | None, float | None, str, str]:
    """(size per base unit, size per sales unit, "L"/"KG", source)."""
    per_sales = base_units_per_sales_unit(product)
    if product.get("baseUnit") == "KG":
        # Sold by weight: one base unit is one kilogram. Any weight in the data or
        # the name is a box or piece weight (salmon "...10KG"), never the unit size.
        return 1.0, per_sales, "KG", "not_needed"
    entries = product["synkkaData"].get("unitConversions") or []

    for entry in entries:
        if entry.get("synkkaPackagingType") == "BASE_UNIT_OR_EACH":
            hit = _usable(entry)
            if hit:
                size, unit = hit
                return size, size * per_sales, unit, "structured"

    for entry in entries:
        if entry.get("synkkaPackagingType") == "CASE":
            hit = _usable(entry)
            if hit:
                case_size, unit = hit
                in_base = _case_size_in_base_units(product)
                if in_base:
                    size = case_size / in_base
                    return size, size * per_sales, unit, "structured_case"

    hit = _size_from_name(product)
    if hit:
        size, unit = hit
        return size, size * per_sales, unit, "name"

    if derived_size_per_sales_unit:
        # From catalogPrice / comparisonPrice. Rounded to cents upstream, so this
        # is a fallback and a cross-check, never ahead of the data above.
        return (
            derived_size_per_sales_unit / per_sales,
            derived_size_per_sales_unit,
            "",
            "derived",
        )

    return None, None, "", "unknown"


def _round_up_to_lot(quantity: float, allowed_lot_size: float) -> float:
    """Whole sales units, and a multiple of allowedLotSize when that is 1 or more.

    allowedLotSize is 0.01 for 16,872 of 17,546 products, where it constrains
    nothing; a few hundred products use 1.5, 2, 3 or 5.
    """
    whole = math.ceil(quantity - 1e-9)
    if allowed_lot_size and allowed_lot_size >= 1:
        steps = math.ceil(whole / allowed_lot_size - 1e-9)
        return round(steps * allowed_lot_size, 2)
    return float(whole)


def convert(
    product: dict[str, Any],
    need_value: float,
    need_unit: str,
    derived_size_per_sales_unit: float | None = None,
) -> Conversion:
    """How many sales units cover the need, and what that actually delivers."""
    need_unit = (need_unit or "").upper()
    sales_unit = product.get("salesUnit", "")
    base_unit = product.get("baseUnit", "")
    per_sales = base_units_per_sales_unit(product)
    out = Conversion(
        ok=False,
        sales_unit=sales_unit,
        base_unit=base_unit,
        base_units_per_sales_unit=per_sales,
        need_value=need_value,
        need_unit=need_unit,
    )

    if product["synkkaData"].get("variableUnit"):
        # 632 products are sold by variable weight: a human decides the quantity.
        out.flags.append("variable_weight")
        out.reason = "variable_weight"
        return out

    # A need already in sales units needs no size at all.
    if need_unit in {sales_unit, "KI", "ST", "CASES", "PCS"} and need_unit not in NEED_TO_LITRES and need_unit not in NEED_TO_KILOS:
        quantity = _round_up_to_lot(need_value, float(product.get("allowedLotSize") or 0))
        out.ok = True
        out.size_source = "not_needed"
        out.quantity_sales_units = quantity
        out.delivered_amount = quantity
        out.overshoot = round(quantity - need_value, 6)
        return out

    if need_unit in NEED_TO_LITRES:
        need_in, kind = need_value * NEED_TO_LITRES[need_unit], "L"
    elif need_unit in NEED_TO_KILOS:
        need_in, kind = need_value * NEED_TO_KILOS[need_unit], "KG"
    else:
        out.reason = f"unsupported_need_unit:{need_unit}"
        return out

    size_base, size_sales, size_unit, source = pack_size(product, derived_size_per_sales_unit)
    out.size_per_base_unit, out.size_per_sales_unit = size_base, size_sales
    out.size_unit, out.size_source = size_unit or kind, source

    if not size_sales:
        out.reason = "size_unknown"          # basket asks for a quantity in cases
        return out
    if size_unit and size_unit != kind:
        # Never convert litres into kilograms, or the other way round.
        out.reason = f"size_in_{size_unit}_but_need_in_{kind}"
        return out

    quantity = _round_up_to_lot(need_in / size_sales, float(product.get("allowedLotSize") or 0))
    delivered = quantity * size_sales            # in litres or kilograms
    factor = NEED_TO_LITRES.get(need_unit) or NEED_TO_KILOS.get(need_unit)
    out.ok = True
    out.quantity_sales_units = quantity
    out.delivered_amount = round(delivered / factor, 6)
    out.overshoot = round(out.delivered_amount - need_value, 6)
    if source == "name":
        out.flags.append("size_from_product_name")
    if source == "derived":
        out.flags.append("size_derived_from_price")
    if derived_size_per_sales_unit and source != "derived":
        # Cross-check against catalogPrice / comparisonPrice. More than 2% apart
        # (beyond the cent-rounding in comparisonPrice) means one of them is wrong,
        # so the line is flagged for a human rather than silently ordered.
        drift = abs(size_sales - derived_size_per_sales_unit) / size_sales
        if drift > 0.02:
            out.flags.append("size_disagrees_with_price")
    return out


def convert_summary(
    summary: Any,
    need_value: float,
    need_unit: str,
    derived_size_per_sales_unit: float | None = None,
) -> Conversion:
    """convert() for the kitchen side, which never holds a raw catalogue row.

    `summary` is a ProductSummary from the gateway (or a dict with the same keys).
    The pack size on it was worked out by pack_size() when the catalogue was
    loaded, so this gives the same quantity convert() would: same rounding, same
    flags, same refusal to turn litres into kilograms.
    """
    get = summary.get if isinstance(summary, dict) else lambda k, d=None: getattr(summary, k, d)
    need_unit = (need_unit or "").upper()
    sales_unit = get("sales_unit") or ""
    lot = float(get("allowed_lot_size") or 0)
    per_sales = float(get("base_units_per_sales_unit") or 1.0)
    out = Conversion(
        ok=False,
        sales_unit=sales_unit,
        base_unit=get("base_unit") or "",
        base_units_per_sales_unit=per_sales,
        need_value=need_value,
        need_unit=need_unit,
    )

    if get("variable_weight"):
        out.flags.append("variable_weight")
        out.reason = "variable_weight"
        return out

    if need_unit in {sales_unit, "KI", "ST", "CASES", "PCS"} and need_unit not in NEED_TO_LITRES and need_unit not in NEED_TO_KILOS:
        quantity = _round_up_to_lot(need_value, lot)
        out.ok = True
        out.size_source = "not_needed"
        out.quantity_sales_units = quantity
        out.delivered_amount = quantity
        out.overshoot = round(quantity - need_value, 6)
        return out

    if need_unit in NEED_TO_LITRES:
        need_in, kind = need_value * NEED_TO_LITRES[need_unit], "L"
    elif need_unit in NEED_TO_KILOS:
        need_in, kind = need_value * NEED_TO_KILOS[need_unit], "KG"
    else:
        out.reason = f"unsupported_need_unit:{need_unit}"
        return out

    size_sales = get("size_per_sales_unit")
    size_unit = (get("size_unit") or "").upper()
    source = get("size_source") or "unknown"
    if not size_sales and derived_size_per_sales_unit:
        size_sales, size_unit, source = derived_size_per_sales_unit, "", "derived"
    out.size_per_sales_unit = size_sales
    out.size_per_base_unit = size_sales / per_sales if size_sales and per_sales else None
    out.size_unit, out.size_source = size_unit or kind, source

    if not size_sales:
        out.size_source = "unknown"
        out.reason = "size_unknown"
        return out
    if size_unit and size_unit != kind:
        out.reason = f"size_in_{size_unit}_but_need_in_{kind}"
        return out

    quantity = _round_up_to_lot(need_in / size_sales, lot)
    delivered = quantity * size_sales
    factor = NEED_TO_LITRES.get(need_unit) or NEED_TO_KILOS.get(need_unit)
    out.ok = True
    out.quantity_sales_units = quantity
    out.delivered_amount = round(delivered / factor, 6)
    out.overshoot = round(out.delivered_amount - need_value, 6)
    if source == "name":
        out.flags.append("size_from_product_name")
    if source == "derived":
        out.flags.append("size_derived_from_price")
    if derived_size_per_sales_unit and source != "derived":
        drift = abs(size_sales - derived_size_per_sales_unit) / size_sales
        if drift > 0.02:
            out.flags.append("size_disagrees_with_price")
    return out
