# Ticket 4b — Publishing & Scheduling Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give comics a local publish/scheduling state machine: draft → scheduled → published (and back), with alt-text/content-warning gates, a delayed auto-publish task, and a cron sweep safety net.

**Architecture:** A new `comics/publishing.py` owns all page state transitions behind one locked internal `publish()` plus public permission-checked wrappers. Scheduling stores `scheduled_for`/`scheduled_by`, enqueues `comics.tasks.publish_scheduled_page` with `run_after`, and a `publish_due_pages` management command re-publishes anything the task missed. No ActivityPub emission here (Ticket 6 wires `Create`/`Update`/`Delete` into `publish()`); no image `Media` (Ticket 5); collaboration/invites are 4c.

**Tech Stack:** Django 6.1.1, PostgreSQL, `django.tasks` (`django_tasks_db.DatabaseBackend`), pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §5 (visibility/federation axes — emission deferred), §6 (creator authoring + scheduling), §8 (content warning/sensitive), §14 ticket 4.

## Global Constraints

- Synchronous Django only. Background work uses `django.tasks`: decorate with `@task` and call `.enqueue()` (optionally `.using(run_after=<aware datetime>)`). Do not introduce Celery/async.
- Run all Python via `uv run` (Python 3.14).
- Permissions are centralized in `comics/permissions.py`; never inline role strings. `can_publish` is true for owner/editor only; contributors may author drafts but not publish.
- Entity models inherit `core.UUIDModel`; `ComicRole` is the only plain integer-pk model in `comics`.
- `INSTANCE_URL` (settings) has no trailing slash; object ids are `f"{INSTANCE_URL}/pages/{page.id}"`.
- No outbound ActivityPub activity is emitted in this ticket; `publish()` is the single seam Ticket 6 will extend.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Worker/DB for dev: `docker compose up -d db`; tests use the same Postgres.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- The auto-publish task firing **early** (before `scheduled_for`) must not publish — it re-enqueues. Task 2.
- The auto-publish task firing after an **unschedule** must be a no-op. Task 2.
- Publishing twice (double click / retry) must not reset `published_at` or re-issue `ap_id`. Task 1.
- A page with empty `alt_text`, or `sensitive=True` with no `content_warning`, must not publish or schedule. Tasks 1, 2.
- Scheduled pages must credit the **scheduling user** as `published_by` (the worker has no request user). Task 2.
- The sweep command must be idempotent and must **skip** a due page that fails its gates rather than aborting the whole run. Task 3.

## File Structure

- `comics/models.py` — add `Page.scheduled_by` FK and a `(status, scheduled_for)` index. (Task 2)
- `comics/publishing.py` — new: `publish`, `publish_page`, `unpublish_page`, `schedule_page`, `unschedule_page`. (Tasks 1, 2)
- `comics/tasks.py` — new: `publish_scheduled_page`. (Task 2)
- `comics/management/commands/publish_due_pages.py` — new: cron sweep. (Task 3)
- Tests: `tests/test_comics_publishing.py` (Task 1), `tests/test_comics_scheduling.py` (Task 2), `tests/test_comics_sweep.py` (Task 3).

---

### Task 1: Publish / unpublish state machine and gates

**Files:**
- Create: `comics/publishing.py`
- Test: `tests/test_comics_publishing.py`

**Interfaces:**
- Consumes: `comics.permissions.can_publish`, `comics.models.{Page, PageStatus}`, `settings.INSTANCE_URL`.
- Produces:
  - `publish(page, *, published_by=None, when=None) -> Page` — locked, idempotent, gate-enforcing transition to `PUBLISHED`.
  - `publish_page(user, page, *, when=None) -> Page` — permission-checked public wrapper.
  - `unpublish_page(user, page) -> Page` — permission-checked return to `DRAFT`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_comics_publishing.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from comics.models import ComicRole, PageStatus
from comics.publishing import publish_page, unpublish_page
from comics.services import create_comic, create_page, create_series


def _user(email="owner@example.com"):
    return get_user_model().objects.create_user(email=email, password="x")


def _page(owner, **page_fields):
    comic = create_comic(owner, "panels", "Panels")
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
    publish_page(editor, page)
    assert page.status == PageStatus.PUBLISHED


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
    comic = create_comic(owner, "panels", "Panels")
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_comics_publishing.py -q`
Expected: ERROR — `comics.publishing` does not exist.

- [ ] **Step 3: Implement `comics/publishing.py`**

```python
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from comics import permissions
from comics.models import Page, PageStatus


def _object_id(page) -> str:
    return f"{settings.INSTANCE_URL}/pages/{page.id}"


def _enforce_publish_gates(page) -> None:
    if not page.alt_text.strip():
        raise ValidationError({"alt_text": "Alt text is required before publishing."})
    if page.sensitive and not page.content_warning.strip():
        raise ValidationError(
            {"content_warning": "A content warning is required for sensitive pages."}
        )


@transaction.atomic
def publish(page, *, published_by=None, when=None):
    """Lock and transition a page to PUBLISHED. Idempotent; enforces gates."""
    locked = Page.objects.select_for_update().get(pk=page.pk)
    if locked.status == PageStatus.PUBLISHED:
        return locked
    _enforce_publish_gates(locked)
    locked.status = PageStatus.PUBLISHED
    locked.published_at = when or timezone.now()
    locked.published_by = published_by
    locked.scheduled_for = None
    if not locked.ap_id:
        locked.ap_id = _object_id(locked)
    locked.save(
        update_fields=[
            "status",
            "published_at",
            "published_by",
            "scheduled_for",
            "ap_id",
            "updated_at",
        ]
    )
    return locked


def publish_page(user, page, *, when=None):
    if not permissions.can_publish(user, page.series.comic):
        raise PermissionDenied("You cannot publish this page.")
    return publish(page, published_by=user, when=when)


def unpublish_page(user, page):
    if not permissions.can_publish(user, page.series.comic):
        raise PermissionDenied("You cannot unpublish this page.")
    page.status = PageStatus.DRAFT
    page.published_at = None
    page.scheduled_for = None
    page.save(
        update_fields=["status", "published_at", "scheduled_for", "updated_at"]
    )
    return page
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_comics_publishing.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add comics/publishing.py tests/test_comics_publishing.py
git commit -m "feat: publish/unpublish page state machine with gates"
```

---

### Task 2: Scheduling, `scheduled_by`, and the auto-publish task

**Files:**
- Modify: `comics/models.py` (add `Page.scheduled_by` and `Meta.indexes`)
- Modify: `comics/publishing.py` (add `schedule_page`, `unschedule_page`)
- Create: `comics/tasks.py`
- Create: `comics/migrations/0002_page_scheduled_by_page_due_idx.py` (generated)
- Test: `tests/test_comics_scheduling.py`

**Interfaces:**
- Consumes: Task 1's `publish`; `comics.permissions.can_publish`; `django.tasks.task`.
- Produces:
  - `Page.scheduled_by` (FK to `AUTH_USER_MODEL`, null, `SET_NULL`, related_name `scheduled_pages`).
  - `schedule_page(user, page, when) -> Page`, `unschedule_page(user, page) -> Page`.
  - `comics.tasks.publish_scheduled_page(page_id: str) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_comics_scheduling.py`:

```python
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from comics.models import ComicRole, Page, PageStatus
from comics.publishing import schedule_page, unschedule_page
from comics.services import create_comic, create_page, create_series
from comics.tasks import publish_scheduled_page


def _user(email="owner@example.com"):
    return get_user_model().objects.create_user(email=email, password="x")


def _page(owner):
    comic = create_comic(owner, "panels", "Panels")
    series = create_series(owner, comic, "Main Story")
    return comic, create_page(owner, series, alt_text="A panel")


@pytest.mark.django_db
def test_schedule_page_sets_state_and_enqueues_delayed_task():
    owner = _user()
    _, page = _page(owner)
    when = timezone.now() + timedelta(hours=2)
    scheduled = schedule_page(owner, page, when)
    assert scheduled.status == PageStatus.SCHEDULED
    assert scheduled.scheduled_for == when
    assert scheduled.scheduled_by == owner
    task = DBTaskResult.objects.get()
    assert task.run_after == when


@pytest.mark.django_db
def test_schedule_rejects_naive_and_past_times():
    owner = _user()
    _, page = _page(owner)
    with pytest.raises(ValidationError) as naive:
        schedule_page(owner, page, timezone.now().replace(tzinfo=None))
    assert "scheduled_for" in naive.value.message_dict
    with pytest.raises(ValidationError) as past:
        schedule_page(owner, page, timezone.now() - timedelta(minutes=1))
    assert "scheduled_for" in past.value.message_dict


@pytest.mark.django_db
def test_schedule_requires_publish_permission_and_gates():
    owner = _user()
    contributor = _user("contributor@example.com")
    comic, page = _page(owner)
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    when = timezone.now() + timedelta(hours=1)
    with pytest.raises(PermissionDenied):
        schedule_page(contributor, page, when)
    blank = create_page(owner, page.series)
    with pytest.raises(ValidationError):
        schedule_page(owner, blank, when)


@pytest.mark.django_db
def test_publish_scheduled_page_publishes_due_page_with_scheduler_credit():
    owner = _user()
    _, page = _page(owner)
    schedule_page(owner, page, timezone.now() + timedelta(hours=1))
    Page.objects.filter(pk=page.pk).update(
        scheduled_for=timezone.now() - timedelta(minutes=1)
    )
    publish_scheduled_page(str(page.id))
    page.refresh_from_db()
    assert page.status == PageStatus.PUBLISHED
    assert page.published_by == owner
    assert page.scheduled_for is None


@pytest.mark.django_db
def test_publish_scheduled_page_ignores_non_scheduled_pages():
    owner = _user()
    _, page = _page(owner)
    publish_scheduled_page(str(page.id))
    page.refresh_from_db()
    assert page.status == PageStatus.DRAFT


@pytest.mark.django_db
def test_publish_scheduled_page_requeues_when_early():
    owner = _user()
    _, page = _page(owner)
    schedule_page(owner, page, timezone.now() + timedelta(hours=3))
    publish_scheduled_page(str(page.id))
    page.refresh_from_db()
    assert page.status == PageStatus.SCHEDULED


@pytest.mark.django_db
def test_unschedule_returns_to_draft():
    owner = _user()
    _, page = _page(owner)
    schedule_page(owner, page, timezone.now() + timedelta(hours=1))
    unscheduled = unschedule_page(owner, page)
    assert unscheduled.status == PageStatus.DRAFT
    assert unscheduled.scheduled_for is None
    assert unscheduled.scheduled_by is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_comics_scheduling.py -q`
Expected: FAIL/ERROR — `Page` has no `scheduled_by`, `comics.tasks` does not exist.

- [ ] **Step 3: Add `scheduled_by` and the sweep index to `Page`**

In `comics/models.py`, add the field to `Page` immediately after `published_by`:

```python
    scheduled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_pages",
    )
```

Replace the `Page.Meta` block (currently only `constraints` + `ordering`) with:

```python
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["series", "position"], name="unique_page_position"
            )
        ]
        indexes = [
            models.Index(fields=["status", "scheduled_for"], name="page_due_idx")
        ]
        ordering = ["series", "position", "id"]
```

- [ ] **Step 4: Add scheduling functions to `comics/publishing.py`**

Append to `comics/publishing.py`:

```python
def schedule_page(user, page, when):
    if not permissions.can_publish(user, page.series.comic):
        raise PermissionDenied("You cannot schedule this page.")
    if timezone.is_naive(when):
        raise ValidationError(
            {"scheduled_for": "A timezone-aware datetime is required."}
        )
    if when <= timezone.now():
        raise ValidationError(
            {"scheduled_for": "The scheduled time must be in the future."}
        )
    _enforce_publish_gates(page)
    from comics.tasks import publish_scheduled_page

    page.status = PageStatus.SCHEDULED
    page.scheduled_for = when
    page.scheduled_by = user
    page.save(update_fields=["status", "scheduled_for", "scheduled_by", "updated_at"])
    publish_scheduled_page.using(run_after=when).enqueue(str(page.id))
    return page


def unschedule_page(user, page):
    if not permissions.can_publish(user, page.series.comic):
        raise PermissionDenied("You cannot unschedule this page.")
    page.status = PageStatus.DRAFT
    page.scheduled_for = None
    page.scheduled_by = None
    page.save(update_fields=["status", "scheduled_for", "scheduled_by", "updated_at"])
    return page
```

- [ ] **Step 5: Create `comics/tasks.py`**

```python
from django.tasks import task
from django.utils import timezone

from comics import publishing
from comics.models import Page, PageStatus


@task
def publish_scheduled_page(page_id):
    page = Page.objects.filter(pk=page_id).first()
    if page is None or page.status != PageStatus.SCHEDULED:
        return
    if page.scheduled_for and page.scheduled_for > timezone.now():
        publish_scheduled_page.using(run_after=page.scheduled_for).enqueue(page_id)
        return
    publishing.publish(
        page, published_by=page.scheduled_by, when=page.scheduled_for
    )
```

- [ ] **Step 6: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations comics
uv run python manage.py migrate
uv run pytest tests/test_comics_scheduling.py -q
```
Expected: PASS (7 passed). Migration adds `scheduled_by` and index `page_due_idx`.

- [ ] **Step 7: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green, no missing migrations.

- [ ] **Step 8: Commit**

```bash
git add comics/models.py comics/publishing.py comics/tasks.py comics/migrations tests/test_comics_scheduling.py
git commit -m "feat: schedule page publishing with delayed auto-publish task"
```

---

### Task 3: Due-page sweep management command

**Files:**
- Create: `comics/management/__init__.py`, `comics/management/commands/__init__.py`
- Create: `comics/management/commands/publish_due_pages.py`
- Test: `tests/test_comics_sweep.py`

**Interfaces:**
- Consumes: Task 1's `publish`, Task 2's `Page.scheduled_by`; `django.core.management.call_command`.
- Produces: `publish_due_pages` command — publishes `SCHEDULED` pages whose `scheduled_for <= now`, skipping any that fail gates.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_comics_sweep.py`:

```python
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from comics.models import Page, PageStatus
from comics.publishing import schedule_page
from comics.services import create_comic, create_page, create_series


def _user(email="owner@example.com"):
    return get_user_model().objects.create_user(email=email, password="x")


def _due_page(owner, series, **fields):
    page = create_page(owner, series, **fields)
    page.status = PageStatus.SCHEDULED
    page.scheduled_for = timezone.now() - timedelta(minutes=5)
    page.scheduled_by = owner
    page.save()
    return page


@pytest.mark.django_db
def test_sweep_publishes_only_due_pages():
    owner = _user()
    comic = create_comic(owner, "panels", "Panels")
    series = create_series(owner, comic, "Main Story")
    due = _due_page(owner, series, alt_text="A panel")
    future = create_page(owner, series, alt_text="Another panel")
    schedule_page(owner, future, timezone.now() + timedelta(hours=1))

    call_command("publish_due_pages")

    due.refresh_from_db()
    future.refresh_from_db()
    assert due.status == PageStatus.PUBLISHED
    assert due.published_by == owner
    assert future.status == PageStatus.SCHEDULED


@pytest.mark.django_db
def test_sweep_skips_pages_that_fail_gates():
    owner = _user()
    comic = create_comic(owner, "panels", "Panels")
    series = create_series(owner, comic, "Main Story")
    broken = _due_page(owner, series)  # no alt text
    good = _due_page(owner, series, alt_text="A panel")

    call_command("publish_due_pages")

    broken.refresh_from_db()
    good.refresh_from_db()
    assert broken.status == PageStatus.SCHEDULED
    assert good.status == PageStatus.PUBLISHED


@pytest.mark.django_db
def test_sweep_is_idempotent():
    owner = _user()
    comic = create_comic(owner, "panels", "Panels")
    series = create_series(owner, comic, "Main Story")
    _due_page(owner, series, alt_text="A panel")

    call_command("publish_due_pages")
    first_stamp = Page.objects.get().published_at
    call_command("publish_due_pages")

    page = Page.objects.get()
    assert page.status == PageStatus.PUBLISHED
    assert page.published_at == first_stamp
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_comics_sweep.py -q`
Expected: ERROR — unknown command `publish_due_pages`.

- [ ] **Step 3: Create the package `__init__.py` files**

Create empty `comics/management/__init__.py` and `comics/management/commands/__init__.py`.

- [ ] **Step 4: Implement the command**

Create `comics/management/commands/publish_due_pages.py`:

```python
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.utils import timezone

from comics import publishing
from comics.models import Page, PageStatus


class Command(BaseCommand):
    help = "Publish any scheduled pages whose scheduled_for time has passed."

    def handle(self, *args, **options):
        now = timezone.now()
        due = (
            Page.objects.filter(status=PageStatus.SCHEDULED, scheduled_for__lte=now)
            .order_by("scheduled_for", "id")
        )
        published = skipped = 0
        for page in due:
            try:
                publishing.publish(
                    page, published_by=page.scheduled_by, when=page.scheduled_for
                )
                published += 1
            except ValidationError as exc:
                skipped += 1
                self.stderr.write(f"Skipped page {page.id}: {exc}")
        self.stdout.write(
            self.style.SUCCESS(f"Published {published} page(s), skipped {skipped}.")
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_comics_sweep.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add comics/management tests/test_comics_sweep.py
git commit -m "feat: add publish_due_pages sweep command"
```

---

## Self-Review

**Spec coverage (Ticket 4b scope):** publish + schedule engine (§14 ticket 4 / 4a's deferred 4b): state machine (Task 1), scheduling with a delayed auto-publish task + `scheduled_by` audit credit (Task 2), and a cron sweep for missed runs (Task 3). Alt-text-required and CW-before-publish gates per §6/§8 (Tasks 1, 2). Out of scope and intentionally omitted: AP `Create`/`Update`/`Delete` emission and `federation_plan` gating (§5/§7 → Ticket 6), image `Media` + per-image alt (Ticket 5), collaboration/invites (4c), notifications (Ticket 7).

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `PageStatus` is the single status vocabulary; `can_publish` is the single publish authorization; `publishing.publish()` is the single transition used by services, the task, and the command; `scheduled_by` is the single scheduler-credit field read by both the task and the command.

**Review Focus coverage:** early-fire/unscheduled no-op — Task 2; double-publish idempotency — Task 1; gates — Tasks 1, 2; scheduler credit — Task 2; sweep idempotency + skip-not-abort — Task 3.
