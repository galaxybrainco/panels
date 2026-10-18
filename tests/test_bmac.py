from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from actors.services import create_local_actor
from comics.services import create_comic
from memberships.models import (
    Provider,
    ProviderConnection,
    ProviderSupporter,
    SupporterStatus,
    Tier,
)
from memberships.services import apply_supporter_entitlement


def user(email=None):
    return get_user_model().objects.create_user(
        email=email or f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


def scene():
    owner = user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    connection = ProviderConnection(comic=comic, provider=Provider.BMAC)
    connection.set_api_key("api-token")
    connection.set_webhook_token("hook-secret")
    connection.save()
    return owner, comic, connection


@pytest.mark.django_db
def test_apply_entitlement_activates_linked_status():
    _, comic, connection = scene()
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    from memberships.services import link_supporter

    link_supporter(actor, comic, Provider.BMAC, "reader@example.com")
    supporter = ProviderSupporter.objects.create(
        connection=connection,
        email="reader@example.com",
        tier_name="Fan",
        active=True,
        valid_until=timezone.now() + timedelta(days=10),
    )
    apply_supporter_entitlement(supporter)
    assert SupporterStatus.objects.filter(link__actor=actor, tier=tier).exists()


@pytest.mark.django_db
def test_apply_entitlement_deactivates_status_when_supporter_inactive():
    _, comic, connection = scene()
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    reader = user("reader@example.com")
    actor = create_local_actor(f"reader{uuid4().hex[:6]}", user=reader)
    from memberships.services import link_supporter

    link = link_supporter(actor, comic, Provider.BMAC, "reader@example.com")
    SupporterStatus.objects.create(link=link, tier=tier)
    supporter = ProviderSupporter.objects.create(
        connection=connection,
        email="reader@example.com",
        tier_name="Fan",
        active=False,
    )
    apply_supporter_entitlement(supporter)
    assert not SupporterStatus.objects.filter(link=link, active=True).exists()
