import re

from django.core.exceptions import ValidationError

HANDLE_RE = re.compile(r"^[a-z0-9_]{3,32}$")

RESERVED_HANDLES = frozenset(
    {
        "about",
        "account",
        "accounts",
        "actor",
        "actors",
        "admin",
        "administrator",
        "api",
        "federation",
        "feeds",
        "healthz",
        "help",
        "inbox",
        "instance",
        "login",
        "media",
        "mod",
        "moderator",
        "nodeinfo",
        "outbox",
        "panels",
        "root",
        "signup",
        "staff",
        "static",
        "support",
        "system",
        "uploads",
        "webfinger",
    }
)


def normalize_handle(handle: str) -> str:
    return (handle or "").strip().lower()


def validate_handle(handle: str) -> str:
    normalized = normalize_handle(handle)
    if not HANDLE_RE.match(normalized):
        raise ValidationError(
            "Handles must be 3-32 characters of lowercase letters, digits, "
            "or underscores."
        )
    if normalized in RESERVED_HANDLES:
        raise ValidationError("That handle is reserved.")
    return normalized
