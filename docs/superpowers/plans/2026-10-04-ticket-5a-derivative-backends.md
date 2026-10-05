# Ticket 5a (revision) — Media Uploads & Pluggable Derivative URLs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace self-generated Pillow derivatives with a pluggable derivative-**URL** layer, defaulting to a local passthrough backend and shipping a Bunny Optimizer backend first.

**Architecture:** `media/derivative_urls.py` owns the vocabulary (`DerivativeKind`), the transform specs, a `DerivativeBackend` protocol with `LocalDerivativeBackend` and `BunnyOptimizerBackend`, and the single public `derivative_url(media, kind)` entry point selected by `MEDIA_DERIVATIVE_BACKEND`. The Pillow derivative pipeline (`media/derivatives.py`, `media/tasks.py`, `MediaDerivative`, `MediaStatus`, `Media.status`) is removed; Pillow stays only for upload validation. The publish gate simplifies to "≥1 media, all alt-texted" plus CW-if-sensitive.

**Tech Stack:** Django 6.1.1, PostgreSQL, Pillow (validation only), pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §3 (Bunny CDN/Storage; operator-configurable), §4 (`Media`), §5 (federation-capped image, alt as attachment `name`), §6 (alt required before publish), §14 ticket 5. This revises the derivative approach in `docs/superpowers/plans/2026-10-04-ticket-5a-media.md`; storage/Bunny-pull-zone wiring is 5b.

## Global Constraints

- Synchronous Django only. Run Python via `uv run` (Python 3.14).
- Derivatives are **URLs**, not files: the backend returns a URL for `(media, kind)`; nothing is pre-generated.
- Backend selected by `MEDIA_DERIVATIVE_BACKEND` (dotted path); default `media.derivative_urls.LocalDerivativeBackend`. `BunnyOptimizerBackend` requires `MEDIA_CDN_BASE_URL`.
- Battery of specs: thumbnail `width=400 q80`, display `width=1600 q82`, both `format=webp`; federation `width=4096 q85 format=jpeg` (maximum server compatibility).
- `DerivativeKind` is the single derivative vocabulary; `derivative_url(media, kind)` is the single public entry point.
- Pillow remains a dependency for upload validation only; no derivative files/rows/status.
- Entity models inherit `core.UUIDModel`.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- The Bunny backend must include the correct per-kind transform parameters and preserve the original object path. Task 1.
- Selecting the Bunny backend without `MEDIA_CDN_BASE_URL` must fail loudly (`ImproperlyConfigured`), not emit a broken URL. Task 1.
- Switching backends must be config-only — no caller changes; `derivative_url` must honor `MEDIA_DERIVATIVE_BACKEND`. Task 1.
- A page with zero images, or an image missing alt text, must not publish. Task 2.
- Removing the processing state must not leave the gate or any helper referencing `Media.status`/`MediaStatus`. Task 2.

## File Structure

- `media/derivative_urls.py` — new: kinds, specs, backends, factory, `derivative_url`. (Task 1)
- `config/settings/base.py` — `MEDIA_DERIVATIVE_BACKEND`, `MEDIA_CDN_BASE_URL`. (Task 1)
- `tests/test_derivative_urls.py` — new. (Task 1)
- Delete: `media/derivatives.py`, `media/tasks.py`, `tests/test_media_derivatives.py`. (Task 2)
- `media/models.py` — drop `MediaStatus`, `DerivativeKind`, `MediaDerivative`, `Media.status`. (Task 2)
- `media/migrations/0001_initial.py` — regenerated. (Task 2)
- `media/services.py` — drop the task enqueue. (Task 2)
- `comics/publishing.py` — gate without the ready check. (Task 2)
- `tests/media_support.py` + comics/media tests — helper rename and de-status. (Task 2)

---

### Task 1: Pluggable derivative-URL backends

**Files:**
- Create: `media/derivative_urls.py`
- Modify: `config/settings/base.py`
- Test: `tests/test_derivative_urls.py`

**Interfaces:**
- Consumes: `django.conf.settings`, `django.utils.module_loading.import_string`, `media.original` (duck-typed `.name`/`.url`).
- Produces:
  - `DerivativeKind` (`THUMBNAIL`/`DISPLAY`/`FEDERATION`), `DERIVATIVE_SPECS: dict[str, dict]`.
  - `DerivativeBackend` with `url(media, kind) -> str`; `LocalDerivativeBackend`, `BunnyOptimizerBackend(base_url=None)`.
  - `get_derivative_backend() -> DerivativeBackend`, `derivative_url(media, kind) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_derivative_urls.py`:

```python
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from urllib.parse import parse_qs, urlparse

from media.derivative_urls import (
    BunnyOptimizerBackend,
    DerivativeKind,
    LocalDerivativeBackend,
    derivative_url,
    get_derivative_backend,
)


class _StubOriginal:
    name = "media/abc/original.png"
    url = "media/abc/original.png"


class _StubMedia:
    original = _StubOriginal()


@pytest.fixture
def media():
    return _StubMedia()


def test_local_backend_returns_absolute_storage_url(media):
    url = LocalDerivativeBackend().url(media, DerivativeKind.THUMBNAIL)
    assert url == "http://testserver/media/abc/original.png"


def test_bunny_backend_preserves_path_and_adds_transform_params(media):
    url = BunnyOptimizerBackend("https://cdn.test").url(media, DerivativeKind.DISPLAY)
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://cdn.test/media/abc/original.png"
    )
    query = parse_qs(parsed.query)
    assert query == {"width": ["1600"], "quality": ["82"], "format": ["webp"]}


def test_bunny_federation_variant_is_jpeg(media):
    query = parse_qs(
        urlparse(
            BunnyOptimizerBackend("https://cdn.test").url(media, DerivativeKind.FEDERATION)
        ).query
    )
    assert query["format"] == ["jpeg"]
    assert query["width"] == ["4096"]


def test_bunny_backend_requires_base_url(media):
    with override_settings(MEDIA_CDN_BASE_URL=""):
        with pytest.raises(ImproperlyConfigured):
            BunnyOptimizerBackend()


@override_settings(
    MEDIA_DERIVATIVE_BACKEND="media.derivative_urls.BunnyOptimizerBackend",
    MEDIA_CDN_BASE_URL="https://cdn.test",
)
def test_backend_is_config_driven(media):
    assert isinstance(get_derivative_backend(), BunnyOptimizerBackend)
    assert derivative_url(media, DerivativeKind.THUMBNAIL).startswith(
        "https://cdn.test/media/abc/original.png?"
    )


def test_default_backend_is_local(media):
    assert isinstance(get_derivative_backend(), LocalDerivativeBackend)
    assert derivative_url(media, DerivativeKind.THUMBNAIL).startswith(
        "http://testserver/"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_derivative_urls.py -q`
Expected: ERROR — `media.derivative_urls` does not exist.

- [ ] **Step 3: Implement `media/derivative_urls.py`**

```python
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.module_loading import import_string


class DerivativeKind(models.TextChoices):
    THUMBNAIL = "thumbnail", "Thumbnail"
    DISPLAY = "display", "Display"
    FEDERATION = "federation", "Federation"


DERIVATIVE_SPECS = {
    DerivativeKind.THUMBNAIL: {"width": 400, "quality": 80, "format": "webp"},
    DerivativeKind.DISPLAY: {"width": 1600, "quality": 82, "format": "webp"},
    DerivativeKind.FEDERATION: {"width": 4096, "quality": 85, "format": "jpeg"},
}


def _absolute_media_url(media) -> str:
    base = settings.INSTANCE_URL.rstrip("/")
    return f"{base}/{media.original.url.lstrip('/')}"


class DerivativeBackend:
    def url(self, media, kind) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class LocalDerivativeBackend(DerivativeBackend):
    """Development/self-host backend: serve the original, no transforms."""

    def url(self, media, kind) -> str:
        return _absolute_media_url(media)


class BunnyOptimizerBackend(DerivativeBackend):
    """Bunny Optimizer dynamic-images backend (query-param transforms)."""

    def __init__(self, base_url=None):
        base_url = base_url or getattr(settings, "MEDIA_CDN_BASE_URL", "")
        if not base_url:
            raise ImproperlyConfigured(
                "MEDIA_CDN_BASE_URL must be set for the Bunny optimizer backend."
            )
        self.base_url = base_url.rstrip("/")

    def url(self, media, kind) -> str:
        query = urlencode(DERIVATIVE_SPECS[kind])
        return f"{self.base_url}/{media.original.name.lstrip('/')}?{query}"


def get_derivative_backend() -> DerivativeBackend:
    path = getattr(
        settings,
        "MEDIA_DERIVATIVE_BACKEND",
        "media.derivative_urls.LocalDerivativeBackend",
    )
    return import_string(path)()


def derivative_url(media, kind) -> str:
    return get_derivative_backend().url(media, kind)
```

- [ ] **Step 4: Add the settings**

In `config/settings/base.py`, immediately after `MEDIA_ALLOWED_CONTENT_TYPES`:

```python
MEDIA_DERIVATIVE_BACKEND = env(
    "MEDIA_DERIVATIVE_BACKEND",
    default="media.derivative_urls.LocalDerivativeBackend",
)
MEDIA_CDN_BASE_URL = env("MEDIA_CDN_BASE_URL", default="")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_derivative_urls.py -q`
Expected: PASS (6 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add media/derivative_urls.py config/settings/base.py tests/test_derivative_urls.py
git commit -m "feat: add pluggable derivative-URL backends with a Bunny optimizer backend"
```

---

### Task 2: Retire the Pillow derivative pipeline and simplify the gate

**Files:**
- Delete: `media/derivatives.py`, `media/tasks.py`, `tests/test_media_derivatives.py`
- Modify: `media/models.py`, `media/migrations/0001_initial.py` (regenerated), `media/services.py`
- Modify: `comics/publishing.py`
- Modify: `tests/media_support.py`, `tests/test_media_models.py`, `tests/test_media_upload.py`, `tests/test_comics_publishing.py`, `tests/test_comics_scheduling.py`, `tests/test_comics_sweep.py`

**Interfaces:**
- Consumes: Task 1 `media.derivative_urls.derivative_url`.
- Produces: `Media` without `status`/derivatives; `_enforce_publish_gates(page)` raising `ValidationError` keyed `"media"` for missing/un-alt-texted media; helper `tests.media_support.make_media`.

- [ ] **Step 1: Delete the Pillow pipeline and strip the model**

```bash
git rm media/derivatives.py media/tasks.py tests/test_media_derivatives.py
```

Replace `media/models.py` with:

```python
import os

from django.db import models

from core.models import UUIDModel


def _original_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    return f"media/{instance.id}/original{ext}"


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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["page", "position"], name="unique_media_position_per_page"
            )
        ]
        ordering = ["page", "position", "id"]

    def __str__(self):
        return f"{self.page} media #{self.position}"
```

In `media/services.py`, delete the two-line enqueue at the end of `add_media`:

```python
    from media.tasks import generate_derivatives

    generate_derivatives.enqueue(str(media.id))
```

so `add_media` ends with `return media` immediately after `media.save()`.

Regenerate the migration (this branch is unmerged, so replace the initial migration):

```bash
rm media/migrations/0001_initial.py
uv run python manage.py makemigrations media
uv run python manage.py migrate
```

- [ ] **Step 2: Simplify the publish gate**

Replace `_enforce_publish_gates` in `comics/publishing.py` with:

```python
def _enforce_publish_gates(page) -> None:
    media = list(page.media.all())
    if not media:
        raise ValidationError({"media": "Add at least one image before publishing."})
    if any(not item.alt_text.strip() for item in media):
        raise ValidationError({"media": "Every image needs alt text before publishing."})
    if page.sensitive and not page.content_warning.strip():
        raise ValidationError(
            {"content_warning": "A content warning is required for sensitive pages."}
        )
```

- [ ] **Step 3: Update the test helper**

In `tests/media_support.py`, replace `make_ready_media` with:

```python
def make_media(page, *, position=1, alt_text="A panel"):
    from media.models import Media

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
    )
```

- [ ] **Step 4: Update the tests**

In `tests/test_media_models.py`: drop `DerivativeKind`/`MediaDerivative`/`MediaStatus` from the import (keep `Media`), replace the `make_ready_media` import with `make_media`, remove the `assert media.status == MediaStatus.PENDING` line from `test_media_defaults`, change the `make_ready_media(...)` calls to `make_media(...)`, and delete `test_derivative_kind_is_unique_and_cascades` entirely.

In `tests/test_media_upload.py`: remove from the imports `DerivativeKind`, `MediaStatus`, and `generate_derivatives`; delete the `_boom` helper and every `test_generate_derivatives_*` test; and rewrite `test_remove_media_deletes_files_and_row` as:

```python
@pytest.mark.django_db
def test_remove_media_deletes_file_and_row(scene):
    owner, _, page = scene
    media = add_media(owner, page, upload())
    original_name = media.original.name
    remove_media(owner, media)
    assert not Media.objects.filter(pk=media.pk).exists()
    assert not default_storage.exists(original_name)
```

In `tests/test_comics_publishing.py`: change the import `from media.models import MediaStatus` and `from tests.media_support import make_ready_media` to `from tests.media_support import make_media`; rename the `make_ready_media(...)` call in `_page` to `make_media(...)`; and delete `test_publish_requires_images_to_be_ready`.

In `tests/test_comics_scheduling.py` and `tests/test_comics_sweep.py`: change `from tests.media_support import make_ready_media` to `make_media` and rename every call.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_media_upload.py tests/test_media_models.py tests/test_comics_publishing.py tests/test_comics_scheduling.py tests/test_comics_sweep.py -q`
Expected: PASS, with no references to the removed names (the RED here is the import/name errors you fix in this step; run once before editing to confirm they fail).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green, no missing migrations.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: drop Pillow derivatives; gate on alt-texted media only"
```

---

## Self-Review

**Spec coverage (Ticket 5a revision):** `Media` original + metadata + alt (§4); derivative URLs incl. a federation variant (§3/§5) via a pluggable backend with a Bunny Optimizer plugin (§3); alt text required at publish, CW-if-sensitive retained (§6/§8); upload guards; storage stays abstract (Bunny Storage/pull zone is 5b); no processing state. Out of scope by design: quota accounting, Bunny Storage/CDN wiring, remote-media cache, view/URL layer, AP attachment serialization (Ticket 6).

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `DerivativeKind`/`DERIVATIVE_SPECS`/`derivative_url` are the single vocabulary and entry point; `Media` has no `status`; `_enforce_publish_gates` is keyed `"media"`; `make_media` is the single test helper.

**Review Focus coverage:** Bunny params + path — Task 1; missing base URL — Task 1; config-driven switch — Task 1; zero/alt-missing media block publish — Task 2; no dangling processing references — Task 2 (full-suite + grep).