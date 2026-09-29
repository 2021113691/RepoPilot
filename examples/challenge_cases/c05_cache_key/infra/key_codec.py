"""Shared storage-key encoding."""


def make_key(raw: str) -> str:
    return raw.casefold().replace("-", "")
