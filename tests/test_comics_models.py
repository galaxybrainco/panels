import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from comics.models import ContentRating, FederationMode, Tag
from comics.services import create_comic
from federation.activitypub import Audience


@pytest.mark.django_db
def test_create_comic_creates_actor_and_defaults():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Panels")
    assert comic.slug == "lunarbaboon"
    assert comic.title == "Panels"
    assert comic.actor.is_local
    assert comic.actor.handle == "lunarbaboon"
    assert comic.content_rating == ContentRating.ALL_AGES
    assert comic.default_audience == Audience.PUBLIC
    assert comic.default_federation == FederationMode.FEDERATED


@pytest.mark.django_db
def test_create_comic_rejects_invalid_handle():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    with pytest.raises(ValidationError):
        create_comic(owner, "Bad Handle", "Bad")


@pytest.mark.django_db
def test_create_comic_rejects_duplicate_slug():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    create_comic(owner, "lunarbaboon", "Panels")
    with pytest.raises(ValidationError):
        create_comic(owner, "lunarbaboon", "Panels Two")


@pytest.mark.django_db
def test_create_comic_assigns_tags():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Panels", tags=["fantasy", "comedy"])
    assert set(comic.tags.values_list("name", flat=True)) == {"fantasy", "comedy"}
    assert Tag.objects.count() == 2


@pytest.mark.django_db
def test_create_comic_makes_creator_owner():
    from comics.models import ComicRole

    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Panels")
    assert ComicRole.objects.get(comic=comic, user=owner).role == ComicRole.Role.OWNER
