from app.record_parser import parse_pair


def test_pair_with_padding():
    assert parse_pair("city:  Paris  ") == ("city", "Paris")
