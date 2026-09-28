from app.service import create_receipt


def test_receipt_preserves_cents():
    assert create_receipt(1.75) == 1.75
