import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured


def _reload_prod(monkeypatch, *, secret_key, allowed_hosts="example.com"):
    monkeypatch.setenv("DJANGO_SECRET_KEY", secret_key)
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", allowed_hosts)
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
