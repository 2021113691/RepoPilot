from app.order_service import OrderService


def test_zero_promotion_keeps_total():
    assert OrderService().calculate_total(19.99, 0) == 19.99


def test_nonzero_promotion():
    assert OrderService().calculate_total(20.0, 0.1) == 18.0
