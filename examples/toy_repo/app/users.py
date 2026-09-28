"""Toy user model."""

from dataclasses import dataclass


@dataclass
class User:
    name: str
    email: str
