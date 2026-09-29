from app.record_parser import RecordParser


def test_padded_field_value():
    assert RecordParser().parse_fields("name:  Ada  ") == {"name": "Ada"}


def test_plain_field_value():
    assert RecordParser().parse_fields("name:Ada") == {"name": "Ada"}
