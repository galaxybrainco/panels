import re
from pathlib import Path

SETTINGS_DIR = Path("config/settings")
ENV_READ_PATTERN = re.compile(
    r"""env(?:\.list|\.db|\.bool|\.int|\.float)?\(\s*["']([A-Z0-9_]+)["']"""
)

CORE_KEYS = {
    "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS",
    "DATABASE_URL",
}


def _documented_keys():
    text = Path(".env.example").read_text()
    return {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }


def _consumed_keys():
    keys = set()
    for path in SETTINGS_DIR.glob("*.py"):
        keys |= set(ENV_READ_PATTERN.findall(path.read_text()))
    return keys


def test_env_example_documents_core_settings():
    assert CORE_KEYS <= _documented_keys()


def test_env_example_keys_are_consumed_by_settings():
    unconsumed = _documented_keys() - _consumed_keys()
    assert unconsumed == set(), (
        f".env.example documents unread keys: {sorted(unconsumed)}"
    )
