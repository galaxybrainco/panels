import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured


def _reload_prod(
    monkeypatch,
    *,
    secret_key,
    allowed_hosts="example.com",
    instance_url="https://panels.test",
    field_encryption_key="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    email_backend="django.core.mail.backends.smtp.EmailBackend",
    **extra,
):
    monkeypatch.setenv("DJANGO_SECRET_KEY", secret_key)
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", allowed_hosts)
    monkeypatch.setenv("INSTANCE_URL", instance_url)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", field_encryption_key)
    monkeypatch.setenv("DJANGO_EMAIL_BACKEND", email_backend)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    import config.settings.base as base

    importlib.reload(base)
    return importlib.reload(importlib.import_module("config.settings.prod"))


def test_prod_settings_require_secret_key(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        _reload_prod(monkeypatch, secret_key="")


def test_prod_settings_reject_dev_placeholder_secret(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        _reload_prod(monkeypatch, secret_key="dev-only-change-me")


def test_prod_settings_reject_short_secret(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        _reload_prod(monkeypatch, secret_key="short")


def test_prod_settings_accept_strong_secret(monkeypatch):
    strong = "s" * 50
    settings = _reload_prod(monkeypatch, secret_key=strong)
    assert settings.SECRET_KEY == strong


def test_prod_settings_require_https_instance_url(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="INSTANCE_URL"):
        _reload_prod(
            monkeypatch, secret_key="s" * 50, instance_url="http://panels.test"
        )


def test_prod_settings_require_field_encryption_key(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="FIELD_ENCRYPTION_KEY"):
        _reload_prod(monkeypatch, secret_key="s" * 50, field_encryption_key="")


def test_prod_settings_harden_transport_and_cookies(monkeypatch):
    settings = _reload_prod(monkeypatch, secret_key="s" * 50)
    assert settings.SECURE_SSL_REDIRECT is True
    assert settings.SESSION_COOKIE_SECURE is True
    assert settings.CSRF_COOKIE_SECURE is True
    assert settings.SECURE_HSTS_SECONDS >= 31536000
    assert settings.SECURE_HSTS_INCLUDE_SUBDOMAINS is True
    assert settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")


def test_prod_settings_reject_dev_email_backend(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_EMAIL_BACKEND"):
        _reload_prod(
            monkeypatch,
            secret_key="s" * 50,
            email_backend="django.core.mail.backends.console.EmailBackend",
        )


def test_prod_settings_use_a_shared_cache(monkeypatch):
    settings = _reload_prod(monkeypatch, secret_key="s" * 50)
    assert settings.CACHES["default"]["BACKEND"] == (
        "django.core.cache.backends.db.DatabaseCache"
    )
