from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from actors.services import create_local_actor
from comics.services import create_comic
from memberships.kofi import (
    attach_pending_supporter,
    process_kofi_event,
    verify_token,
)
from memberships.models import (
    Provider,
    ProviderConnection,
    ProviderSupporter,
    SupporterLink,
    SupporterStatus,
    Tier,
)
from memberships.services import supporter_has_access

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


def kofi_payload(
    email="reader@example.com",
    token=TEST_TOKEN,
    subscription=True,
    tier_name="Fan",
):
    return {
        "verification_token": token,
        "email": email,
        "is_subscription_payment": subscription,
        "tier_name": tier_name,
        "type": "Subscription" if subscription else "Donation",
        "amount": "5.00",
        "kofi_transaction_id": uuid4().hex,
    }


@pytest.mark.django_db
def test_verify_token():
    _, _, connection = scene()
    assert verify_token(connection, TEST_TOKEN) is True
    assert verify_token(connection, "wrong") is False


@pytest.mark.django_db
def test_subscription_creates_active_supporter():
    _, _, connection = scene()
    before = timezone.now()
    supporter = process_kofi_event(connection, kofi_payload())
    assert supporter.active is True
    assert supporter.tier_name == "Fan"
    assert supporter.valid_until >= before + timedelta(days=33)
    assert supporter.last_payment_at is not None


@pytest.mark.django_db
def test_donation_does_not_grant_membership():
    _, _, connection = scene()
    supporter = process_kofi_event(connection, kofi_payload(subscription=False))
    assert supporter.active is False
    assert supporter.valid_until is None


@pytest.mark.django_db
def test_bad_token_stores_nothing():
    _, _, connection = scene()
    assert process_kofi_event(connection, kofi_payload(token="wrong")) is None
    assert ProviderSupporter.objects.count() == 0


@pytest.mark.django_db
def test_renewal_extends_validity():
    _, _, connection = scene()
    supporter = process_kofi_event(connection, kofi_payload())
    supporter.valid_until = timezone.now() + timedelta(days=1)
    supporter.save()
    before = timezone.now()
    renewed = process_kofi_event(connection, kofi_payload())
    assert renewed.valid_until >= before + timedelta(days=33)


@pytest.mark.django_db
def test_attach_pending_links_matching_account_email():
    _, comic, connection = scene()
    Tier.objects.create(comic=comic, name="Fan", rank=1)
    reader = user("Reader@Example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    process_kofi_event(
        connection, kofi_payload(email="reader@example.com", tier_name="Fan")
    )
    link = attach_pending_supporter(actor, comic, Provider.KOFI, "reader@example.com")
    assert link is not None
    assert SupporterLink.objects.filter(comic=comic, actor=actor).exists()
    assert SupporterStatus.objects.filter(link=link, tier__name="Fan").exists()
    assert supporter_has_access(actor, comic) is True


@pytest.mark.django_db
def test_attach_rejects_email_mismatch():
    _, comic, connection = scene()
    Tier.objects.create(comic=comic, name="Fan", rank=1)
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    process_kofi_event(connection, kofi_payload(email="other@example.com"))
    assert (
        attach_pending_supporter(actor, comic, Provider.KOFI, "other@example.com")
        is None
    )
    assert not SupporterLink.objects.filter(actor=actor).exists()


@pytest.mark.django_db
def test_attach_without_tier_match_creates_no_status():
    _, comic, connection = scene()
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    process_kofi_event(connection, kofi_payload(tier_name="Unknown"))
    link = attach_pending_supporter(actor, comic, Provider.KOFI, "reader@example.com")
    assert link is not None
    assert not SupporterStatus.objects.filter(link=link).exists()
    assert supporter_has_access(actor, comic) is False
