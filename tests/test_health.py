import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_healthz_returns_ok_with_db_roundtrip(client):
    response = client.get(reverse("healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.django_db
def test_healthz_returns_500_when_database_unavailable(monkeypatch):
    from django.db import connection

    def boom(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(connection, "cursor", boom)
    client = Client(raise_request_exception=False)
    response = client.get(reverse("healthz"))
    assert response.status_code == 500
