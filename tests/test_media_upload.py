import hashlib

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django_tasks_db.models import DBTaskResult

from comics.models import ComicRole
from comics.services import create_comic, create_page, create_series
from media.models import DerivativeKind, Media, MediaStatus
from media.services import add_media, remove_media
from media.tasks import generate_derivatives
from tests.media_support import corrupted_png, image_bytes, spoofed_upload, upload


def _boom(image, kind):
    raise ValueError("boom")


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def scene():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    return owner, comic, create_page(owner, series)


@pytest.mark.django_db
def test_add_media_stores_original_metadata_and_enqueues(scene):
    owner, _, page = scene
    data = image_bytes()
    media = add_media(owner, page, upload(), alt_text="A panel")
    assert media.status == MediaStatus.PENDING
    assert media.position == 1
    assert (media.width, media.height) == (40, 60)
    assert media.bytes == len(data)
    assert media.sha256 == hashlib.sha256(data).hexdigest()
    assert media.alt_text == "A panel"
    assert default_storage.exists(media.original.name)
    assert DBTaskResult.objects.count() == 1


@pytest.mark.django_db
def test_add_media_assigns_incrementing_positions(scene):
    owner, _, page = scene
    first = add_media(owner, page, upload())
    second = add_media(owner, page, upload())
    assert (first.position, second.position) == (1, 2)


@pytest.mark.django_db
def test_add_media_rejects_non_image(scene):
    owner, _, page = scene
    bad = SimpleUploadedFile(
        "bad.png", b"not really an image", content_type="image/png"
    )
    with pytest.raises(ValidationError):
        add_media(owner, page, bad)
    assert Media.objects.count() == 0


@pytest.mark.django_db
def test_add_media_rejects_disallowed_content_type(scene):
    owner, _, page = scene
    bad = SimpleUploadedFile(
        "x", image_bytes(), content_type="application/octet-stream"
    )
    with pytest.raises(ValidationError):
        add_media(owner, page, bad)


@pytest.mark.django_db
def test_add_media_rejects_oversized(scene, settings):
    owner, _, page = scene
    settings.MEDIA_MAX_UPLOAD_BYTES = 10
    with pytest.raises(ValidationError):
        add_media(owner, page, upload())


@pytest.mark.django_db
def test_add_media_permissions(scene):
    owner, comic, page = scene
    contributor = get_user_model().objects.create_user(
        email="contributor@example.com", password="x"
    )
    outsider = get_user_model().objects.create_user(
        email="outsider@example.com", password="x"
    )
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    with pytest.raises(PermissionDenied):
        add_media(outsider, page, upload())
    assert add_media(contributor, page, upload()).position == 1


@pytest.mark.django_db
def test_generate_derivatives_creates_variants_and_marks_ready(scene):
    owner, _, page = scene
    media = add_media(owner, page, upload())
    generate_derivatives.func(str(media.id))
    media.refresh_from_db()
    assert media.status == MediaStatus.READY
    kinds = set(media.derivatives.values_list("kind", flat=True))
    assert kinds == {
        DerivativeKind.THUMBNAIL,
        DerivativeKind.DISPLAY,
        DerivativeKind.FEDERATION,
    }
    for derivative in media.derivatives.all():
        assert default_storage.exists(derivative.file.name)


@pytest.mark.django_db
def test_generate_derivatives_is_idempotent(scene):
    owner, _, page = scene
    media = add_media(owner, page, upload())
    generate_derivatives.func(str(media.id))
    generate_derivatives.func(str(media.id))
    assert media.derivatives.count() == 3


@pytest.mark.django_db
def test_generate_derivatives_marks_failed_on_bad_original(scene):
    owner, _, page = scene
    media = Media.objects.create(
        page=page,
        position=1,
        alt_text="x",
        original=ContentFile(b"not an image", name="bad.png"),
        content_type="image/png",
        width=1,
        height=1,
        bytes=12,
        sha256="0" * 64,
    )
    generate_derivatives.func(str(media.id))
    media.refresh_from_db()
    assert media.status == MediaStatus.FAILED
    assert media.derivatives.count() == 0


@pytest.mark.django_db
def test_remove_media_deletes_files_and_row(scene):
    owner, _, page = scene
    media = add_media(owner, page, upload())
    generate_derivatives.func(str(media.id))
    original_name = media.original.name
    derivative_names = list(media.derivatives.values_list("file", flat=True))
    remove_media(owner, media)
    assert not Media.objects.filter(pk=media.pk).exists()
    assert not default_storage.exists(original_name)
    assert not any(default_storage.exists(name) for name in derivative_names)


@pytest.mark.django_db
def test_remove_media_permissions(scene):
    owner, _, page = scene
    outsider = get_user_model().objects.create_user(
        email="outsider@example.com", password="x"
    )
    media = add_media(owner, page, upload())
    with pytest.raises(PermissionDenied):
        remove_media(outsider, media)


@pytest.mark.django_db
def test_add_media_rejects_corrupted_image(scene):
    owner, _, page = scene
    with pytest.raises(ValidationError):
        add_media(owner, page, corrupted_png())
    assert Media.objects.count() == 0


@pytest.mark.django_db
def test_add_media_rejects_spoofed_image_bytes(scene):
    owner, _, page = scene
    with pytest.raises(ValidationError):
        add_media(owner, page, spoofed_upload())
    assert Media.objects.count() == 0


@pytest.mark.django_db
def test_add_media_records_detected_type_and_extension(scene):
    owner, _, page = scene
    fake = SimpleUploadedFile(
        "photo.jpg", image_bytes(image_format="PNG"), content_type="image/jpeg"
    )
    media = add_media(owner, page, fake)
    assert media.content_type == "image/png"
    assert media.original.name.endswith(".png")


@pytest.mark.django_db
def test_generate_derivatives_marks_failed_on_unexpected_error(scene, monkeypatch):
    owner, _, page = scene
    media = add_media(owner, page, upload())
    monkeypatch.setattr("media.tasks.render_derivative", _boom)
    generate_derivatives.func(str(media.id))
    media.refresh_from_db()
    assert media.status == MediaStatus.FAILED


@pytest.mark.django_db
def test_generate_derivatives_preserves_existing_on_rerender_failure(
    scene, monkeypatch
):
    owner, _, page = scene
    media = add_media(owner, page, upload())
    generate_derivatives.func(str(media.id))
    names = list(media.derivatives.values_list("file", flat=True))
    monkeypatch.setattr("media.tasks.render_derivative", _boom)
    generate_derivatives.func(str(media.id))
    media.refresh_from_db()
    assert media.status == MediaStatus.FAILED
    assert media.derivatives.count() == 3
    assert all(default_storage.exists(name) for name in names)
