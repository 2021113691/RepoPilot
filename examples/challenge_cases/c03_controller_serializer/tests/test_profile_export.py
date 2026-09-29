from decimal import Decimal
from app.profile_controller import export_profile


def test_export_balance_representation():
    assert export_profile(Decimal("8.25")) == {"balance": "8.25"}
