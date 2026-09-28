"""Create invoice totals from a shared tax helper."""

from .tax import apply_tax


def invoice_total(subtotal: float, rate: float) -> float:
    return apply_tax(subtotal, rate)
