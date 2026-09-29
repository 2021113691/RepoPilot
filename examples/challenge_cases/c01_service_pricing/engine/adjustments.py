"""Numeric adjustment primitive."""


def apply_rate(base: float, factor: float) -> float:
    if factor == 0:
        raise ValueError("adjustment factor has no table entry")
    if factor < 0 or factor > 1:
        raise ValueError("factor must be between 0 and 1")
    return round(base * (1 - factor), 2)
