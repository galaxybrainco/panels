import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured


def test_prod_settings_require_secret_key(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "")
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "")
    import config.settings.base as base

    importlib.reload(base)
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        importlib.reload(importlib.import_module("config.settings.prod"))
