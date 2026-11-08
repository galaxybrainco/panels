from datetime import timedelta
from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from billing.models import ComicPlan, PlanStatus
from comics.models import ComicRole, Page, PageStatus
from memberships.models import Provider, Tier
from memberships.services import grant_supporter_status, link_supporter
from moderation import reports as report_services
from social.models import Follow, FollowStatus
from tests.test_reader import _publish, _scene, _user


def _remote(handle="fan"):
    return Actor.objects.create(
        ap_id=f"https://remote.test/users/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain="remote.test",
        inbox=f"https://remote.test/users/{handle}/inbox",
    )


def _role(comic, user, role):
    ComicRole.objects.create(comic=comic, user=user, role=role)


@pytest.mark.django_db
def test_dashboard_requires_collaboration(client):
    owner, comic, _, _, _, _, _ = _scene()
    url = reverse("comic-dashboard", args=[comic.slug])
    assert client.get(url).status_code == 302
    client.force_login(_user())
    assert client.get(url).status_code == 403


@pytest.mark.django_db
def test_owner_sees_editor_moderation_and_member_sections(client):
    owner, comic, _, _, page, _, _ = _scene()
    _publish(page)
    report_services.report_target(
        owner, target_type="page", target_id=page.id, reason="spam"
    )
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    fan = _user()
    actor = create_local_actor(f"fan{uuid4().hex[:6]}", user=fan)
    link = link_supporter(actor, comic, Provider.KOFI, fan.email)
    grant_supporter_status(link, tier)
    ComicPlan.objects.create(comic=comic, status=PlanStatus.ACTIVE, quota_bytes=1000)
    client.force_login(owner)
    response = client.get(reverse("comic-dashboard", args=[comic.slug]))
    assert response.status_code == 200
    assert b"Schedule" in response.content
    assert b"Moderation queue" in response.content
    assert b"Members" in response.content
    assert b"Plan" in response.content
    assert response.context["supporter_count"] == 1
    assert response.context["plan"] is not None
    assert b"spam" in response.content


@pytest.mark.django_db
def test_editor_and_moderator_sections_are_scoped(client):
    owner, comic, _, _, _, _, _ = _scene()
    editor = _user()
    _role(comic, editor, ComicRole.Role.EDITOR)
    moderator = _user()
    _role(comic, moderator, ComicRole.Role.MODERATOR)
    url = reverse("comic-dashboard", args=[comic.slug])

    client.force_login(editor)
    editor_response = client.get(url)
    assert b"Schedule" in editor_response.content
    assert b"Moderation queue" in editor_response.content
    assert b"Members" not in editor_response.content

    client.force_login(moderator)
    moderator_response = client.get(url)
    assert b"Moderation queue" in moderator_response.content
    assert b"Schedule" not in moderator_response.content
    assert b"Members" not in moderator_response.content


@pytest.mark.django_db
def test_follower_counts_and_schedule_grid(client):
    owner, comic, first, _, _, _, _ = _scene()
    local_follower = create_local_actor(f"local{uuid4().hex[:6]}")
    remote_follower = _remote()
    Follow.objects.create(
        follower=local_follower, target=comic.actor, status=FollowStatus.ACCEPTED
    )
    Follow.objects.create(
        follower=remote_follower, target=comic.actor, status=FollowStatus.ACCEPTED
    )
    page = Page.objects.create(
        series=first,
        position=99,
        title="Scheduled Page",
        status=PageStatus.SCHEDULED,
        scheduled_for=timezone.now() + timedelta(days=2),
    )
    client.force_login(owner)
    response = client.get(reverse("comic-dashboard", args=[comic.slug]))
    assert response.context["local_followers"] == 1
    assert response.context["remote_followers"] == 1
    assert b"Scheduled Page" in response.content
    assert list(response.context["upcoming"]) == [page]
    assert (
        client.get(
            reverse("comic-dashboard", args=[comic.slug]) + "?month=nope"
        ).status_code
        == 400
    )


@pytest.mark.django_db
def test_dashboard_link_on_comic_home_for_editors(client):
    _, comic, _, _, _, _, _ = _scene()
    editor = _user()
    _role(comic, editor, ComicRole.Role.EDITOR)
    client.force_login(editor)
    response = client.get(reverse("comic-home", args=[comic.slug]))
    assert b"Creator dashboard" in response.content
