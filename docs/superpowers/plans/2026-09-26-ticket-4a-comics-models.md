# Ticket 4a — Comics Domain Models & Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The comics domain — `Comic` (auto-created AP actor), `Tag`, `Series`/`Chapter`/`Page` with ordering, multi-user `ComicRole` (owner/editor/contributor/moderator) with a one-owner invariant, and pure permission helpers — all synchronous and tested. Publishing/scheduling and invites are the follow-up 4b plan.

**Architecture:** The `comics` app gains `models.py` (Comic/Tag/ComicRole/Series/Chapter/Page), `services.py` (`create_comic`, `create_series`, `create_page` with permission checks and position assignment), and `permissions.py` (role lookups). A `Comic` is `OneToOne → Actor`, created via the existing `actors.services.create_local_actor`. Media is Ticket 5, so `Page` has no Media FK yet and the alt-text publish gate is enforced later.

**Tech Stack:** Django 6.1.1, PostgreSQL, existing `actors`/`federation` primitives.

**Spec:** `webcomic-fediverse-plan.md` §4 (Comic, ComicRole, Series/Chapter/Page, Tag), §6 (creator authoring/collaboration), §14 ticket 4.

## Global Constraints

- Synchronous Django only.
- A `Comic` has exactly one `Actor` (`OneToOne`, `PROTECT`); creating a comic creates the actor with a validated handle.
- Exactly one `owner` `ComicRole` per comic; one role row per `(comic, user)`.
- Roles escalate: `owner` > `editor` > `contributor`; `moderator` is orthogonal. Permission checks live in `comics/permissions.py` and are used by services now, views later.
- Pages are ordered by an integer `position` unique within a series; a gag comic is a series with no chapters.
- Audience/federation choices are reused from `federation.activitypub.Audience` plus a local `FederationMode`.
- `alt_text` is stored (blank allowed for drafts) but the "required before publish" gate is deferred to Ticket 5/6 (no Media yet).
- `update_schedule` is structured JSON `{"days": [...], "time": "HH:MM"}`; scheduling execution is 4b.

## Post-approval change — UUID entity IDs

After plan approval, entity models were standardized onto a shared abstract base in a new `core` app:

- `core/models.py` defines the abstract `UUIDModel`: `id = UUIDField(primary_key=True, default=uuid.uuid4, editable=False)`, `created_at`, `updated_at`.
- `Comic`, `Tag`, `Series`, `Chapter`, `Page` inherit `UUIDModel` (dropping duplicate `id`/timestamp declarations).
- `ComicRole` stays a plain `Model` (BigAuto pk + its own `created_at`): it is an internal join table, never externally referenced, so it keeps the smaller integer key.
- `core` is added to `INSTALLED_APPS`.
- Because PR #7 was unmerged, `comics/migrations` was regenerated from scratch as a single `0001_initial.py` (dev DB reset).
- Pinned by `tests/test_comics_ids.py`.

## Review Focus

Spec-implied failure modes no task's happy path exercises; each is pinned by a test in its owning task:

- A comic created without an actor, or with an actor whose handle duplicates another comic — Task 1.
- More than one `owner` on a comic, or a user holding two roles on the same comic — Task 2.
- A contributor able to publish, or a non-owner able to change ownership/settings — Tasks 2, 4.
- Duplicate page `position` within a series, or pages not deterministically ordered — Task 3.
- A page defaulting to the wrong audience/federation instead of the comic's defaults — Task 4.
- Deleting a comic or actor orphaning roles/pages (cascade/protect semantics) — Tasks 1, 3.

## File Structure

- `core/models.py` — abstract `UUIDModel` (UUID pk + timestamps) inherited by comics entities.
- `comics/models.py` — `Tag`, `ContentRating`, `FederationMode`, `Comic`, `ComicRole`, `Series`, `Chapter`, `PageStatus`, `Page`.
- `comics/services.py` — `create_comic`, `create_series`, `create_page`.
- `comics/permissions.py` — `role_for`, `is_owner`, `can_manage_comic`, `can_edit`, `can_publish`, `can_moderate`, `can_contribute`.
- Tests: `tests/test_comics_models.py`, `tests/test_comics_roles.py`, `tests/test_comics_pages.py`, `tests/test_comics_services.py`, `tests/test_comics_ids.py`.

---

### Task 1: `Tag` and `Comic` models plus `create_comic`

**Files:**
- Create: `comics/models.py` (replace empty file), `comics/services.py`
- Create: `comics/migrations/0001_initial.py` (generated)
- Create: `tests/test_comics_models.py`

**Interfaces:**
- Consumes: `actors.services.create_local_actor`, `actors.handles.validate_handle`, `federation.activitypub.Audience`.
- Produces: `Tag`, `ContentRating`, `FederationMode`, `Comic` (fields `actor`, `title`, `slug`, `description`, `content_rating`, `update_schedule`, `default_audience`, `default_federation`, `tags`), and `comics.services.create_comic(owner, handle, title, **kwargs) -> Comic`. `ComicRole` is referenced by `create_comic` and defined in Task 2.

- [ ] **Step 1: Write the failing test**

Create `tests/test_comics_models.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from comics.models import Comic, ContentRating, FederationMode, Tag
from comics.services import create_comic
from federation.activitypub import Audience


@pytest.mark.django_db
def test_create_comic_creates_actor_and_defaults():
    owner = get_user_model().objects.create_user(email="owner@example.com", password="x")
    comic = create_comic(owner, "panels", "Panels")
    assert comic.slug == "panels"
    assert comic.title == "Panels"
    assert comic.actor.is_local
    assert comic.actor.handle == "panels"
    assert comic.content_rating == ContentRating.ALL_AGES
    assert comic.default_audience == Audience.PUBLIC
    assert comic.default_federation == FederationMode.FEDERATED


@pytest.mark.django_db
def test_create_comic_rejects_invalid_handle():
    owner = get_user_model().objects.create_user(email="owner@example.com", password="x")
    with pytest.raises(ValidationError):
        create_comic(owner, "Bad Handle", "Bad")


@pytest.mark.django_db
def test_create_comic_rejects_duplicate_slug():
    owner = get_user_model().objects.create_user(email="owner@example.com", password="x")
    create_comic(owner, "panels", "Panels")
    with pytest.raises(ValidationError):
        create_comic(owner, "panels", "Panels Two")


@pytest.mark.django_db
def test_create_comic_assigns_tags():
    owner = get_user_model().objects.create_user(email="owner@example.com", password="x")
    comic = create_comic(owner, "panels", "Panels", tags=["fantasy", "comedy"])
    assert set(comic.tags.values_list("name", flat=True)) == {"fantasy", "comedy"}
    assert Tag.objects.count() == 2


@pytest.mark.django_db
def test_tag_slug_is_unique():
    Tag.objects.create(name="Fantasy", slug="fantasy")
    assert Comic.objects.count() == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_comics_models.py -v`
Expected: FAIL/ERROR — `comics.models` has no `Comic`/`Tag` and `comics.services` does not exist.

- [ ] **Step 3: Implement `Tag`, `Comic`, and `create_comic`**

Create `comics/models.py`:

```python
from django.db import models

from core.models import UUIDModel
from federation.activitypub import Audience


class ContentRating(models.TextChoices):
    ALL_AGES = "all_ages", "All ages"
    TEEN = "teen", "Teen"


class FederationMode(models.TextChoices):
    FEDERATED = "federated", "Federated"
    LOCAL_ONLY = "local_only", "Local only"


class Tag(UUIDModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    def __str__(self):
        return self.name


class Comic(UUIDModel):
    actor = models.OneToOneField(
        "actors.Actor", on_delete=models.PROTECT, related_name="comic"
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True, default="")
    content_rating = models.CharField(
        max_length=16, choices=ContentRating.choices, default=ContentRating.ALL_AGES
    )
    update_schedule = models.JSONField(default=dict, blank=True)
    default_audience = models.CharField(
        max_length=16, choices=Audience.choices, default=Audience.PUBLIC
    )
    default_federation = models.CharField(
        max_length=16,
        choices=FederationMode.choices,
        default=FederationMode.FEDERATED,
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="comics")

    class Meta:
        ordering = ["title", "id"]

    def __str__(self):
        return self.title

    def set_tags(self, names):
        tags = []
        for name in names:
            tag, _ = Tag.objects.get_or_create(
                name=name, defaults={"slug": slugify(name)}
            )
            tags.append(tag)
        self.tags.set(tags)
```

Create `comics/services.py`:

```python
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.text import slugify

from actors.handles import validate_handle
from actors.models import ActorType
from actors.services import create_local_actor
from actors import crypto  # noqa: F401  (kept explicit for later media wiring)
from comics.models import Comic, ContentRating, FederationMode, Tag
from federation.activitypub import Audience


@transaction.atomic
def create_comic(
    owner,
    handle,
    title,
    *,
    description="",
    content_rating=ContentRating.ALL_AGES,
    default_audience=Audience.PUBLIC,
    default_federation=FederationMode.FEDERATED,
    update_schedule=None,
    tags=None,
    actor_type=ActorType.PERSON,
):
    slug = validate_handle(handle)
    if Comic.objects.filter(slug=slug).exists():
        raise ValidationError("That slug is already taken.")
    try:
        actor = create_local_actor(
            slug, actor_type=actor_type, name=title, summary=description
        )
    except IntegrityError as exc:
        raise ValidationError("That handle is already taken.") from exc
    comic = Comic.objects.create(
        actor=actor,
        title=title,
        slug=slug,
        description=description,
        content_rating=content_rating,
        default_audience=default_audience,
        default_federation=default_federation,
        update_schedule=update_schedule or {},
    )
    from comics.models import ComicRole

    ComicRole.objects.create(comic=comic, user=owner, role=ComicRole.Role.OWNER)
    if tags:
        comic.set_tags(tags)
    return comic
```

(`ComicRole` is defined in Task 2; import it locally so Task 1 can ship before Task 2 in strict order — but the tests here require the owner role, so implement Task 2's `ComicRole` before running Task 1's tests, or accept the ordering noted in the ledger.)

- [ ] **Step 4: Create migrations and run the tests**

Run: `uv run python manage.py makemigrations comics && uv run python manage.py migrate && uv run pytest tests/test_comics_models.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add comics/models.py comics/services.py comics/migrations tests/test_comics_models.py
git commit -m "feat: add Comic and Tag models with comic creation"
```

---

### Task 2: `ComicRole` and permission helpers

**Files:**
- Modify: `comics/models.py`; create `comics/migrations/0002_comicrole.py` (generated)
- Create: `comics/permissions.py`, `tests/test_comics_roles.py`

**Interfaces:**
- Consumes: `Comic` (Task 1), `settings.AUTH_USER_MODEL`.
- Produces: `ComicRole` (fields `comic`, `user`, `role`; `Role` choices), `comics.permissions.role_for/is_owner/can_manage_comic/can_edit/can_publish/can_moderate/can_contribute`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_comics_roles.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from comics.models import ComicRole
from comics.permissions import (
    can_contribute,
    can_edit,
    can_manage_comic,
    can_moderate,
    can_publish,
    is_owner,
    role_for,
)
from comics.services import create_comic


def _user(email):
    return get_user_model().objects.create_user(email=email, password="x")


@pytest.mark.django_db
def test_create_comic_makes_creator_owner():
    owner = _user("owner@example.com")
    comic = create_comic(owner, "panels", "Panels")
    assert is_owner(owner, comic) is True
    assert can_manage_comic(owner, comic) is True
    assert can_publish(owner, comic) is True
    assert can_moderate(owner, comic) is True


@pytest.mark.django_db
def test_only_one_owner_per_comic():
    owner = _user("owner@example.com")
    other = _user("other@example.com")
    comic = create_comic(owner, "panels", "Panels")
    with pytest.raises(IntegrityError), transaction.atomic():
        ComicRole.objects.create(comic=comic, user=other, role=ComicRole.Role.OWNER)


@pytest.mark.django_db
def test_user_has_one_role_per_comic():
    owner = _user("owner@example.com")
    editor = _user("editor@example.com")
    comic = create_comic(owner, "panels", "Panels")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    with pytest.raises(IntegrityError), transaction.atomic():
        ComicRole.objects.create(
            comic=comic, user=editor, role=ComicRole.Role.MODERATOR
        )


@pytest.mark.django_db
def test_role_capabilities():
    owner = _user("owner@example.com")
    editor = _user("editor@example.com")
    contributor = _user("contributor@example.com")
    moderator = _user("moderator@example.com")
    outsider = _user("outsider@example.com")
    comic = create_comic(owner, "panels", "Panels")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    ComicRole.objects.create(comic=comic, user=moderator, role=ComicRole.Role.MODERATOR)

    assert can_edit(editor, comic) is True
    assert can_publish(editor, comic) is True
    assert can_edit(contributor, comic) is False
    assert can_publish(contributor, comic) is False
    assert can_contribute(contributor, comic) is True
    assert can_moderate(moderator, comic) is True
    assert can_edit(moderator, comic) is False
    assert role_for(outsider, comic) is None
    assert can_contribute(outsider, comic) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_comics_roles.py -v`
Expected: FAIL/ERROR — `ComicRole` and `comics.permissions` do not exist.

- [ ] **Step 3: Implement `ComicRole` and permissions**

Append to `comics/models.py`:

```python
from django.conf import settings
from django.db.models import Q


class ComicRole(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        EDITOR = "editor", "Editor"
        CONTRIBUTOR = "contributor", "Contributor"
        MODERATOR = "moderator", "Moderator"

    comic = models.ForeignKey(Comic, on_delete=models.CASCADE, related_name="roles")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comic_roles"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "user"], name="unique_user_role_per_comic"
            ),
            models.UniqueConstraint(
                fields=["comic"],
                condition=Q(role="owner"),
                name="one_owner_per_comic",
            ),
        ]

    def __str__(self):
        return f"{self.user} is {self.role} of {self.comic}"
```

Create `comics/permissions.py`:

```python
from comics.models import ComicRole


def role_for(user, comic):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return (
        ComicRole.objects.filter(comic=comic, user=user)
        .values_list("role", flat=True)
        .first()
    )


def is_owner(user, comic) -> bool:
    return role_for(user, comic) == ComicRole.Role.OWNER


def can_manage_comic(user, comic) -> bool:
    return is_owner(user, comic)


def can_edit(user, comic) -> bool:
    return role_for(user, comic) in {ComicRole.Role.OWNER, ComicRole.Role.EDITOR}


def can_publish(user, comic) -> bool:
    return can_edit(user, comic)


def can_moderate(user, comic) -> bool:
    return role_for(user, comic) in {
        ComicRole.Role.OWNER,
        ComicRole.Role.EDITOR,
        ComicRole.Role.MODERATOR,
    }


def can_contribute(user, comic) -> bool:
    return role_for(user, comic) is not None
```

- [ ] **Step 4: Create migrations and run the tests**

Run: `uv run python manage.py makemigrations comics && uv run python manage.py migrate && uv run pytest tests/test_comics_roles.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add comics/models.py comics/permissions.py comics/migrations tests/test_comics_roles.py
git commit -m "feat: add ComicRole and permission helpers"
```

---

### Task 3: `Series`, `Chapter`, and `Page` models

**Files:**
- Modify: `comics/models.py`; create `comics/migrations/0003_series_chapter_page.py` (generated)
- Create: `tests/test_comics_pages.py`

**Interfaces:**
- Consumes: `Comic` (Task 1).
- Produces: `Series` (`comic`, `title`, `slug`, `position`; unique `(comic, slug)`), `Chapter` (`series`, `title`, `position`; unique `(series, position)`), `PageStatus`, `Page` (fields `series`, `chapter`, `position`, `title`, `alt_text`, `transcript`, `author_commentary`, `author`, `published_by`, `status`, `audience`, `federation`, `content_warning`, `sensitive`, `ap_id`, `scheduled_for`, `published_at`; unique `(series, position)`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_comics_pages.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from comics.models import Page, PageStatus, Series
from comics.services import create_comic
from federation.activitypub import Audience


@pytest.fixture
def comic():
    owner = get_user_model().objects.create_user(email="owner@example.com", password="x")
    return create_comic(owner, "panels", "Panels")


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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_comics_pages.py -v`
Expected: FAIL/ERROR — `Series`/`Page` do not exist.

- [ ] **Step 3: Implement the models**

Append to `comics/models.py`:

```python
class Series(UUIDModel):
    comic = models.ForeignKey(Comic, on_delete=models.CASCADE, related_name="series")
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    description = models.TextField(blank=True, default="")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "slug"], name="unique_series_slug_per_comic"
            ),
            models.UniqueConstraint(
                fields=["comic", "position"], name="unique_series_position"
            ),
        ]
        ordering = ["comic", "position", "id"]
        verbose_name_plural = "series"

    def __str__(self):
        return self.title


class Chapter(UUIDModel):
    series = models.ForeignKey(Series, on_delete=models.CASCADE, related_name="chapters")
    title = models.CharField(max_length=255)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["series", "position"], name="unique_chapter_position"
            )
        ]
        ordering = ["series", "position"]

    def __str__(self):
        return self.title


class PageStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SCHEDULED = "scheduled", "Scheduled"
    PUBLISHED = "published", "Published"


class Page(UUIDModel):
    series = models.ForeignKey(Series, on_delete=models.CASCADE, related_name="pages")
    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pages",
    )
    position = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255, blank=True, default="")
    alt_text = models.TextField(blank=True, default="")
    transcript = models.TextField(blank=True, default="")
    author_commentary = models.TextField(blank=True, default="")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authored_pages",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_pages",
    )
    status = models.CharField(
        max_length=16, choices=PageStatus.choices, default=PageStatus.DRAFT
    )
    audience = models.CharField(
        max_length=16, choices=Audience.choices, default=Audience.PUBLIC
    )
    federation = models.CharField(
        max_length=16,
        choices=FederationMode.choices,
        default=FederationMode.FEDERATED,
    )
    content_warning = models.CharField(max_length=255, blank=True, default="")
    sensitive = models.BooleanField(default=False)
    ap_id = models.URLField(blank=True, default="")
    scheduled_for = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["series", "position"], name="unique_page_position"
            )
        ]
        ordering = ["series", "position", "id"]

    def __str__(self):
        return f"{self.series} #{self.position}"
```

- [ ] **Step 4: Create migrations and run the tests**

Run: `uv run python manage.py makemigrations comics && uv run python manage.py migrate && uv run pytest tests/test_comics_pages.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add comics/models.py comics/migrations tests/test_comics_pages.py
git commit -m "feat: add Series, Chapter, and Page models"
```

---

### Task 4: Series/Page creation services with permissions and position

**Files:**
- Modify: `comics/services.py`
- Create: `tests/test_comics_services.py`

**Interfaces:**
- Consumes: `permissions` (Task 2), `Series`/`Page` (Task 3).
- Produces: `create_series(user, comic, title, *, slug=None, description="", position=None) -> Series`, `create_page(user, series, *, title="", chapter=None, audience=None, federation=None, **fields) -> Page`. Both raise `PermissionDenied` when the user lacks the role.

- [ ] **Step 1: Write the failing test**

Create `tests/test_comics_services.py`:

```python
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
    comic = create_comic(owner, "panels", "Panels")
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
    comic = create_comic(owner, "panels", "Panels")
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
        owner, "panels", "Panels", default_audience=Audience.UNLISTED
    )
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    assert page.audience == Audience.UNLISTED
    with pytest.raises(PermissionDenied):
        create_page(outsider, series)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_comics_services.py -v`
Expected: FAIL/ERROR — `create_series`/`create_page` do not exist.

- [ ] **Step 3: Implement the services**

Append to `comics/services.py`:

```python
from django.core.exceptions import PermissionDenied
from django.db.models import Max

from comics import permissions
from comics.models import Chapter, Page, PageStatus, Series


def _next_position(queryset):
    current = queryset.aggregate(Max("position"))["position__max"]
    return (current or 0) + 1


def create_series(user, comic, title, *, slug=None, description="", position=None):
    if not permissions.can_edit(user, comic):
        raise PermissionDenied("You cannot manage this comic's series.")
    series = Series(
        comic=comic,
        title=title,
        slug=slug or slugify(title),
        description=description,
        position=position if position is not None else _next_position(comic.series),
    )
    series.full_clean(exclude=["position"])
    series.save()
    return series


def create_page(
    user,
    series,
    *,
    title="",
    chapter=None,
    audience=None,
    federation=None,
    **fields,
):
    comic = series.comic
    if not permissions.can_contribute(user, comic):
        raise PermissionDenied("You cannot contribute to this comic.")
    page = Page(
        series=series,
        chapter=chapter,
        title=title,
        position=fields.pop("position", _next_position(series.pages)),
        audience=audience or comic.default_audience,
        federation=federation or comic.default_federation,
        author=user,
        status=PageStatus.DRAFT,
        **fields,
    )
    page.full_clean(exclude=["position", "chapter"])
    page.save()
    return page
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_comics_services.py -v`
Expected: PASS. If `full_clean(exclude=["position"])` also rejects the auto position/other auto fields, adjust the `exclude` list and record a ledger ruling.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green and no missing migrations.

- [ ] **Step 6: Commit**

```bash
git add comics/services.py tests/test_comics_services.py
git commit -m "feat: create series and pages with permissions and ordering"
```

---

## Self-Review

**Spec coverage (Ticket 4a scope):** `Comic` with auto AP actor, rating, structured schedule, default audience/federation, tags (Task 1); `ComicRole` with the one-owner invariant and permission helpers (Task 2); `Series`/`Chapter`/`Page` with ordering and the Page fields except Media (Task 3); creation services enforcing roles and defaults (Task 4). Publishing/scheduling, invites, media, page→Note serialization, and federation emission are 4b/5/6.

**Placeholder scan:** no TBD/TODO steps; every code step carries full content. The `actors.crypto` import in the Task 1 service is unnecessary and must be dropped when writing.

**Type consistency:** `ComicRole.Role` is the single role vocabulary; `permissions.can_*` are the single authorization path; `_next_position` is the single position allocator; `audience`/`federation` reuse `Audience`/`FederationMode`.

**Known deviations recorded as ledger rulings during execution:** (1) Media FK and the alt-text publish gate are deferred to Ticket 5; (2) `Page` ordering is series-global with chapters as grouping labels; (3) `full_clean(exclude=...)` may need widening for auto-managed fields.