"""Public task inputs for the Day 5.5 diagnostic challenge set.

No expected file, symbol, or patch labels are stored in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ChallengeCase:
    case_id: str
    fixture: Path
    issue: str


CASES = {
    "c01_service_pricing": ChallengeCase(
        "c01_service_pricing", ROOT / "examples/challenge_cases/c01_service_pricing",
        "OrderService.calculate_total raises for a zero-percent promotion. Both an order total and quote should preserve the original amount in that case. Find the cause and verify with pytest.",
    ),
    "c02_parser_normalizer": ChallengeCase(
        "c02_parser_normalizer", ROOT / "examples/challenge_cases/c02_parser_normalizer",
        "RecordParser.parse_fields should trim surrounding spaces from field values while leaving the field name intact. A padded name value currently fails. Verify the parser behavior with pytest.",
    ),
    "c03_controller_serializer": ChallengeCase(
        "c03_controller_serializer", ROOT / "examples/challenge_cases/c03_controller_serializer",
        "ProfileController.response should return a JSON-ready balance string with two decimal places for a monetary value such as 10.50. It currently errors. Verify with pytest.",
    ),
    "c04_invoice_rounding": ChallengeCase(
        "c04_invoice_rounding", ROOT / "examples/challenge_cases/c04_invoice_rounding",
        "BillingFacade.invoice_due(2.05) should return 2.26 after the configured ten-percent charge. It currently fails instead of returning a payable amount. Verify with pytest.",
    ),
    "c05_cache_key": ChallengeCase(
        "c05_cache_key", ROOT / "examples/challenge_cases/c05_cache_key",
        "CatalogService.get_item can return a stale item when product codes differ by punctuation. Distinct codes must remain distinct; verify with pytest.",
    ),
}
