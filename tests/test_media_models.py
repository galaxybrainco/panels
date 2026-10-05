import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from comics.services import create_comic, create_page, create_series
from media.models import Media
from tests.media_support import make_media


@pytest.fixture
def page():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    return create_page(owner, series)


@pytest.mark.django_db
def test_media_defaults(page):
    media = Media.objects.create(
        page=page,
        position=1,
        original="media/x.png",
        content_type="image/png",
        width=10,
        height=20,
        bytes=123,
        sha256="a" * 64,
    )
    assert media.alt_text == ""
    assert media.page == page


@pytest.mark.django_db
def test_media_position_is_unique_per_page(page):
    make_media(page, position=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Media.objects.create(
            page=page,
            position=1,
            original="media/y.png",
            content_type="image/png",
            width=1,
            height=1,
            bytes=1,
            sha256="a" * 64,
        )


@pytest.mark.django_db
def test_media_orders_by_position(page):
    make_media(page, position=2)
    make_media(page, position=1)
    assert [item.position for item in page.media.all()] == [1, 2]
