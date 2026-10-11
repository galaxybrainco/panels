from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from social.models import (
    Follow,
    FollowStatus,
    Notification,
    NotificationType,
)
from social.notifications import mark_all_read, mark_read, notify
from social.services import accept_follow
from tests.media_support import make_media


def user():
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x"
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


@pytest.mark.django_db
def test_notify_creates_a_notification():
    recipient = user()
    actor = remote("bob")
    note = notify(recipient, kind=NotificationType.FOLLOW, actor=actor)
    assert note.recipient == recipient
    assert note.type == NotificationType.FOLLOW


@pytest.mark.django_db
def test_notify_skips_missing_recipient():
    assert notify(None, kind=NotificationType.FOLLOW) is None
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_notify_skips_self():
    actor = create_local_actor("solo", user=None)
    owner = user()
    actor.user = owner
    actor.save()
    assert notify(owner, kind=NotificationType.FOLLOW, actor=actor) is None


@pytest.mark.django_db
def test_mark_read_and_all_read():
    recipient = user()
    keep = notify(recipient, kind=NotificationType.FOLLOW)
    mark_read(keep)
    keep.refresh_from_db()
    assert keep.read is True
    notify(recipient, kind=NotificationType.LIKE)
    mark_all_read(recipient)
    assert not Notification.objects.filter(recipient=recipient, read=False).exists()


def scene():
    owner = user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)
    page.refresh_from_db()
    return owner, comic, page


@pytest.mark.django_db
def test_publish_notifies_local_followers():
    owner, comic, _ = scene()
    follower = user()
    follower_actor = create_local_actor(f"fan{uuid4().hex[:6]}", user=follower)
    Follow.objects.create(
        follower=follower_actor, target=comic.actor, status=FollowStatus.ACCEPTED
    )
    series_page = create_page(owner, comic.series.first())
    make_media(series_page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, series_page)

    assert Notification.objects.filter(
        recipient=follower, type=NotificationType.NEW_PAGE, page=series_page
    ).exists()


@pytest.mark.django_db
def test_publish_does_not_notify_remote_followers():
    owner, comic, _ = scene()
    remote_follower = remote("remotefan")
    Follow.objects.create(
        follower=remote_follower, target=comic.actor, status=FollowStatus.ACCEPTED
    )
    page = create_page(owner, comic.series.first())
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)

    assert not Notification.objects.filter(type=NotificationType.NEW_PAGE).exists()


@pytest.mark.django_db
def test_accept_follow_notifies_comic_owner():
    owner, comic, _ = scene()
    bob = remote("bob")
    follow = Follow.objects.create(
        follower=bob, target=comic.actor, status=FollowStatus.PENDING
    )
    accept_follow(follow)
    assert Notification.objects.filter(
        recipient=owner, type=NotificationType.FOLLOW, actor=bob
    ).exists()
