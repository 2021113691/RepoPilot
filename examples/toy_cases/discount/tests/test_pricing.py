import pytest

from app.pricing import discounted_total


def test_zero_rate_keeps_original_price():
    assert discounted_total(10.0, 0) == 10.0


def test_half_cent_rounds_up():
    assert discounted_total(2.05, 0.5) == 1.03


def test_negative_rate_is_rejected():
    with pytest.raises(ValueError):
        discounted_total(10.0, -0.1)
