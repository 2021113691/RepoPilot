from app.greeting import format_greeting


def test_surrounding_whitespace():
    assert format_greeting("  Ada  ") == "Hello, Ada!"


def test_internal_spaces_remain():
    assert format_greeting("Ada Lovelace") == "Hello, Ada Lovelace!"
