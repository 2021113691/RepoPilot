"""Toy user service."""

from app.email_utils import normalize_email
from app.users import User


def register_user(name: str, email: str) -> User:
    return User(name=name, email=normalize_email(email))
