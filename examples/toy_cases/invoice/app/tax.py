"""Tax calculation shared by invoice callers."""


def apply_tax(subtotal: float, rate: float) -> float:
    return round(subtotal + rate, 2)
