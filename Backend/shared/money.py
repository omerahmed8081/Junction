"""Money: one place where line and basket totals are computed.

CONTRACT - agreed hour 0. Every euro figure in a basket, a quote and the policy
check comes from here, so a judge with a calculator gets the same answer.

The rule that matters: VAT is computed **once per line, from the line's net
value**, never by multiplying a per-unit price that has already been rounded to
cents. Aimo's API returns both, and the rounded one is off by euros at scale:

    464306, 1,000 ST at EUR 1.01 net, 13.5% VAT
      wrong: 1,000 x 1.15 (the API's rounded unit gross) = 1,150.00
      right: net 1,010.00 + VAT 136.35                   = 1,146.35
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def _money(value: float | Decimal | str) -> Decimal:
    """Two decimals, half a cent always rounding up.

    Decimal, not round(): Python rounds floats half to even, so round(2.675, 2)
    is 2.67 and a hand calculation would say 2.68.
    """
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def line_totals(
    price_per_sales_unit: float,
    quantity_sales_units: float,
    tax_rate_percent: float | None,
) -> dict[str, float]:
    """Net, VAT and gross for one basket line.

    `price_per_sales_unit` is the net catalogue price for this customer
    (`catalogPrice.value`). `tax_rate_percent` is the API's `tax.taxRate`, e.g. 13.5.
    """
    net = _money(Decimal(str(price_per_sales_unit)) * Decimal(str(quantity_sales_units)))
    rate = Decimal(str(tax_rate_percent or 0)) / Decimal("100")
    vat = _money(net * rate)
    return {"line_net": float(net), "line_vat": float(vat), "line_gross": float(net + vat)}


def basket_totals(lines: list[dict[str, float]]) -> dict[str, float]:
    """Sum the line figures.

    The basket VAT is the **sum of the line VATs**, not VAT recomputed on the
    basket net. The two can differ by a cent when lines round in the same
    direction, and the invoice a kitchen would receive adds up the lines.
    """
    net = sum((_money(l.get("line_net", 0)) for l in lines), Decimal("0"))
    vat = sum((_money(l.get("line_vat", 0)) for l in lines), Decimal("0"))
    return {"net_total": float(net), "vat_total": float(vat), "gross_total": float(net + vat)}


def price_per_comparison_unit_is_rounded(
    price_per_sales_unit: float,
    comparison_price: float,
    size_per_sales_unit: float | None,
) -> bool:
    """True when quantity x comparison_price will not equal the line net.

    Aimo rounds `comparisonPrice` to cents, so 1,000 x EUR 1.57 is not the line
    total for the 10 L Novobox. The basket shows the per-litre figure as a
    rounded comparison, and totals from the sales-unit price.
    """
    if not (comparison_price and size_per_sales_unit):
        return False
    exact = price_per_sales_unit / size_per_sales_unit
    return abs(exact - comparison_price) > 0.0005
