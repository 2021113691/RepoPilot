from infra.key_codec import make_key


def test_make_key_keeps_dash():
    assert make_key("a-b") != make_key("ab")
