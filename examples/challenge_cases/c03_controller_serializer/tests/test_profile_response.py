from decimal import Decimal
from app.profile_controller import ProfileController


def test_profile_balance_representation():
    assert ProfileController().response(Decimal("10.50")) == {"balance": "10.50"}
