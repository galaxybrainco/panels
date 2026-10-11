from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from social.models import Notification, NotificationType
from social.notifications import mark_all_read, mark_read, notify


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
    note = notify(recipient, type=NotificationType.FOLLOW, actor=actor)
    assert note.recipient == recipient
    assert note.type == NotificationType.FOLLOW


@pytest.mark.django_db
def test_notify_skips_missing_recipient():
    assert notify(None, type=NotificationType.FOLLOW) is None
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_notify_skips_self():
    actor = create_local_actor("solo", user=None)
    owner = user()
    actor.user = owner
    actor.save()
    assert notify(owner, type=NotificationType.FOLLOW, actor=actor) is None


@pytest.mark.django_db
def test_mark_read_and_all_read():
    recipient = user()
    keep = notify(recipient, type=NotificationType.FOLLOW)
    mark_read(keep)
    keep.refresh_from_db()
    assert keep.read is True
    notify(recipient, type=NotificationType.LIKE)
    mark_all_read(recipient)
    assert not Notification.objects.filter(recipient=recipient, read=False).exists()
