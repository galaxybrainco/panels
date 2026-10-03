import uuid

import pytest
from django.contrib.auth import get_user_model

from comics.models import Chapter, ComicRole, Tag
from comics.services import create_comic, create_page, create_series


def _user(email="owner@example.com"):
    return get_user_model().objects.create_user(email=email, password="x")


@pytest.mark.django_db
def test_entity_models_use_uuid_primary_keys():
    owner = _user()
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon", tags=["comedy"])
    series = create_series(owner, comic, "Main Story")
    chapter = Chapter.objects.create(series=series, title="One", position=1)
    page = create_page(owner, series)
    tag = Tag.objects.get(name="comedy")

    for instance in (comic, series, chapter, page, tag):
        assert isinstance(instance.pk, uuid.UUID), type(instance).__name__


@pytest.mark.django_db
def test_entity_models_have_created_and_updated_timestamps():
    owner = _user()
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    chapter = Chapter.objects.create(series=series, title="One", position=1)
    page = create_page(owner, series)
    tag = Tag.objects.create(name="comedy", slug="comedy")

    for instance in (comic, series, chapter, page, tag):
        assert instance.created_at is not None, type(instance).__name__
        assert instance.updated_at is not None, type(instance).__name__


@pytest.mark.django_db
def test_comicrole_join_table_keeps_integer_primary_key():
    owner = _user()
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    role = ComicRole.objects.get(comic=comic, user=owner)
    assert isinstance(role.pk, int)
    assert not isinstance(role.pk, uuid.UUID)
