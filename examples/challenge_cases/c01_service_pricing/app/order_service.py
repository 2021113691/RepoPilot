"""Public order calculations."""
from engine.adjustments import apply_rate


class OrderService:
    def calculate_total(self, subtotal: float, promotion: float) -> float:
        return apply_rate(subtotal, promotion)


def quote_total(subtotal: float, promotion: float) -> float:
    return apply_rate(subtotal, promotion)
