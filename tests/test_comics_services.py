import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from comics.models import ComicRole, PageStatus
from comics.services import create_comic, create_page, create_series
from federation.activitypub import Audience


def _user(email):
    return get_user_model().objects.create_user(email=email, password="x")


@pytest.mark.django_db
def test_editor_can_create_series_contributor_cannot():
    owner = _user("owner@example.com")
    contributor = _user("contributor@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    series = create_series(owner, comic, "Main Story")
    assert series.slug == "main-story"
    assert series.position == 1
    with pytest.raises(PermissionDenied):
        create_series(contributor, comic, "Nope")


@pytest.mark.django_db
def test_create_page_defaults_and_positioning():
    owner = _user("owner@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    first = create_page(owner, series)
    second = create_page(owner, series, title="Two")
    assert first.position == 1
    assert second.position == 2
    assert first.status == PageStatus.DRAFT
    assert first.audience == Audience.PUBLIC
    assert first.author == owner


@pytest.mark.django_db
def test_create_page_respects_comic_defaults_and_permissions():
    owner = _user("owner@example.com")
    outsider = _user("outsider@example.com")
    comic = create_comic(
        owner, "lunarbaboon", "Lunar Baboon", default_audience=Audience.UNLISTED
    )
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    assert page.audience == Audience.UNLISTED
    with pytest.raises(PermissionDenied):
        create_page(outsider, series)
