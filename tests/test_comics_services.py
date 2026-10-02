import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from comics.models import Chapter, ComicRole, FederationMode, PageStatus
from comics.services import create_comic, create_page, create_series
from federation.activitypub import Audience


def _user(email):
    return get_user_model().objects.create_user(email=email, password="x")


@pytest.mark.django_db
def test_editor_can_create_series_contributor_cannot():
    owner = _user("owner@example.com")
    editor = _user("editor@example.com")
    contributor = _user("contributor@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    series = create_series(editor, comic, "Main Story")
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
    assert first.federation == FederationMode.FEDERATED
    assert first.author == owner


@pytest.mark.django_db
def test_create_page_respects_comic_defaults_and_permissions():
    owner = _user("owner@example.com")
    outsider = _user("outsider@example.com")
    comic = create_comic(
        owner,
        "lunarbaboon",
        "Lunar Baboon",
        default_audience=Audience.UNLISTED,
        default_federation=FederationMode.LOCAL_ONLY,
    )
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    assert page.audience == Audience.UNLISTED
    assert page.federation == FederationMode.LOCAL_ONLY
    with pytest.raises(PermissionDenied):
        create_page(outsider, series)


@pytest.mark.django_db
def test_moderator_cannot_author_pages():
    owner = _user("owner@example.com")
    moderator = _user("moderator@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    ComicRole.objects.create(comic=comic, user=moderator, role=ComicRole.Role.MODERATOR)
    series = create_series(owner, comic, "Main Story")
    with pytest.raises(PermissionDenied):
        create_page(moderator, series)


@pytest.mark.django_db
def test_create_page_rejects_chapter_from_another_series():
    owner = _user("owner@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    other = create_series(owner, comic, "Side Story")
    chapter = Chapter.objects.create(series=other, title="One", position=1)
    with pytest.raises(ValidationError):
        create_page(owner, series, chapter=chapter)


@pytest.mark.django_db
def test_create_page_rejects_unknown_fields():
    owner = _user("owner@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    with pytest.raises(TypeError):
        create_page(owner, series, bogus_field="x")


@pytest.mark.django_db
def test_create_comic_rejects_invalid_choice():
    owner = _user("owner@example.com")
    with pytest.raises(ValidationError):
        create_comic(owner, "lunarbaboon", "Lunar Baboon", default_audience="bogus")


@pytest.mark.django_db
def test_create_series_rejects_duplicate_slug():
    owner = _user("owner@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    create_series(owner, comic, "Main Story")
    with pytest.raises(ValidationError):
        create_series(owner, comic, "Main Story", slug="main-story")
