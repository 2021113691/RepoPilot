from app.slug import slugify


def test_tabs_remain_unchanged():
    assert slugify("A\tB") == "a\tb"


def test_single_space_becomes_hyphen():
    assert slugify("Hello World") == "hello-world"
