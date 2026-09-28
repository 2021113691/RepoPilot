"""Toy discount calculation with two boundary problems."""


def discounted_total(price: float, rate: float) -> float:
    if rate <= 0 or rate > 1:
        raise ValueError("rate must be between 0 and 1")
    return round(price * (1 - rate), 2)
