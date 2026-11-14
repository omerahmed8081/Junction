"""Totals must survive a hand calculation; the hour 0 numbers are the cases."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.money import basket_totals, line_totals, price_per_comparison_unit_is_rounded  # noqa: E402


def test_cheapest_milk_line():
    # 464306: 1,000 ST at EUR 1.01 net, 13.5% VAT.
    t = line_totals(1.01, 1000, 13.5)
    assert t == {"line_net": 1010.00, "line_vat": 136.35, "line_gross": 1146.35}
    # The API's rounded unit gross (1.15) would have given 1,150.00: EUR 3.65 out.
    assert t["line_gross"] != round(1.15 * 1000, 2)


def test_novobox_line():
    # 463038: 50 KI at EUR 31.31 net.
    t = line_totals(31.31, 50, 13.5)
    assert t == {"line_net": 1565.50, "line_vat": 211.34, "line_gross": 1776.84}


def test_basket_sums_the_lines():
    lines = [line_totals(1.01, 1000, 13.5), line_totals(31.31, 50, 13.5)]
    assert basket_totals(lines) == {
        "net_total": 2575.50, "vat_total": 347.69, "gross_total": 2923.19,
    }


def test_zero_and_missing_tax_rate():
    assert line_totals(1.01, 1000, None)["line_vat"] == 0.0
    assert line_totals(1.01, 0, 13.5)["line_net"] == 0.0


def test_comparison_price_is_flagged_as_rounded():
    # Novobox: 31.31 / 20 L = 1.5655, which Aimo reports as 1.57.
    assert price_per_comparison_unit_is_rounded(31.31, 1.57, 20.0)
    # 464306: 1.01 per 1 L is exact.
    assert not price_per_comparison_unit_is_rounded(1.01, 1.01, 1.0)

def test_half_a_cent_rounds_up_not_to_even():
    """Decimal with ROUND_HALF_UP, so a hand calculation agrees.

    Python's round() would give 2.67 for 2.675 (half to even) and 0.13 for 0.135.
    """
    assert line_totals(0.535, 5, 0)["line_net"] == 2.68        # 2.675 -> 2.68
    assert line_totals(1.00, 1, 13.5)["line_vat"] == 0.14      # 0.135  -> 0.14


def test_basket_vat_is_the_sum_of_line_vats():
    """Not VAT recomputed on the basket net: the two can differ by a cent."""
    lines = [line_totals(1.00, 1, 13.5), line_totals(1.00, 1, 13.5)]
    totals = basket_totals(lines)
    assert totals["vat_total"] == 0.28                          # 0.14 + 0.14
    assert round(totals["net_total"] * 0.135, 2) == 0.27        # the other way round


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok  ", name); passed += 1
    print(f"\n{passed} passed")
