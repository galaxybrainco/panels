from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from comics.services import create_comic, create_page, create_series
from media.quota import comic_storage_bytes, ensure_within_quota
from media.services import add_media, remove_media
from tests.media_support import upload


def scene():
    owner = get_user_model().objects.create_user(
        email=f"o-{uuid4().hex[:8]}@example.com", password="x"
    )
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    return owner, comic, page


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path


@pytest.mark.django_db
def test_storage_bytes_sums_originals_across_pages():
    owner, comic, first = scene()
    second = create_page(owner, comic.series.first())
    add_media(owner, first, upload())
    add_media(owner, second, upload())
    assert comic_storage_bytes(comic) == 2 * len(upload().read())


@pytest.mark.django_db
def test_removing_media_frees_bytes():
    owner, comic, page = scene()
    media = add_media(owner, page, upload())
    remove_media(owner, media)
    assert comic_storage_bytes(comic) == 0


@pytest.mark.django_db
def test_ensure_within_quota_rejects_over_cap(settings):
    owner, comic, page = scene()
    settings.MEDIA_STORAGE_QUOTA_BYTES = comic_storage_bytes(comic) + 1
    with pytest.raises(ValidationError) as exc:
        ensure_within_quota(comic, 2)
    data = exc.value.message_dict
    assert {"used_bytes", "quota_bytes", "needed_bytes"} <= set(data)


@pytest.mark.django_db
def test_add_media_blocked_when_over_quota(settings):
    owner, comic, page = scene()
    settings.MEDIA_STORAGE_QUOTA_BYTES = 1
    with pytest.raises(ValidationError):
        add_media(owner, page, upload())
    from media.models import Media

    assert Media.objects.count() == 0
