"""Shared atom canonicalization."""


def clean_atom(raw: str) -> str:
    if raw.startswith(" "):
        raise RuntimeError("atom has forbidden leading padding")
    return raw.strip()
