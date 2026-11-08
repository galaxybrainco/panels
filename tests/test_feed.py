from uuid import uuid4

import pytest
from django.urls import reverse

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.models import Page, PageStatus
from federation.activitypub import Audience
from memberships.models import Provider, Tier
from memberships.services import grant_supporter_status, link_supporter
from moderation import accounts, blocks
from moderation import services as moderation_services
from social.models import Follow, FollowStatus, Notification, NotificationType
from tests.test_reader import _publish, _reader, _scene, _user


def _actor(user):
    return create_local_actor(f"u{uuid4().hex[:8]}", user=user)


def _follow(user, comic):
    actor = _actor(user)
    Follow.objects.create(
        follower=actor, target=comic.actor, status=FollowStatus.ACCEPTED
    )
    return actor


def _remote(handle="bob"):
    return Actor.objects.create(
        ap_id=f"https://remote.test/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain="remote.test",
        name=handle.title(),
        inbox=f"https://remote.test/actors/{handle}/inbox",
    )


def _notification(user, **kwargs):
    return Notification.objects.create(
        recipient=user, type=NotificationType.NEW_PAGE, **kwargs
    )


@pytest.mark.django_db
def test_feed_requires_login(client):
    assert client.get(reverse("feed")).status_code == 302
    assert client.get(reverse("notifications")).status_code == 302


@pytest.mark.django_db
def test_feed_shows_pages_from_followed_comics_only(client):
    _, followed, _, _, p1, p2, _ = _scene()
    _, other, _, _, other_page, _, _ = _scene()
    _publish(p1, p2, other_page)
    Page.objects.filter(pk=p2.pk).update(published_at=p1.published_at)
    Page.objects.filter(pk=other_page.pk).update(title="Other-Page")
    user = _user()
    _follow(user, followed)
    client.force_login(user)
    response = client.get(reverse("feed"))
    assert response.status_code == 200
    assert b"One-A" in response.content
    assert b"One-B" in response.content
    assert b"Other-Page" not in response.content


@pytest.mark.django_db
def test_feed_hides_muted_blocked_and_suspended_comics(client):
    _, comic, _, _, p1, _, _ = _scene()
    _publish(p1)
    user = _user()
    _follow(user, comic)
    client.force_login(user)
    moderation_services.mute_actor(user, comic.actor)
    assert b"One-A" not in client.get(reverse("feed")).content
    moderation_services.unmute_actor(user, comic.actor)
    assert b"One-A" in client.get(reverse("feed")).content

    blocks.block_actor(user, comic.actor)
    assert b"One-A" not in client.get(reverse("feed")).content
    blocks.unblock_actor(user, comic.actor)

    staff = _user(staff=True)
    accounts.apply_account_action(comic.actor, staff, "suspend", reason="x")
    assert b"One-A" not in client.get(reverse("feed")).content


@pytest.mark.django_db
def test_feed_gates_member_pages_for_non_supporters(client):
    _, comic, _, _, p1, _, _ = _scene(audience=Audience.MEMBERS)
    _publish(p1)
    user = _user()
    _follow(user, comic)
    client.force_login(user)
    assert b"One-A" not in client.get(reverse("feed")).content

    actor = Actor.objects.get(user=user)
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    link = link_supporter(actor, comic, Provider.KOFI, user.email)
    grant_supporter_status(link, tier)
    assert b"One-A" in client.get(reverse("feed")).content


@pytest.mark.django_db
def test_notifications_page_and_open_marks_read(client):
    _, _, _, _, p1, _, _ = _scene()
    _publish(p1)
    user = _user()
    other = _user()
    actor = _remote()
    own = _notification(user, actor=actor, page=p1)
    _notification(other, actor=actor, page=p1)
    client.force_login(user)
    response = client.get(reverse("notifications"))
    assert response.status_code == 200
    assert Notification.objects.filter(recipient=other).count() == 1
    assert response.content.count(b"New page") == 1

    opened = client.get(reverse("notification-open", args=[own.id]))
    assert opened.status_code == 302
    assert opened["Location"] == _reader(p1)
    own.refresh_from_db()
    assert own.read is True


@pytest.mark.django_db
def test_mark_all_read(client):
    _, _, _, _, p1, _, _ = _scene()
    _publish(p1)
    user = _user()
    _notification(user, actor=_remote(), page=p1)
    client.force_login(user)
    response = client.post(reverse("notifications-read-all"))
    assert response.status_code == 302
    assert Notification.objects.filter(recipient=user, read=False).count() == 0


@pytest.mark.django_db
def test_notifications_hide_moderated_and_unpublished_items(client):
    _, _, _, _, p1, _, _ = _scene()
    _publish(p1)
    user = _user()
    actor = _remote(handle="muted")
    moderation_services.mute_actor(user, actor, hide_notifications=True)
    hidden_mute = _notification(user, actor=actor, page=p1)

    _actor(user)
    blocked_actor = _remote(handle="blocked")
    blocks.block_actor(user, blocked_actor)
    hidden_block = _notification(user, actor=blocked_actor, page=p1)

    suspended = _remote(handle="suspended")
    staff = _user(staff=True)
    accounts.apply_account_action(suspended, staff, "suspend", reason="x")
    hidden_suspended = _notification(user, actor=suspended, page=p1)

    _notification(user, actor=_remote(handle="fine"), page=p1)
    client.force_login(user)
    content = client.get(reverse("notifications")).content
    for notification in (hidden_mute, hidden_block, hidden_suspended):
        assert notification.actor.handle.encode() not in content
    assert b"Fine" in content

    Page.objects.filter(pk=p1.pk).update(status=PageStatus.DRAFT)
    content = client.get(reverse("notifications")).content
    assert b"Fine" not in content


@pytest.mark.django_db
def test_unread_count_appears_in_navigation(client):
    _, comic, _, _, p1, _, _ = _scene()
    _publish(p1)
    user = _user()
    _notification(user, actor=_remote(), page=p1)
    client.force_login(user)
    response = client.get(reverse("comic-home", args=[comic.slug]))
    assert b"Notifications (1)" in response.content
