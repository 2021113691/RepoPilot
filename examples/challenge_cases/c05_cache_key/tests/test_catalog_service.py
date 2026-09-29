from app.catalog_service import CatalogService


def test_distinct_product_codes_do_not_share_cached_item():
    service = CatalogService()
    assert service.get_item("a-b") == "red item"
    assert service.get_item("ab") == "blue item"
