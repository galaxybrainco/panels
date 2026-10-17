from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from comics.services import create_comic
from memberships.models import Provider, ProviderConnection, ProviderSupporter

TEST_TOKEN = "secret"


def user(email=None):
    return get_user_model().objects.create_user(
        email=email or f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


def scene(token=TEST_TOKEN):
    owner = user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    connection = ProviderConnection(comic=comic, provider=Provider.KOFI)
    connection.set_webhook_token(token)
    connection.save()
    return owner, comic, connection


@pytest.mark.django_db
def test_webhook_token_is_encrypted_and_round_trips():
    _, _, connection = scene("super-secret")
    connection.refresh_from_db()
    raw = bytes(connection.webhook_token)
    assert b"super-secret" not in raw
    assert connection.get_webhook_token() == "super-secret"


@pytest.mark.django_db
def test_provider_connection_is_unique_per_comic_and_provider():
    _, comic, _ = scene()
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        ProviderConnection.objects.create(
            comic=comic, provider=Provider.KOFI, webhook_token=b"x"
        )


@pytest.mark.django_db
def test_provider_supporter_is_unique_per_connection_email():
    _, _, connection = scene()
    ProviderSupporter.objects.create(connection=connection, email="a@example.com")
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        ProviderSupporter.objects.create(connection=connection, email="a@example.com")
