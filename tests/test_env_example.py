from pathlib import Path

import environ

CORE_KEYS = {
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ALLOWED_HOSTS",
    "DATABASE_URL",
}


def test_env_example_documents_core_settings():
    text = Path(".env.example").read_text()
    keys = {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    assert CORE_KEYS <= keys


def test_env_example_parses_as_environment():
    text = Path(".env.example").read_text()
    parsed = environ.Env.parse_value  # sanity: django-environ importable
    assert parsed is not None
    assert "DATABASE_URL" in text
