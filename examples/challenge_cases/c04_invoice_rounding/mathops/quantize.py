"""Final numeric settlement."""
from decimal import Decimal, ROUND_HALF_UP


def quantize_money(raw: float) -> float:
    number = Decimal(str(raw))
    if number != number.quantize(Decimal("0.01")):
        raise RuntimeError("fractional settlement")
    return float(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
