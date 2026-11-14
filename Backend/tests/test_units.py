"""Tests for the one conversion function, on real products from the catalogue.

    cd Backend && python -m pytest tests -q        (or: python tests/test_units.py)

Each case is a trap found in the real data. If one of these breaks, every
quantity in the demo is wrong, so they run before anything is wired together.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.units import convert, pack_size  # noqa: E402

PRODUCTS = {
    p["id"]: p
    for p in json.loads(
        (Path(__file__).parent / "fixtures" / "products_sample.json").read_text("utf-8")
    )
}


def test_structured_litres_per_base_unit():
    # 464306, the cheapest lactose-free milk: 1 L per ST, sold per ST.
    c = convert(PRODUCTS["464306"], 1000, "L")
    assert c.ok and c.size_source == "structured"
    assert c.quantity_sales_units == 1000 and c.sales_unit == "ST"
    assert c.delivered_amount == 1000 and c.overshoot == 0


def test_size_only_in_the_name():
    # 463038, the 10 L Novobox: no structured size (its CASE entry is empty),
    # sold per KI of 2 ST, so 20 L per case and 1,000 L is 50 cases.
    c = convert(PRODUCTS["463038"], 1000, "L")
    assert c.ok and c.size_source == "name"
    assert c.size_per_base_unit == 10 and c.size_per_sales_unit == 20
    assert c.quantity_sales_units == 50 and c.sales_unit == "KI"
    assert "size_from_product_name" in c.flags


def test_structured_ten_litres_sold_singly():
    # 464179, the Arla 10 L Novobox: 10 L per ST, so 100 units, not 50.
    c = convert(PRODUCTS["464179"], 1000, "L")
    assert c.ok and c.size_source == "structured"
    assert c.quantity_sales_units == 100 and c.sales_unit == "ST"


def test_size_only_at_case_level():
    # 460003, 40 g crisps: the only size is 720 g on the CASE entry and the case
    # holds 18 bags, so one bag is 40 g. Never 720 g per bag.
    size_base, size_sales, unit, source = pack_size(PRODUCTS["460003"])
    assert source == "structured_case" and unit == "KG"
    assert abs(size_base - 0.04) < 1e-9       # 40 g per bag
    assert abs(size_sales - 0.72) < 1e-9      # 720 g per case
    c = convert(PRODUCTS["460003"], 10, "KG")
    assert c.ok and c.quantity_sales_units == 14   # 13.9 cases rounded up


def test_case_level_volume_divided_correctly():
    # 469330, "5 x Arla 1 L": CASE volume 5 L, case holds 5 ST, so 1 L per ST.
    # The name has a multiplier, so name parsing must not be what answers here.
    size_base, size_sales, unit, source = pack_size(PRODUCTS["469330"])
    assert source == "structured_case" and unit == "L"
    assert abs(size_base - 1.0) < 1e-9 and abs(size_sales - 5.0) < 1e-9
    c = convert(PRODUCTS["469330"], 1000, "L")
    assert c.quantity_sales_units == 200          # 200 cases of 5 L


def test_mass_stored_in_the_volume_field():
    # 472492, 5 kg sour milk: volume holds GRM 5000, which is a mass.
    size_base, _, unit, source = pack_size(PRODUCTS["472492"])
    assert source == "structured" and unit == "KG" and abs(size_base - 5.0) < 1e-9
    assert convert(PRODUCTS["472492"], 100, "KG").quantity_sales_units == 20
    # ... and a litre need must not be answered from a kilogram size.
    assert not convert(PRODUCTS["472492"], 100, "L").ok


def test_pieces_are_not_a_size():
    # 460490, table covers: volume unit H87 is pieces, and the name says
    # "127x220cm ... 25pcs", so there is no usable size at all.
    c = convert(PRODUCTS["460490"], 100, "L")
    assert not c.ok and c.size_source == "unknown" and c.reason == "size_unknown"


def test_base_level_wins_when_case_units_are_ambiguous():
    # 463335 has both KI (5) and CS (20), but it also has a base-level size,
    # so the ambiguity never comes up.
    c = convert(PRODUCTS["463335"], 1000, "L")
    assert c.ok and c.size_source == "structured"
    assert c.quantity_sales_units == 1000


def test_name_with_single_size_in_a_case():
    # 463779, "2,5 dl" sold per KI of 15: 0.25 L per unit, 3.75 L per case.
    c = convert(PRODUCTS["463779"], 100, "L")
    assert c.ok and c.size_source == "name"
    assert abs(c.size_per_sales_unit - 3.75) < 1e-9
    assert c.quantity_sales_units == 27            # 26.67 cases rounded up
    assert c.delivered_amount > 100                # overshoot is shown, not hidden


def test_derived_size_from_price_is_the_last_resort():
    # Hour 0 finding: catalogPrice / comparisonPrice gives the size of one sales
    # unit. Only used when nothing above answers, and flagged when it is.
    product = json.loads(json.dumps(PRODUCTS["460490"]))   # H87, no usable size
    product["synkkaData"]["names"] = [{"value": "no size here", "language": "en"}]
    c = convert(product, 100, "L", derived_size_per_sales_unit=27.22 / 2.72)
    assert c.ok and c.size_source == "derived"
    assert "size_derived_from_price" in c.flags
    assert c.quantity_sales_units == 10


def test_rounds_up_to_whole_sales_units():
    # 464201, 1 L per ST: an awkward need still orders whole units.
    c = convert(PRODUCTS["464201"], 999.5, "L")
    assert c.quantity_sales_units == 1000 and c.overshoot == 0.5


def test_need_already_in_cases_needs_no_size():
    c = convert(PRODUCTS["463038"], 12, "KI")
    assert c.ok and c.quantity_sales_units == 12 and c.size_source == "not_needed"


def test_derived_size_agrees_with_the_data():
    """Cross-check: the price-derived size matches the size we computed."""
    for pid, catalog, comparison in (
        ("464306", 1.01, 1.01),
        ("463038", 31.31, 1.57),
        ("464179", 27.22, 2.72),
        ("463779", 10.71, 2.86),
        ("472492", 7.28, 1.46),
    ):
        _, size_sales, _, _ = pack_size(PRODUCTS[pid])
        derived = catalog / comparison
        assert abs(size_sales - derived) / size_sales < 0.01, pid


def test_flags_when_size_rule_and_price_disagree():
    """A derived size that contradicts the data is flagged, not ignored."""
    # 463038 is 20 L per case; pretend the price implies 10 L per case.
    c = convert(PRODUCTS["463038"], 1000, "L", derived_size_per_sales_unit=10.0)
    assert c.ok and c.size_source == "name"
    assert "size_disagrees_with_price" in c.flags
    # The real derived size (31.31 / 1.57 = 19.94) agrees, so no flag.
    ok = convert(PRODUCTS["463038"], 1000, "L", derived_size_per_sales_unit=31.31 / 1.57)
    assert "size_disagrees_with_price" not in ok.flags


def test_sold_by_the_kilogram_name_weight_is_ignored():
    # 462150 salmon fillet, base unit KG. The Finnish name ends "10KG" (the box),
    # but one KG is one kilogram. allowedLotSize 10, so 15 kg -> 20 KG.
    c = convert(PRODUCTS["462150"], 15, "KG")
    assert c.ok and c.size_per_base_unit == 1 and c.size_per_sales_unit == 1
    assert c.quantity_sales_units == 20 and c.delivered_amount == 20


def test_sold_by_the_kilogram_structured_weight_is_ignored():
    # 463067 beef striploin, base unit KG, structured 2.8 kg (one piece). Still 1 kg per KG.
    c = convert(PRODUCTS["463067"], 5, "KG")
    assert c.ok and c.quantity_sales_units == 5 and c.delivered_amount == 5


def test_kilogram_base_unit_in_a_case():
    # 460007 mushrooms, base unit KG, sold per KI of 2 KG, no size anywhere else.
    c = convert(PRODUCTS["460007"], 10, "KG")
    assert c.ok and c.size_per_sales_unit == 2
    assert c.quantity_sales_units == 5 and c.delivered_amount == 10


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
            passed += 1
    print(f"\n{passed} passed")
