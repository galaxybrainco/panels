import pytest
from django.db import OperationalError


@pytest.mark.django_db
def test_healthz_reports_database_and_cache(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "cache": "ok",
    }


@pytest.mark.django_db
def test_healthz_returns_503_when_database_fails(client, monkeypatch):
    def broken():
        raise OperationalError("db down")

    monkeypatch.setattr("config.health._check_database", broken)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "error"
    assert b"db down" not in response.content


@pytest.mark.django_db
def test_healthz_returns_503_when_cache_fails(client, monkeypatch):
    def broken():
        raise RuntimeError("cache down")

    monkeypatch.setattr("config.health._check_cache", broken)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["cache"] == "error"
    assert b"cache down" not in response.content
