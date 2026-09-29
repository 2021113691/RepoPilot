"""Materialize the fixed, small Day 5.5 diagnostic repositories.

The generated repositories contain ordinary source and tests only. Evaluation
labels live outside these fixtures and are never copied into Agent workspaces.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "examples" / "challenge_cases"

CASES: dict[str, dict[str, str]] = {
    "c01_service_pricing": {
        "app/order_service.py": '''"""Public order calculations."""
from engine.adjustments import apply_rate


class OrderService:
    def calculate_total(self, subtotal: float, promotion: float) -> float:
        return apply_rate(subtotal, promotion)


def quote_total(subtotal: float, promotion: float) -> float:
    return apply_rate(subtotal, promotion)
''',
        "app/order_view.py": '''"""Presentation helpers for order screens."""


def display_total(value: float) -> str:
    return f"${value:.2f}"
''',
        "engine/adjustments.py": '''"""Numeric adjustment primitive."""


def apply_rate(base: float, factor: float) -> float:
    if factor == 0:
        raise ValueError("adjustment factor has no table entry")
    if factor < 0 or factor > 1:
        raise ValueError("factor must be between 0 and 1")
    return round(base * (1 - factor), 2)
''',
        "engine/currency.py": '''"""Display-only currency conversion."""


def currency_label(code: str) -> str:
    return code.upper()
''',
        "tests/test_order_service.py": '''from app.order_service import OrderService


def test_zero_promotion_keeps_total():
    assert OrderService().calculate_total(19.99, 0) == 19.99


def test_nonzero_promotion():
    assert OrderService().calculate_total(20.0, 0.1) == 18.0
''',
        "tests/test_order_quote.py": '''from app.order_service import quote_total


def test_quote_with_zero_adjustment():
    assert quote_total(8.0, 0) == 8.0
''',
    },
    "c02_parser_normalizer": {
        "app/record_parser.py": '''"""Parse public record text into fields."""
from core.canonical import clean_atom


class RecordParser:
    def parse_fields(self, line: str) -> dict[str, str]:
        name, value = line.split(":", 1)
        return {name: clean_atom(value)}


def parse_pair(line: str) -> tuple[str, str]:
    name, value = line.split(":", 1)
    return name, clean_atom(value)
''',
        "app/record_view.py": '''"""Record presentation helper."""


def display_field(name: str, value: str) -> str:
    return f"{name}: {value}"
''',
        "core/canonical.py": '''"""Shared atom canonicalization."""


def clean_atom(raw: str) -> str:
    if raw.startswith(" "):
        raise RuntimeError("atom has forbidden leading padding")
    return raw.strip()
''',
        "core/tokens.py": '''"""Token delimiter helpers."""


def field_separator() -> str:
    return ":"
''',
        "tests/test_record_parser.py": '''from app.record_parser import RecordParser


def test_padded_field_value():
    assert RecordParser().parse_fields("name:  Ada  ") == {"name": "Ada"}


def test_plain_field_value():
    assert RecordParser().parse_fields("name:Ada") == {"name": "Ada"}
''',
        "tests/test_record_pair.py": '''from app.record_parser import parse_pair


def test_pair_with_padding():
    assert parse_pair("city:  Paris  ") == ("city", "Paris")
''',
    },
    "c03_controller_serializer": {
        "app/profile_controller.py": '''"""Public profile response builder."""
from wire.encoding import pack_number


class ProfileController:
    def response(self, balance) -> dict[str, str]:
        return {"balance": pack_number(balance)}


def export_profile(balance) -> dict[str, str]:
    return {"balance": pack_number(balance)}
''',
        "app/profile_view.py": '''"""Display helper for profile pages."""


def badge(name: str) -> str:
    return f"Member: {name}"
''',
        "wire/encoding.py": '''"""Shared wire number encoding."""


def pack_number(raw) -> str:
    if hasattr(raw, "as_tuple"):
        raise TypeError("numeric wire type unsupported")
    return f"{raw:.2f}"
''',
        "wire/headers.py": '''"""Static transport headers."""


def content_type() -> str:
    return "application/json"
''',
        "tests/test_profile_response.py": '''from decimal import Decimal
from app.profile_controller import ProfileController


def test_profile_balance_representation():
    assert ProfileController().response(Decimal("10.50")) == {"balance": "10.50"}
''',
        "tests/test_profile_export.py": '''from decimal import Decimal
from app.profile_controller import export_profile


def test_export_balance_representation():
    assert export_profile(Decimal("8.25")) == {"balance": "8.25"}
''',
    },
    "c04_invoice_rounding": {
        "app/billing_facade.py": '''"""Public billing API."""
from ledger.tax import add_sales_tax


class BillingFacade:
    def invoice_due(self, subtotal: float) -> float:
        return add_sales_tax(subtotal)
''',
        "app/billing_view.py": '''"""Display helper for bills."""


def display_due(value: float) -> str:
    return f"Due: {value:.2f}"
''',
        "ledger/tax.py": '''"""Tax arithmetic shared by invoices and quotes."""
from mathops.quantize import quantize_money


def add_sales_tax(value: float) -> float:
    return quantize_money(value * 1.10)


def quote_with_tax(value: float) -> float:
    return quantize_money(value * 1.10)
''',
        "ledger/receipts.py": '''"""Receipt labels."""


def receipt_label(number: int) -> str:
    return f"R-{number:04d}"
''',
        "mathops/quantize.py": '''"""Final numeric settlement."""
from decimal import Decimal, ROUND_HALF_UP


def quantize_money(raw: float) -> float:
    number = Decimal(str(raw))
    if number != number.quantize(Decimal("0.01")):
        raise RuntimeError("fractional settlement")
    return float(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
''',
        "mathops/ratios.py": '''"""Ratio display helper."""


def percent_label(value: float) -> str:
    return f"{value * 100:.0f}%"
''',
        "tests/test_billing_due.py": '''from app.billing_facade import BillingFacade


def test_invoice_due_preserves_cents():
    assert BillingFacade().invoice_due(2.05) == 2.26
''',
        "tests/test_billing_quote.py": '''from ledger.tax import quote_with_tax


def test_quote_uses_same_settlement():
    assert quote_with_tax(2.05) == 2.26
''',
    },
    "c05_cache_key": {
        "app/catalog_service.py": '''"""Public catalog lookup with a small in-memory cache."""
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
''',
        "app/catalog_view.py": '''"""Catalog presentation helper."""


def display_item(name: str) -> str:
    return name.title()
''',
        "infra/key_codec.py": '''"""Shared storage-key encoding."""


def make_key(raw: str) -> str:
    return raw.casefold().replace("-", "")
''',
        "infra/store.py": '''"""Simple dictionary-backed store."""


def store_value(items: dict, key: str, value: str) -> None:
    items[key] = value
''',
        "tests/test_catalog_service.py": '''from app.catalog_service import CatalogService


def test_distinct_product_codes_do_not_share_cached_item():
    service = CatalogService()
    assert service.get_item("a-b") == "red item"
    assert service.get_item("ab") == "blue item"
''',
        "tests/test_storage_keys.py": '''from infra.key_codec import make_key


def test_make_key_keeps_dash():
    assert make_key("a-b") != make_key("ab")
''',
    },
}


def main() -> None:
    for case_id, files in CASES.items():
        root = ROOT / case_id
        for directory in {Path(name).parent for name in files}:
            if directory != Path("."):
                files.setdefault(f"{directory.as_posix()}/__init__.py", "")
        files.setdefault("pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
        files.setdefault(".gitignore", "__pycache__/\n*.pyc\n.pytest_cache/\n")
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_text(encoding="utf-8") != content:
                raise ValueError(f"refusing to overwrite changed fixture: {path}")
            path.write_text(content, encoding="utf-8")
        print(f"{case_id}: {len(files)} files")


if __name__ == "__main__":
    main()
