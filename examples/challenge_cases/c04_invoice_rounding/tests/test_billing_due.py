from app.billing_facade import BillingFacade


def test_invoice_due_preserves_cents():
    assert BillingFacade().invoice_due(2.05) == 2.26
