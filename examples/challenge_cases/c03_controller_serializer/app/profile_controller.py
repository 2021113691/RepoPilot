"""Public profile response builder."""
from wire.encoding import pack_number


class ProfileController:
    def response(self, balance) -> dict[str, str]:
        return {"balance": pack_number(balance)}


def export_profile(balance) -> dict[str, str]:
    return {"balance": pack_number(balance)}
