from app.invoice import invoice_total


def test_percentage_tax():
    assert invoice_total(100.0, 0.10) == 110.0


def test_zero_tax():
    assert invoice_total(10.0, 0.0) == 10.0
