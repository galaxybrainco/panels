import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from federation import handlers
from federation.activitypub import Audience
from federation.models import Activity, ActivityDirection
from social.models import Boost, Follow, FollowStatus, Like
from social.reactions import (
    boost,
    boost_count,
    like,
    like_count,
    unboost,
    unlike,
)
from tests.media_support import make_media


def published_page(**page_fields):
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series, **page_fields)
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)
    page.refresh_from_db()
    return page


@pytest.mark.django_db
def test_like_creates_row_and_count():
    actor, page = create_local_actor("alice"), published_page()
    like(actor, page)
    assert like_count(page) == 1
    assert Like.objects.get().object_id == page.ap_id
    assert Like.objects.get().page == page


@pytest.mark.django_db
def test_like_is_idempotent_per_actor_and_object():
    actor, page = create_local_actor("alice"), published_page()
    like(actor, page)
    like(actor, page)
    assert Like.objects.count() == 1


@pytest.mark.django_db
def test_unlike_removes_row():
    actor, page = create_local_actor("alice"), published_page()
    like(actor, page)
    unlike(actor, page)
    assert like_count(page) == 0


@pytest.mark.django_db
def test_boost_creates_and_unboost_removes():
    actor, page = create_local_actor("alice"), published_page()
    boost(actor, page)
    assert boost_count(page) == 1
    unboost(actor, page)
    assert boost_count(page) == 0


@pytest.mark.django_db
def test_like_requires_a_published_page():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    draft = create_page(owner, series)
    actor = create_local_actor("alice")
    with pytest.raises(ValidationError):
        like(actor, draft)
    with pytest.raises(ValidationError):
        boost(actor, draft)


@pytest.mark.django_db
def test_like_unique_constraint():
    actor, page = create_local_actor("alice"), published_page()
    Like.objects.create(actor=actor, object_id=page.ap_id, page=page)
    with pytest.raises(IntegrityError), transaction.atomic():
        Like.objects.create(actor=actor, object_id=page.ap_id, page=page)


def remote(handle="bob"):
    domain = f"{handle}.test"
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
    )


def _inbound(activity_type, actor, payload):
    return Activity.objects.create(
        ap_id=payload["id"],
        type=activity_type,
        actor=actor,
        direction=ActivityDirection.INBOUND,
        payload=payload,
    )


@pytest.mark.django_db
def test_inbound_like_stores_for_published_federatable_page():
    page, bob = published_page(), remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Like", bob, payload))
    assert Like.objects.get(actor=bob).page == page


@pytest.mark.django_db
def test_inbound_like_is_idempotent():
    page, bob = published_page(), remote("bob")
    first = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Like", bob, first))
    handlers.dispatch(_inbound("Like", bob, {**first, "id": "https://bob.test/a/2"}))
    assert Like.objects.count() == 1


@pytest.mark.django_db
def test_inbound_like_ignored_for_members_page():
    page, bob = published_page(audience=Audience.MEMBERS), remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Like", bob, payload))
    assert not Like.objects.exists()


@pytest.mark.django_db
def test_inbound_like_ignored_for_unknown_object():
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": "https://elsewhere.test/pages/nope",
    }
    handlers.dispatch(_inbound("Like", bob, payload))
    assert not Like.objects.exists()


@pytest.mark.django_db
def test_inbound_announce_stores_boost():
    page, bob = published_page(), remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Announce",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Announce", bob, payload))
    assert Boost.objects.get(actor=bob).page == page


@pytest.mark.django_db
def test_inbound_undo_like_removes_row_dict_form():
    page, bob = published_page(), remote("bob")
    Like.objects.create(
        actor=bob, object_id=page.ap_id, page=page, activity_id="https://bob.test/a/1"
    )
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": {"type": "Like", "actor": bob.ap_id, "object": page.ap_id},
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Like.objects.exists()


@pytest.mark.django_db
def test_inbound_undo_like_removes_row_string_form():
    page, bob = published_page(), remote("bob")
    Like.objects.create(
        actor=bob, object_id=page.ap_id, page=page, activity_id="https://bob.test/a/1"
    )
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": "https://bob.test/a/1",
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Like.objects.exists()


@pytest.mark.django_db
def test_inbound_undo_announce_removes_boost():
    page, bob = published_page(), remote("bob")
    Boost.objects.create(
        actor=bob, object_id=page.ap_id, page=page, activity_id="https://bob.test/a/1"
    )
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": {"type": "Announce", "actor": bob.ap_id, "object": page.ap_id},
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Boost.objects.exists()


@pytest.mark.django_db
def test_undo_follow_still_removed():
    comic, bob = create_local_actor("comic"), remote("bob")
    Follow.objects.create(follower=bob, target=comic, status=FollowStatus.ACCEPTED)
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": {"type": "Follow", "actor": bob.ap_id, "object": comic.ap_id},
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Follow.objects.exists()


@pytest.mark.django_db
def test_inbound_like_with_embedded_object():
    page, bob = published_page(), remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": {"id": page.ap_id, "type": "Note"},
    }
    handlers.dispatch(_inbound("Like", bob, payload))
    assert Like.objects.get(actor=bob).page == page


@pytest.mark.django_db
def test_inbound_like_backfills_activity_id_on_existing_row():
    page, bob = published_page(), remote("bob")
    Like.objects.create(actor=bob, object_id=page.ap_id, page=page, activity_id="")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Like", bob, payload))
    assert Like.objects.get(actor=bob).activity_id == "https://bob.test/activities/1"


@pytest.mark.django_db
def test_inbound_like_then_undo_by_activity_id():
    page, bob = published_page(), remote("bob")
    like_payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Like", bob, like_payload))
    undo_payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": "https://bob.test/activities/1",
    }
    handlers.dispatch(_inbound("Undo", bob, undo_payload))
    assert not Like.objects.exists()


@pytest.mark.django_db
def test_inbound_announce_then_undo_by_activity_id():
    page, bob = published_page(), remote("bob")
    announce_payload = {
        "id": "https://bob.test/activities/1",
        "type": "Announce",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Announce", bob, announce_payload))
    undo_payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": "https://bob.test/activities/1",
    }
    handlers.dispatch(_inbound("Undo", bob, undo_payload))
    assert not Boost.objects.exists()
