import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from comics.models import Page, PageStatus, Series
from comics.services import create_comic
from federation.activitypub import Audience


@pytest.fixture
def comic():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    return create_comic(owner, "lunarbaboon", "Lunar Baboon")


@pytest.mark.django_db
def test_series_slug_unique_per_comic(comic):
    Series.objects.create(comic=comic, title="One", slug="one", position=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Series.objects.create(comic=comic, title="One too", slug="one", position=2)


@pytest.mark.django_db
def test_page_position_unique_per_series(comic):
    series = Series.objects.create(comic=comic, title="One", slug="one", position=1)
    Page.objects.create(series=series, position=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Page.objects.create(series=series, position=1)


@pytest.mark.django_db
def test_page_defaults(comic):
    series = Series.objects.create(comic=comic, title="One", slug="one", position=1)
    page = Page.objects.create(series=series, position=1)
    assert page.status == PageStatus.DRAFT
    assert page.audience == Audience.PUBLIC
    assert page.sensitive is False
    assert page.ap_id == ""
    assert page.published_at is None


@pytest.mark.django_db
def test_pages_are_ordered_by_position(comic):
    series = Series.objects.create(comic=comic, title="One", slug="one", position=1)
    Page.objects.create(series=series, position=2)
    Page.objects.create(series=series, position=1)
    assert [page.position for page in series.pages.all()] == [1, 2]


@pytest.mark.django_db
def test_deleting_comic_cascades_series_and_pages(comic):
    series = Series.objects.create(comic=comic, title="One", slug="one", position=1)
    Page.objects.create(series=series, position=1)
    comic.delete()
    assert Series.objects.count() == 0
    assert Page.objects.count() == 0
