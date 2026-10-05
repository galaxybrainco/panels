# Ticket 5a — Media Upload & Derivatives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give pages ordered images: `Media` uploads with validated originals, Pillow-generated derivatives (thumbnail/display/federation), and a publish gate that requires every image to have alt text and be processed.

**Architecture:** A `media` app owns bytes: `Media`/`MediaDerivative` models, a Pillow-based `derivatives.py`, a permission-checked `add_media`/`remove_media` service, and a `generate_derivatives` task. `comics/publishing.py` consults `page.media` in its gate. Storage goes through Django's default `STORAGES` backend (local filesystem now; Bunny/S3 is 5b). Alt text lives per-image; `Page.alt_text` is removed.

**Tech Stack:** Django 6.1.1, PostgreSQL, Pillow, `django.tasks` (`django_tasks_db`), pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §3 (media & delivery), §4 (`Media`), §5 (federation-capped image, alt text as attachment `name`), §6 (authoring, alt required before publish), §14 ticket 5. Split approved: 5a is upload + derivatives + alt gate; 5b is quota accounting + storage/CDN config.

## Global Constraints

- Synchronous Django only; background work uses `django.tasks` (`@task` + `.enqueue()`). Run Python via `uv run` (Python 3.14).
- Image processing uses **Pillow** (approved deviation from the spec's "prefer pyvips", which needs a system libvips).
- Entity models inherit `core.UUIDModel`; `media` is its own app (approved) and depends on `comics`, never the reverse.
- Alt text is per-`Media`; the publish gate requires ≥1 media, every media's `alt_text` non-empty, and every media `status == ready`.
- Storage uses the default `STORAGES` backend throughout; no Bunny/CDN/Bunny-specific code in 5a.
- `MEDIA_MAX_UPLOAD_BYTES` and `MEDIA_ALLOWED_CONTENT_TYPES` are the single upload guards.
- Tests write files to a tmp `MEDIA_ROOT` and synthesize images with Pillow in memory.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- A page with **zero images**, an image **missing alt text**, or an image still **processing/failed** must not publish. Task 4.
- A **non-image**, **oversized**, or **disallowed content-type** upload must be rejected with no `Media` row and no stored file. Task 3.
- A derivative failure must mark the media `failed` (blocking publish) and not crash the worker. Task 3.
- `generate_derivatives` must be **idempotent** — re-running replaces derivatives without duplicating rows or orphaning files. Task 3.
- Removing a media must delete its **stored files** (original + derivatives), not just the row. Task 3.
- Derivatives must **not upscale** images smaller than the caps. Task 2.

## File Structure

- `media/models.py` — `MediaStatus`, `DerivativeKind`, `Media`, `MediaDerivative`, upload-path callables. (Task 1)
- `media/derivatives.py` — `DERIVATIVE_SPECS`, `render_derivative(image, kind)`. (Task 2)
- `media/services.py` — `add_media`, `remove_media`, validation/hash helpers. (Task 3)
- `media/tasks.py` — `generate_derivatives`. (Task 3)
- `comics/models.py` — remove `Page.alt_text`. (Task 4)
- `comics/publishing.py` — gate consults media. (Task 4)
- `comics/services.py` — drop `alt_text` from `PAGE_OVERRIDE_FIELDS`. (Task 4)
- `config/settings/base.py` — upload guards. (Task 3)
- `tests/media_support.py` — image/upload/`make_ready_media` helpers. (Task 1)
- Tests: `tests/test_media_models.py` (1), `tests/test_media_derivatives.py` (2), `tests/test_media_upload.py` (3), plus gate updates in `tests/test_comics_publishing.py` / `test_comics_scheduling.py` / `test_comics_sweep.py` (4).

---

### Task 1: `Media` and `MediaDerivative` models

**Files:**
- Modify: `pyproject.toml` (add Pillow)
- Create: `media/models.py`
- Create: `media/migrations/0001_initial.py` (generated)
- Create: `tests/media_support.py`
- Test: `tests/test_media_models.py`

**Interfaces:**
- Consumes: `core.models.UUIDModel`, `comics.Page` (string FK `"comics.Page"`).
- Produces:
  - `MediaStatus` (`PENDING="pending"`, `READY="ready"`, `FAILED="failed"`), `DerivativeKind` (`THUMBNAIL="thumbnail"`, `DISPLAY="display"`, `FEDERATION="federation"`).
  - `Media` fields: `page`, `position`, `alt_text`, `original`, `content_type`, `width`, `height`, `bytes`, `sha256`, `status`; `related_name="media"` from `Page`.
  - `MediaDerivative` fields: `media` (`related_name="derivatives"`), `kind`, `file`, `width`, `height`, `bytes`.
  - `tests.media_support.image_bytes/upload/make_ready_media`.

- [ ] **Step 1: Add Pillow and write the failing test**

Run: `uv add pillow`

Create `tests/media_support.py`:

```python
import hashlib
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

DEFAULT_COLOR = (180, 20, 20)


def image_bytes(size=(40, 60), image_format="PNG", color=DEFAULT_COLOR):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=image_format)
    return buffer.getvalue()


def upload(name="panel.png", size=(40, 60), image_format="PNG", content_type="image/png"):
    data = image_bytes(size=size, image_format=image_format)
    return SimpleUploadedFile(name, data, content_type=content_type)


def make_ready_media(page, *, position=1, alt_text="A panel"):
    from media.models import Media, MediaStatus

    data = image_bytes()
    return Media.objects.create(
        page=page,
        position=position,
        alt_text=alt_text,
        original=f"media/{page.id}/{position}.png",
        content_type="image/png",
        width=40,
        height=60,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        status=MediaStatus.READY,
    )
```

Create `tests/test_media_models.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from comics.services import create_comic, create_page, create_series
from media.models import DerivativeKind, Media, MediaDerivative, MediaStatus
from tests.media_support import make_ready_media


@pytest.fixture
def page():
    owner = get_user_model().objects.create_user(email="owner@example.com", password="x")
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
    assert media.status == MediaStatus.PENDING
    assert media.alt_text == ""
    assert media.page == page


@pytest.mark.django_db
def test_media_position_is_unique_per_page(page):
    make_ready_media(page, position=1)
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
    make_ready_media(page, position=2)
    make_ready_media(page, position=1)
    assert [item.position for item in page.media.all()] == [1, 2]


@pytest.mark.django_db
def test_derivative_kind_is_unique_and_cascades(page):
    media = make_ready_media(page, position=1)
    MediaDerivative.objects.create(
        media=media,
        kind=DerivativeKind.THUMBNAIL,
        file="media/x/thumbnail.webp",
        width=10,
        height=20,
        bytes=50,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        MediaDerivative.objects.create(
            media=media,
            kind=DerivativeKind.THUMBNAIL,
            file="media/x/thumbnail2.webp",
            width=10,
            height=20,
            bytes=50,
        )
    media.delete()
    assert MediaDerivative.objects.count() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_media_models.py -q`
Expected: ERROR — `media.models` has no `Media`/`MediaDerivative`.

- [ ] **Step 3: Implement `media/models.py`**

```python
import os

from django.db import models

from core.models import UUIDModel


class MediaStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class DerivativeKind(models.TextChoices):
    THUMBNAIL = "thumbnail", "Thumbnail"
    DISPLAY = "display", "Display"
    FEDERATION = "federation", "Federation"


def _original_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    return f"media/{instance.id}/original{ext}"


def _derivative_upload_to(instance, filename):
    return f"media/{instance.media_id}/{instance.kind}.webp"


class Media(UUIDModel):
    page = models.ForeignKey(
        "comics.Page", on_delete=models.CASCADE, related_name="media"
    )
    position = models.PositiveIntegerField(default=0)
    alt_text = models.TextField(blank=True, default="")
    original = models.FileField(upload_to=_original_upload_to)
    content_type = models.CharField(max_length=64)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=MediaStatus.choices, default=MediaStatus.PENDING
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["page", "position"], name="unique_media_position_per_page"
            )
        ]
        ordering = ["page", "position", "id"]

    def __str__(self):
        return f"{self.page} media #{self.position}"


class MediaDerivative(UUIDModel):
    media = models.ForeignKey(
        Media, on_delete=models.CASCADE, related_name="derivatives"
    )
    kind = models.CharField(max_length=16, choices=DerivativeKind.choices)
    file = models.FileField(upload_to=_derivative_upload_to)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    bytes = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["media", "kind"], name="unique_derivative_kind_per_media"
            )
        ]
        ordering = ["media", "kind"]

    def __str__(self):
        return f"{self.media} {self.kind}"
```

- [ ] **Step 4: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations media
uv run python manage.py migrate
uv run pytest tests/test_media_models.py -q
```
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock media/models.py media/migrations tests/media_support.py tests/test_media_models.py
git commit -m "feat: add Media and MediaDerivative models"
```

---

### Task 2: Pillow derivative rendering

**Files:**
- Create: `media/derivatives.py`
- Test: `tests/test_media_derivatives.py`

**Interfaces:**
- Consumes: `media.models.DerivativeKind`.
- Produces: `DERIVATIVE_SPECS: dict[str, dict]`, `render_derivative(image, kind) -> (io.BytesIO, int, int)` (WebP bytes, width, height).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_media_derivatives.py`:

```python
from io import BytesIO

from PIL import Image

from media.derivatives import render_derivative
from media.models import DerivativeKind


def _image(size, mode="RGB"):
    buffer = BytesIO()
    Image.new(mode, size, (120, 30, 30) if mode == "RGB" else 128).save(
        buffer, format="PNG"
    )
    buffer.seek(0)
    image = Image.open(buffer)
    image.load()
    return image


def test_thumbnail_caps_longest_side():
    buffer, width, height = render_derivative(_image((1200, 800)), DerivativeKind.THUMBNAIL)
    assert (width, height) == (400, round(800 * 400 / 1200))
    assert Image.open(buffer).format == "WEBP"


def test_display_caps_width():
    buffer, width, height = render_derivative(_image((3000, 1000)), DerivativeKind.DISPLAY)
    assert (width, height) == (1600, round(1000 * 1600 / 3000))


def test_federation_caps_longest_side():
    _, width, height = render_derivative(_image((5000, 3000)), DerivativeKind.FEDERATION)
    assert max(width, height) == 4096


def test_small_images_are_not_upscaled():
    _, width, height = render_derivative(_image((100, 80)), DerivativeKind.THUMBNAIL)
    assert (width, height) == (100, 80)


def test_exif_orientation_is_applied():
    buffer = BytesIO()
    image = Image.new("RGB", (400, 200), (5, 5, 5))
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 CW on display
    image.save(buffer, format="JPEG", exif=exif)
    buffer.seek(0)
    oriented = Image.open(buffer)
    oriented.load()
    _, width, height = render_derivative(oriented, DerivativeKind.THUMBNAIL)
    assert (width, height) == (200, 400)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_media_derivatives.py -q`
Expected: ERROR — `media.derivatives` does not exist.

- [ ] **Step 3: Implement `media/derivatives.py`**

```python
from io import BytesIO

from PIL import Image, ImageOps

from media.models import DerivativeKind

DERIVATIVE_SPECS = {
    DerivativeKind.THUMBNAIL: {"max_longest_side": 400, "quality": 80},
    DerivativeKind.DISPLAY: {"max_width": 1600, "quality": 82},
    DerivativeKind.FEDERATION: {"max_longest_side": 4096, "quality": 85},
}


def _prepare(image):
    transposed = ImageOps.exif_transpose(image)
    image = transposed if transposed is not None else image
    if image.mode == "P":
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    elif image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    return image


def _fit_longest(image, longest):
    if max(image.width, image.height) <= longest:
        return image
    scale = longest / max(image.width, image.height)
    return image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )


def _fit_width(image, max_width):
    if image.width <= max_width:
        return image
    scale = max_width / image.width
    return image.resize(
        (max_width, round(image.height * scale)), Image.Resampling.LANCZOS
    )


def render_derivative(image, kind):
    spec = DERIVATIVE_SPECS[kind]
    prepared = _prepare(image)
    if "max_width" in spec:
        rendered = _fit_width(prepared, spec["max_width"])
    else:
        rendered = _fit_longest(prepared, spec["max_longest_side"])
    buffer = BytesIO()
    rendered.save(buffer, format="WEBP", quality=spec["quality"])
    buffer.seek(0)
    return buffer, rendered.width, rendered.height
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_media_derivatives.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add media/derivatives.py tests/test_media_derivatives.py
git commit -m "feat: render media derivatives with Pillow"
```

---

### Task 3: Upload/removal services and the derivative task

**Files:**
- Modify: `config/settings/base.py` (upload guards)
- Create: `media/services.py`, `media/tasks.py`
- Test: `tests/test_media_upload.py`

**Interfaces:**
- Consumes: Task 1 `Media`/`MediaDerivative`/`MediaStatus`; Task 2 `DERIVATIVE_SPECS`/`render_derivative`; `comics.permissions.can_author`; `comics.models.Page`.
- Produces: `media.services.add_media(user, page, uploaded_file, *, alt_text="") -> Media`, `media.services.remove_media(user, media) -> None`, `media.tasks.generate_derivatives(media_id) -> None`.

- [ ] **Step 1: Add the settings guards and write the failing tests**

In `config/settings/base.py`, immediately after `MEDIA_ROOT = BASE_DIR / "uploads"`:

```python
MEDIA_MAX_UPLOAD_BYTES = env.int("MEDIA_MAX_UPLOAD_BYTES", default=25_000_000)
MEDIA_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
```

Create `tests/test_media_upload.py`:

```python
import hashlib

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import PermissionDenied, ValidationError
from django_tasks_db.models import DBTaskResult

from comics.models import ComicRole
from comics.services import create_comic, create_page, create_series
from media.models import DerivativeKind, Media, MediaStatus
from media.services import add_media, remove_media
from media.tasks import generate_derivatives
from tests.media_support import image_bytes, upload


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def scene():
    owner = get_user_model().objects.create_user(email="owner@example.com", password="x")
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
    bad = SimpleUploadedFile("bad.png", b"not really an image", content_type="image/png")
    with pytest.raises(ValidationError):
        add_media(owner, page, bad)
    assert Media.objects.count() == 0


@pytest.mark.django_db
def test_add_media_rejects_disallowed_content_type(scene):
    owner, _, page = scene
    bad = SimpleUploadedFile("x", image_bytes(), content_type="application/octet-stream")
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_media_upload.py -q`
Expected: ERROR — `media.services`/`media.tasks` do not exist.

- [ ] **Step 3: Implement `media/tasks.py`**

```python
import logging

from django.core.files.base import ContentFile
from django.tasks import task
from PIL import Image, UnidentifiedImageError

from media.derivatives import DERIVATIVE_SPECS, render_derivative
from media.models import Media, MediaDerivative, MediaStatus

logger = logging.getLogger(__name__)


def _replace_derivatives(media, generated):
    for derivative in media.derivatives.all():
        derivative.file.delete(save=False)
        derivative.delete()
    for kind, buffer, width, height in generated:
        derivative = MediaDerivative(
            media=media,
            kind=kind,
            width=width,
            height=height,
            bytes=buffer.getbuffer().nbytes,
        )
        derivative.file.save(f"{kind}.webp", ContentFile(buffer.getvalue()), save=False)
        derivative.save()


@task
def generate_derivatives(media_id):
    media = Media.objects.filter(pk=media_id).first()
    if media is None:
        return
    try:
        generated = []
        media.original.open("rb")
        with Image.open(media.original) as image:
            image.load()
            for kind in DERIVATIVE_SPECS:
                buffer, width, height = render_derivative(image, kind)
                generated.append((kind, buffer, width, height))
        _replace_derivatives(media, generated)
        media.status = MediaStatus.READY
        media.save(update_fields=["status", "updated_at"])
    except (UnidentifiedImageError, OSError) as exc:
        logger.warning("Derivative generation failed for media %s: %s", media_id, exc)
        media.status = MediaStatus.FAILED
        media.save(update_fields=["status", "updated_at"])
```

- [ ] **Step 4: Implement `media/services.py`**

```python
import hashlib

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from PIL import Image, UnidentifiedImageError

from comics import permissions
from comics.models import Page
from media.models import Media, MediaStatus

DEFAULT_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _validate_upload(uploaded_file):
    max_bytes = getattr(settings, "MEDIA_MAX_UPLOAD_BYTES", 25_000_000)
    allowed = set(
        getattr(settings, "MEDIA_ALLOWED_CONTENT_TYPES", DEFAULT_ALLOWED_CONTENT_TYPES)
    )
    if uploaded_file.size > max_bytes:
        raise ValidationError({"file": "That image is too large."})
    if getattr(uploaded_file, "content_type", None) not in allowed:
        raise ValidationError({"file": "Unsupported image type."})
    try:
        with Image.open(uploaded_file) as image:
            image.verify()
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            width, height = image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValidationError({"file": "That file is not a valid image."}) from exc
    finally:
        uploaded_file.seek(0)
    return width, height


def _sha256(uploaded_file):
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


@transaction.atomic
def add_media(user, page, uploaded_file, *, alt_text=""):
    if not permissions.can_author(user, page.series.comic):
        raise PermissionDenied("You cannot add media to this comic.")
    width, height = _validate_upload(uploaded_file)
    digest = _sha256(uploaded_file)
    locked_page = Page.objects.select_for_update().get(pk=page.pk)
    current = locked_page.media.aggregate(Max("position"))["position__max"]
    media = Media(
        page=locked_page,
        position=(current or 0) + 1,
        alt_text=alt_text,
        content_type=uploaded_file.content_type,
        width=width,
        height=height,
        bytes=uploaded_file.size,
        sha256=digest,
        status=MediaStatus.PENDING,
    )
    media.original.save(uploaded_file.name, uploaded_file, save=False)
    media.save()
    from media.tasks import generate_derivatives

    generate_derivatives.enqueue(str(media.id))
    return media


@transaction.atomic
def remove_media(user, media):
    if not permissions.can_author(user, media.page.series.comic):
        raise PermissionDenied("You cannot remove media from this comic.")
    for derivative in media.derivatives.all():
        derivative.file.delete(save=False)
    media.original.delete(save=False)
    media.delete()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_media_upload.py -q`
Expected: PASS (11 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add config/settings/base.py media/services.py media/tasks.py tests/test_media_upload.py
git commit -m "feat: upload media with derivatives via a background task"
```

---

### Task 4: Publish gate on media; retire `Page.alt_text`

**Files:**
- Modify: `comics/models.py` (remove `Page.alt_text`)
- Modify: `comics/services.py` (drop `alt_text` from `PAGE_OVERRIDE_FIELDS`)
- Modify: `comics/publishing.py` (gate on media)
- Create: `comics/migrations/0003_remove_page_alt_text.py` (generated)
- Modify: `tests/test_comics_publishing.py`, `tests/test_comics_scheduling.py`, `tests/test_comics_sweep.py`

**Interfaces:**
- Consumes: Task 1 `media.models.MediaStatus`, `tests.media_support.make_ready_media`.
- Produces: `_enforce_publish_gates(page)` raising `ValidationError` keyed `"media"` for missing/misconfigured media.

- [ ] **Step 1: Remove `Page.alt_text` and update the gate**

In `comics/models.py`, delete the line `alt_text = models.TextField(blank=True, default="")` from `Page`.

In `comics/services.py`, remove `"alt_text",` from `PAGE_OVERRIDE_FIELDS`.

In `comics/publishing.py`, replace `_enforce_publish_gates` with:

```python
def _enforce_publish_gates(page) -> None:
    from media.models import MediaStatus

    media = list(page.media.all())
    if not media:
        raise ValidationError({"media": "Add at least one image before publishing."})
    if any(not item.alt_text.strip() for item in media):
        raise ValidationError({"media": "Every image needs alt text before publishing."})
    if any(item.status != MediaStatus.READY for item in media):
        raise ValidationError({"media": "Images are still processing."})
    if page.sensitive and not page.content_warning.strip():
        raise ValidationError(
            {"content_warning": "A content warning is required for sensitive pages."}
        )
```

- [ ] **Step 2: Update the existing comics tests and add the new gate tests**

In `tests/test_comics_publishing.py`, change the import block to add `make_ready_media` and `MediaStatus`:

```python
from media.models import MediaStatus
from tests.media_support import make_ready_media
```

Change the `_page` helper to attach a ready image:

```python
def _page(owner, **page_fields):
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series, **page_fields)
    make_ready_media(page, position=1, alt_text="A panel")
    return comic, series, page
```

Replace `test_publish_requires_alt_text` with:

```python
@pytest.mark.django_db
def test_publish_requires_at_least_one_image():
    owner = _user()
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    with pytest.raises(ValidationError) as exc:
        publish_page(owner, page)
    assert "media" in exc.value.message_dict


@pytest.mark.django_db
def test_publish_requires_alt_text_on_every_image():
    owner = _user()
    _, _, page = _page(owner)
    page.media.update(alt_text="")
    with pytest.raises(ValidationError) as exc:
        publish_page(owner, page)
    assert "media" in exc.value.message_dict


@pytest.mark.django_db
def test_publish_requires_images_to_be_ready():
    owner = _user()
    _, _, page = _page(owner)
    page.media.update(status=MediaStatus.PENDING)
    with pytest.raises(ValidationError) as exc:
        publish_page(owner, page)
    assert "media" in exc.value.message_dict
```

In `tests/test_comics_scheduling.py`, change the import block to add `from tests.media_support import make_ready_media` and change `_page` to:

```python
def _page(owner):
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_ready_media(page, position=1, alt_text="A panel")
    return comic, page
```

In `test_task_leaves_page_scheduled_when_gates_fail`, change the page construction line `page = create_page(owner, series, alt_text="")` to:

```python
    page = create_page(owner, series)
```

In `tests/test_comics_sweep.py`, add `from tests.media_support import make_ready_media` and replace `_due_page` and the two direct `create_page(..., alt_text=...)` calls:

```python
def _due_page(owner, series, *, with_media=True):
    page = create_page(owner, series)
    if with_media:
        make_ready_media(page, position=1, alt_text="A panel")
    page.status = PageStatus.SCHEDULED
    page.scheduled_for = timezone.now() - timedelta(minutes=5)
    page.scheduled_by = owner
    page.save()
    return page
```

In `test_sweep_publishes_only_due_pages`, replace the `future` lines with:

```python
    future = create_page(owner, series)
    make_ready_media(future, position=1, alt_text="Another panel")
    schedule_page(owner, future, timezone.now() + timedelta(hours=1))
```

In `test_sweep_skips_pages_that_fail_gates`, replace the two page lines with:

```python
    broken = _due_page(owner, series, with_media=False)
    good = _due_page(owner, series)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_comics_publishing.py tests/test_comics_scheduling.py tests/test_comics_sweep.py -q`
Expected: FAIL — the gate still reads `page.alt_text` (`AttributeError`) and the helpers now pass no alt.

- [ ] **Step 4: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations comics
uv run python manage.py migrate
uv run pytest tests/test_comics_publishing.py tests/test_comics_scheduling.py tests/test_comics_sweep.py -q
```
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green, no missing migrations.

- [ ] **Step 6: Commit**

```bash
git add comics/models.py comics/services.py comics/publishing.py comics/migrations tests/test_comics_publishing.py tests/test_comics_scheduling.py tests/test_comics_sweep.py
git commit -m "feat: require ready, alt-texted media before publishing a page"
```

---

## Self-Review

**Spec coverage (Ticket 5a scope):** `Media` model with original + metadata (§4); derivatives incl. a federation-capped variant (§3/§5) via Pillow; alt text per image required at publish and CW-if-sensitive retained (§6/§8); upload guards; async derivative generation via `django.tasks` (§3); storage via the default backend (§3). Out of scope by design (5b/7/8): per-comic byte accounting and quota enforcement, Bunny Storage/CDN + immutable cache headers, remote-media cache, responsive widths/AVIF, view/URL layer, `Media`-aware federation attachment serialization (Ticket 6).

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `MediaStatus`/`DerivativeKind` are the single vocabularies; `DERIVATIVE_SPECS`/`render_derivative` are the single rendering path; `add_media`/`remove_media` are the single mutation path; the gate raises `ValidationError` keyed `"media"`.

**Review Focus coverage:** zero/alt-missing/not-ready media block publish — Task 4; bad uploads rejected — Task 3; failure marks `failed` — Task 3; idempotent task — Task 3; removal deletes files — Task 3; no upscaling — Task 2.
