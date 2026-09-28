from app.slug import slugify


def test_repeated_spaces_collapse_to_one_hyphen():
    assert slugify("Hello  World") == "hello-world"
