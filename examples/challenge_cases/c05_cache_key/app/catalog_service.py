"""Public catalog lookup with a small in-memory cache."""
from infra.key_codec import make_key


class CatalogService:
    def __init__(self):
        self.records = {"a-b": "red item", "ab": "blue item"}
        self.cache = {}

    def get_item(self, code: str) -> str:
        key = make_key(code)
        if key not in self.cache:
            self.cache[key] = self.records[code]
        return self.cache[key]
