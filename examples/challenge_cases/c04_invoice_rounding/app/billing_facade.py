"""Public billing API."""
from ledger.tax import add_sales_tax


class BillingFacade:
    def invoice_due(self, subtotal: float) -> float:
        return add_sales_tax(subtotal)
