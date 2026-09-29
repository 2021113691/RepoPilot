"""Shared wire number encoding."""


def pack_number(raw) -> str:
    if hasattr(raw, "as_tuple"):
        raise TypeError("numeric wire type unsupported")
    return f"{raw:.2f}"
