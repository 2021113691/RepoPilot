"""Preserve non-space separators while normalizing literal spaces."""


def slugify(text: str) -> str:
    return text.lower().replace(" ", "-")
