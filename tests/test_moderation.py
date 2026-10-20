from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from actors.models import Actor, ActorType
from comics.services import create_comic, create_page, create_series
from moderation import services
from moderation.models import DomainBlock, KeywordFilter, Mute
from social.models import Follow, FollowStatus


def _user(email=None):
    return get_user_model().objects.create_user(
        email=email or f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


def _remote(handle="bob", domain="remote.test"):
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        name="Bob",
    )


def _scene():
    owner = _user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series, title="The Landing")
    return owner, comic, page


def _future(**kwargs):
    return timezone.now() + timedelta(**kwargs)


@pytest.mark.django_db
def test_mute_is_idempotent_and_updates_the_toggle():
    user = _user()
    actor = _remote()
    first = services.mute_actor(user, actor)
    assert first.hide_notifications is False
    again = services.mute_actor(user, actor, hide_notifications=True)
    assert again.pk == first.pk
    assert Mute.objects.count() == 1
    assert services.is_muted(user, actor) is True


@pytest.mark.django_db
def test_mute_rejects_self_and_owned_comic_actors():
    owner, comic, _ = _scene()
    personal = Actor.objects.create(
        ap_id=f"http://testserver/actors/{uuid4().hex}",
        handle=uuid4().hex,
        domain="",
        user=owner,
    )
    with pytest.raises(services.SelfMuteError):
        services.mute_actor(owner, personal)
    with pytest.raises(services.SelfMuteError):
        services.mute_actor(owner, comic.actor)


@pytest.mark.django_db
def test_expired_mute_is_inactive():
    user = _user()
    actor = _remote()
    services.mute_actor(user, actor, expires_at=_future(days=1))
    Mute.objects.filter(user=user, actor=actor).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert services.is_muted(user, actor) is False


@pytest.mark.django_db
def test_expiry_validation_rejects_naive_and_past():
    user = _user()
    actor = _remote()
    with pytest.raises(services.InvalidExpiryError):
        services.mute_actor(user, actor, expires_at=timezone.now())
    with pytest.raises(services.InvalidExpiryError):
        services.mute_actor(user, actor, expires_at=timezone.now() - timedelta(days=1))


@pytest.mark.django_db
def test_thread_mute_round_trip():
    user = _user()
    _, _, page = _scene()
    services.mute_thread(user, page)
    assert services.is_thread_muted(user, page) is True
    services.unmute_thread(user, page)
    assert services.is_thread_muted(user, page) is False


@pytest.mark.django_db
def test_filter_matches_comment_title_and_actor_case_insensitively():
    user = _user()
    _, _, page = _scene()
    actor = _remote()
    services.add_filter(user, "LUNAR")
    assert services.is_filtered(user, page=page) is True
    assert services.is_filtered(user, actor=actor) is False
    services.add_filter(user, "bob")
    assert services.is_filtered(user, actor=actor) is True


@pytest.mark.django_db
def test_expired_filter_does_not_match():
    user = _user()
    _, _, page = _scene()
    services.add_filter(user, "lunar", expires_at=_future(days=1))
    KeywordFilter.objects.filter(user=user).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert services.is_filtered(user, page=page) is False


@pytest.mark.django_db
def test_filter_validation():
    user = _user()
    with pytest.raises(services.InvalidPhraseError):
        services.add_filter(user, "   ")
    with pytest.raises(services.InvalidPhraseError):
        services.add_filter(user, "x" * 256)


@pytest.mark.django_db
def test_domain_block_normalizes_and_rejects_local():
    user = _user()
    block = services.block_domain(user, " Remote.Test ")
    assert block.domain == "remote.test"
    assert services.is_domain_blocked(user, "remote.test") is True
    assert DomainBlock.objects.count() == 1
    services.unblock_domain(user, "remote.test")
    assert services.is_domain_blocked(user, "remote.test") is False
    with pytest.raises(services.InvalidDomainError):
        services.block_domain(user, "not a domain")
    with pytest.raises(services.InvalidDomainError):
        services.block_domain(user, "testserver")


@pytest.mark.django_db
def test_remove_follower_requires_managing_the_actor():
    owner, comic, _ = _scene()
    follower = _remote()
    follow = Follow.objects.create(
        follower=follower, target=comic.actor, status=FollowStatus.ACCEPTED
    )
    with pytest.raises(services.NotActorOwnerError):
        services.remove_follower(_user(), comic.actor, follow)
    assert services.can_manage_actor(owner, comic.actor) is True
    services.remove_follower(owner, comic.actor, follow)
    assert not Follow.objects.filter(pk=follow.pk).exists()


@pytest.mark.django_db
def test_remove_follower_rejects_mismatched_target():
    owner, comic, _ = _scene()
    other = _remote(handle="other")
    follow = Follow.objects.create(follower=_remote(), target=other)
    with pytest.raises(services.FollowNotFoundError):
        services.remove_follower(owner, comic.actor, follow)
