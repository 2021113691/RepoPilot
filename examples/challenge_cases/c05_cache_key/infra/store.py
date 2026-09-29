"""Simple dictionary-backed store."""


def store_value(items: dict, key: str, value: str) -> None:
    items[key] = value
