from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from federation.activitypub import Audience
from media.quota import comic_storage_quota
from memberships.models import ComicPlan, PlanStatus, Provider, SupporterLink, Tier
from memberships.services import (
    ANY_SUPPORTER,
    active_supporter_status,
    can_view_page,
    grant_supporter_status,
    link_supporter,
    page_requires_tier,
    supporter_has_access,
)


def remote(handle="bob"):
    domain = f"{handle}.test"
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
    )


def scene():
    owner = get_user_model().objects.create_user(
        email=f"o-{uuid4().hex[:8]}@example.com", password="x"
    )
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    return owner, comic, page


@pytest.mark.django_db
def test_tier_name_and_rank_unique_per_comic():
    _, comic, _ = scene()
    Tier.objects.create(comic=comic, name="Fan", rank=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Tier.objects.create(comic=comic, name="Fan", rank=2)
    with pytest.raises(IntegrityError), transaction.atomic():
        Tier.objects.create(comic=comic, name="Other", rank=1)


@pytest.mark.django_db
def test_link_supporter_is_idempotent_and_verified():
    _, comic, _ = scene()
    actor = create_local_actor("alice")
    link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    again = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    assert SupporterLink.objects.filter(comic=comic, actor=actor).count() == 1
    assert again.verified_at is not None


@pytest.mark.django_db
def test_access_requires_active_unexpired_status():
    _, comic, _ = scene()
    actor = create_local_actor("alice")
    fan = Tier.objects.create(comic=comic, name="Fan", rank=1)
    link = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    assert supporter_has_access(actor, comic) is False
    status = grant_supporter_status(link, fan)
    assert supporter_has_access(actor, comic) is True
    status.active = False
    status.save()
    assert active_supporter_status(actor, comic) is None
    assert supporter_has_access(actor, comic) is False


@pytest.mark.django_db
def test_expired_status_grants_no_access():
    _, comic, _ = scene()
    actor = create_local_actor("alice")
    fan = Tier.objects.create(comic=comic, name="Fan", rank=1)
    link = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    grant_supporter_status(link, fan, valid_until=timezone.now() - timedelta(days=1))
    assert supporter_has_access(actor, comic) is False


@pytest.mark.django_db
def test_rank_gates_tier():
    _, comic, _ = scene()
    actor = create_local_actor("alice")
    fan = Tier.objects.create(comic=comic, name="Fan", rank=1)
    patron = Tier.objects.create(comic=comic, name="Patron", rank=3)
    link = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    grant_supporter_status(link, fan)
    assert supporter_has_access(actor, comic, fan) is True
    assert supporter_has_access(actor, comic, patron) is False


@pytest.mark.django_db
def test_remote_actor_can_support():
    _, comic, _ = scene()
    bob = remote("bob")
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    link = link_supporter(bob, comic, Provider.BMAC, "bob@remote.test")
    grant_supporter_status(link, tier)
    assert supporter_has_access(bob, comic) is True


@pytest.mark.django_db
def test_none_actor_has_no_access():
    _, comic, _ = scene()
    assert supporter_has_access(None, comic) is False


@pytest.mark.django_db
def test_public_page_requires_nothing():
    _, comic, page = scene()
    assert page_requires_tier(page) is None
    assert can_view_page(None, page) is True


@pytest.mark.django_db
def test_members_page_requires_any_supporter():
    _, comic, page = scene()
    page.audience = Audience.MEMBERS
    page.save()
    assert page_requires_tier(page) is ANY_SUPPORTER
    actor = create_local_actor("alice")
    assert can_view_page(actor, page) is False
    link = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    grant_supporter_status(link, Tier.objects.create(comic=comic, name="Fan", rank=1))
    assert can_view_page(actor, page) is True


@pytest.mark.django_db
def test_tier_page_requires_that_rank():
    _, comic, page = scene()
    fan = Tier.objects.create(comic=comic, name="Fan", rank=1)
    patron = Tier.objects.create(comic=comic, name="Patron", rank=3)
    page.audience = Audience.TIER
    page.required_tier = patron
    page.save()
    actor = create_local_actor("alice")
    link = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    grant_supporter_status(link, fan)
    assert can_view_page(actor, page) is False
    grant_supporter_status(link, patron)
    assert can_view_page(actor, page) is True


@pytest.mark.django_db
def test_active_plan_overrides_the_storage_cap(settings):
    _, comic, _ = scene()
    settings.MEDIA_STORAGE_QUOTA_BYTES = 111
    ComicPlan.objects.create(comic=comic, status=PlanStatus.ACTIVE, quota_bytes=222)
    assert comic_storage_quota(comic) == 222


@pytest.mark.django_db
def test_inactive_plan_does_not_override(settings):
    _, comic, _ = scene()
    settings.MEDIA_STORAGE_QUOTA_BYTES = 111
    ComicPlan.objects.create(comic=comic, status=PlanStatus.FROZEN, quota_bytes=222)
    assert comic_storage_quota(comic) == 111


@pytest.mark.django_db
def test_no_plan_uses_the_settings_default(settings):
    _, comic, _ = scene()
    settings.MEDIA_STORAGE_QUOTA_BYTES = 111
    assert comic_storage_quota(comic) == 111
