from app.email_utils import normalize_email


def test_normalize_email_lowercases_domain():
    assert normalize_email("User@Example.COM") == "User@example.com"
