from ledger.tax import quote_with_tax


def test_quote_uses_same_settlement():
    assert quote_with_tax(2.05) == 2.26
