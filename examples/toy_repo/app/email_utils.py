"""Email helpers. One deliberate bug is left for agent inspection."""


def normalize_email(address: str) -> str:
    local, domain = address.strip().split("@", 1)
    return f"{local}@{domain}"  # Bug: mixed-case domain remains unchanged.
