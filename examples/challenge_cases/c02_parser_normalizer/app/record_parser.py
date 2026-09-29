"""Parse public record text into fields."""
from core.canonical import clean_atom


class RecordParser:
    def parse_fields(self, line: str) -> dict[str, str]:
        name, value = line.split(":", 1)
        return {name: clean_atom(value)}


def parse_pair(line: str) -> tuple[str, str]:
    name, value = line.split(":", 1)
    return name, clean_atom(value)
