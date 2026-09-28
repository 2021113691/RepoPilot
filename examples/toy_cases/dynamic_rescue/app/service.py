"""Receipt service."""

from internal.pricing import round_price


def create_receipt(amount: float) -> float:
    return round_price(amount)
