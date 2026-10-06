import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from social.models import Like
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
