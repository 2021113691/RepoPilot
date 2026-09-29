"""Tax arithmetic shared by invoices and quotes."""
from mathops.quantize import quantize_money


def add_sales_tax(value: float) -> float:
    return quantize_money(value * 1.10)


def quote_with_tax(value: float) -> float:
    return quantize_money(value * 1.10)
