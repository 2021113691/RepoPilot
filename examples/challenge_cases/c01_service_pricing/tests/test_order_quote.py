from app.order_service import quote_total


def test_quote_with_zero_adjustment():
    assert quote_total(8.0, 0) == 8.0
