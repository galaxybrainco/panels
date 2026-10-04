import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from comics.models import ComicRole, PageStatus
from comics.publishing import publish_page, unpublish_page
from comics.services import create_comic, create_page, create_series


def _user(email="owner@example.com"):
    return get_user_model().objects.create_user(email=email, password="x")


def _page(owner, **page_fields):
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series, alt_text="A panel", **page_fields)
    return comic, series, page


@pytest.mark.django_db
def test_publish_page_sets_status_credit_and_object_id():
    owner = _user()
    _, _, page = _page(owner)
    published = publish_page(owner, page)
    assert published.status == PageStatus.PUBLISHED
    assert published.published_by == owner
    assert published.published_at is not None
    assert published.ap_id == f"http://testserver/pages/{page.id}"


@pytest.mark.django_db
def test_publish_page_is_idempotent():
    owner = _user()
    _, _, page = _page(owner)
    first = publish_page(owner, page)
    stamp = first.published_at
    second = publish_page(owner, page)
    assert second.status == PageStatus.PUBLISHED
    assert second.published_at == stamp


@pytest.mark.django_db
def test_contributor_cannot_publish_but_editor_can():
    owner = _user()
    contributor = _user("contributor@example.com")
    editor = _user("editor@example.com")
    comic, _, page = _page(owner)
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    with pytest.raises(PermissionDenied):
        publish_page(contributor, page)
    published = publish_page(editor, page)
    assert published.status == PageStatus.PUBLISHED


@pytest.mark.django_db
def test_outsider_cannot_publish():
    owner = _user()
    outsider = _user("outsider@example.com")
    _, _, page = _page(owner)
    with pytest.raises(PermissionDenied):
        publish_page(outsider, page)


@pytest.mark.django_db
def test_publish_requires_alt_text():
    owner = _user()
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    with pytest.raises(ValidationError) as exc:
        publish_page(owner, page)
    assert "alt_text" in exc.value.message_dict


@pytest.mark.django_db
def test_publish_requires_content_warning_when_sensitive():
    owner = _user()
    _, _, page = _page(owner, sensitive=True)
    with pytest.raises(ValidationError) as exc:
        publish_page(owner, page)
    assert "content_warning" in exc.value.message_dict
    page.content_warning = "Flashing imagery"
    page.save()
    assert publish_page(owner, page).status == PageStatus.PUBLISHED


@pytest.mark.django_db
def test_unpublish_returns_to_draft_but_keeps_object_id():
    owner = _user()
    _, _, page = _page(owner)
    published = publish_page(owner, page)
    object_id = published.ap_id
    unpublished = unpublish_page(owner, published)
    assert unpublished.status == PageStatus.DRAFT
    assert unpublished.published_at is None
    assert unpublished.scheduled_for is None
    assert unpublished.ap_id == object_id


@pytest.mark.django_db
def test_contributor_cannot_unpublish():
    owner = _user()
    contributor = _user("contributor@example.com")
    comic, _, page = _page(owner)
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    publish_page(owner, page)
    with pytest.raises(PermissionDenied):
        unpublish_page(contributor, page)
