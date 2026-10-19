from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from billing.models import ComicPlan, PlanStatus
from comics.publishing import publish_page
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
    from pathlib import Path

    from media.models import Media

    assert Media.objects.count() == 0
    assert not any(path.is_file() for path in Path(settings.MEDIA_ROOT).rglob("*"))


@pytest.mark.django_db
def test_quota_boundary_allows_exact_fit(settings):
    owner, comic, page = scene()
    size = len(upload().read())
    settings.MEDIA_STORAGE_QUOTA_BYTES = size
    ensure_within_quota(comic, size)


def _plan(comic, status, quota_bytes=10 * 1024**3):
    return ComicPlan.objects.create(comic=comic, status=status, quota_bytes=quota_bytes)


@pytest.mark.django_db
def test_past_due_plan_can_still_upload():
    owner, comic, page = scene()
    _plan(comic, PlanStatus.PAST_DUE)
    assert add_media(owner, page, upload()) is not None


@pytest.mark.django_db
def test_frozen_plan_rejects_new_uploads():
    owner, comic, page = scene()
    _plan(comic, PlanStatus.FROZEN)
    with pytest.raises(ValidationError) as exc:
        add_media(owner, page, upload())
    assert "read-only" in " ".join(exc.value.message_dict["file"])


@pytest.mark.django_db
def test_archived_plan_rejects_new_uploads():
    owner, comic, page = scene()
    _plan(comic, PlanStatus.ARCHIVED)
    with pytest.raises(ValidationError):
        add_media(owner, page, upload())


@pytest.mark.django_db
def test_frozen_plan_still_allows_media_removal():
    owner, comic, page = scene()
    plan = _plan(comic, PlanStatus.ACTIVE)
    media = add_media(owner, page, upload())
    plan.status = PlanStatus.FROZEN
    plan.save(update_fields=["status"])
    remove_media(owner, media)
    assert comic_storage_bytes(comic) == 0


@pytest.mark.django_db
def test_frozen_plan_still_serves_published_pages(client):
    owner, comic, page = scene()
    plan = _plan(comic, PlanStatus.ACTIVE)
    add_media(owner, page, upload(), alt_text="Panel")
    publish_page(owner, page)
    plan.status = PlanStatus.FROZEN
    plan.save(update_fields=["status"])
    response = client.get(f"/pages/{page.id}")
    assert response.status_code == 200


@pytest.mark.django_db
def test_past_due_plan_keeps_its_quota():
    _, comic, _ = scene()
    _plan(comic, PlanStatus.PAST_DUE, quota_bytes=222)
    from media.quota import comic_storage_quota

    assert comic_storage_quota(comic) == 222
