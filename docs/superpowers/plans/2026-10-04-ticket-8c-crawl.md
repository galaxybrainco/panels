# Ticket 8c — Reply-Tree Crawl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Best-effort, rate-limited completion of comment threads: fetch unseen reply Notes and backfill missing ancestors, storing them as comments.

**Architecture:** `social/comments.py` exposes a shared `store_inbound_reply(note, actor)` (the inbound `Create` handler's logic). `social/crawl.py` dereferences Notes via `federation.client.fetch_json` (per-domain throttle), resolves authors via `federation.remotes.fetch_remote_actor`, walks `replies` collections and `inReplyTo` chains under a depth/size budget, and records `Comment.crawled_at`. `social/tasks.py` runs a crawl; the inbound handler enqueues it; a `crawl_comment_threads` management command re-crawls stale threads.

**Tech Stack:** Django 6.1.1, PostgreSQL, `django.tasks` (`django_tasks_db`), existing `federation` primitives, pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §5 (comments = inbound replies; incomplete-thread caveat; crawl the replies collection and walk `inReplyTo` chains — background, rate-limited, cached), §13 (rate-limit crawling), §14 ticket 8.

## Global Constraints

- Synchronous Django only; background work via `django.tasks`. Run Python via `uv run` (Python 3.14).
- All fetched Notes go through `store_inbound_reply`, so gating (published + `federation_plan.emit`), bans, sanitization, and idempotency by `ap_id` are shared with the inbound path.
- Crawls are **best-effort**: depth ≤ 3, at most 20 fetched notes per crawl, and a 30/min per-domain throttle; any failure returns quietly.
- Author resolution uses `federation.remotes.fetch_remote_actor` (itself rate-limited); a reply from an unresolvable author is skipped.
- Network is never hit in tests: `social.crawl.fetch_object` and `social.crawl.resolve_author` are monkeypatched.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- A crawled reply must be dropped when its author is banned or unresolvable. Tasks 1, 2.
- A crawled reply to an unknown/foreign `inReplyTo` must store nothing. Task 1.
- The crawl must not exceed the depth/size budget (no unbounded recursion). Task 2.
- Re-crawling must not duplicate already-stored comments. Tasks 1, 2.
- The inbound handler must still store replies and now enqueue a crawl. Task 3.

## File Structure

- `social/models.py` — add `Comment.crawled_at`. (Task 1)
- `social/migrations/0007_comment_crawled_at.py` — generated. (Task 1)
- `social/comments.py` — add `federatable_page`, `reply_target`, `store_inbound_reply`. (Task 1)
- `social/handlers.py` — use the shared helpers. (Tasks 1, 3)
- `social/crawl.py` — new. (Task 2)
- `social/tasks.py` — new: `crawl_comment_thread`. (Task 3)
- `social/management/commands/crawl_comment_threads.py` — new. (Task 3)
- Tests: `tests/test_social_crawl.py` (new).

---

### Task 1: Shared inbound-reply storage

**Files:**
- Modify: `social/models.py`, `social/comments.py`, `social/handlers.py`
- Create: `social/migrations/0007_comment_crawled_at.py` (generated)
- Test: `tests/test_social_crawl.py`

**Interfaces:**
- Consumes: `comics.federation.federation_plan`, `comics.models.{Page, PageStatus}`, `social.moderation.is_banned`, `social.sanitize.sanitize_html`, `social.models.{Comment, CommentStatus}`.
- Produces:
  - `Comment.crawled_at`.
  - `social.comments.{federatable_page, reply_target, store_inbound_reply}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_social_crawl.py`:

```python
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from social.comments import store_inbound_reply
from social.models import Comment
from tests.media_support import make_media


def remote(handle="bob"):
    domain = f"{handle}.test"
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
    )


def scene():
    suffix = uuid4().hex[:8]
    owner = get_user_model().objects.create_user(
        email=f"owner-{suffix}@example.com", password="x"
    )
    comic = create_comic(owner, f"comic{suffix}", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)
    page.refresh_from_db()
    return owner, comic, page


def _note(ap_id, author, in_reply_to, content="<p>hi</p>"):
    return {
        "id": ap_id,
        "type": "Note",
        "attributedTo": author.ap_id,
        "inReplyTo": in_reply_to,
        "content": content,
    }


@pytest.mark.django_db
def test_store_inbound_reply_stores_reply_to_page():
    _, _, page = scene()
    bob = remote("bob")
    comment = store_inbound_reply(
        _note("https://bob.test/notes/1", bob, page.ap_id), bob
    )
    assert comment.page == page
    assert comment.parent is None
    assert comment.content == "<p>hi</p>"
    assert comment.activity_id == ""


@pytest.mark.django_db
def test_store_inbound_reply_is_idempotent():
    _, _, page = scene()
    bob = remote("bob")
    note = _note("https://bob.test/notes/1", bob, page.ap_id)
    store_inbound_reply(note, bob)
    assert store_inbound_reply(note, bob) is None
    assert Comment.objects.count() == 1


@pytest.mark.django_db
def test_store_inbound_reply_rejects_foreign_in_reply_to():
    bob = remote("bob")
    note = _note("https://bob.test/notes/1", bob, "https://elsewhere.test/notes/x")
    assert store_inbound_reply(note, bob) is None


@pytest.mark.django_db
def test_store_inbound_reply_rejects_local_note_id():
    _, _, page = scene()
    bob = remote("bob")
    note = _note("http://testserver/comments/spoof", bob, page.ap_id)
    assert store_inbound_reply(note, bob) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_crawl.py -q`
Expected: ERROR — `store_inbound_reply` does not exist.

- [ ] **Step 3: Add `Comment.crawled_at`**

In `social/models.py`, add to `Comment` (after `activity_id`):

```python
    crawled_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 4: Add the shared helpers to `social/comments.py`**

Add imports (extend the existing block):

```python
from comics.federation import federation_plan
from comics.models import Page, PageStatus
from social.moderation import is_banned
```

Append:

```python
def federatable_page(object_id):
    page = Page.objects.filter(ap_id=object_id, status=PageStatus.PUBLISHED).first()
    if page is None or not federation_plan(page).emit:
        return None
    return page


def reply_target(value):
    if isinstance(value, dict):
        return value.get("id")
    return value


def store_inbound_reply(note, actor):
    if not isinstance(note, dict) or note.get("type") != "Note":
        return None
    ap_id = note.get("id")
    in_reply_to = reply_target(note.get("inReplyTo"))
    if not ap_id or not in_reply_to:
        return None
    if ap_id.startswith(settings.INSTANCE_URL):
        return None
    if Comment.objects.filter(ap_id=ap_id).exists():
        return None
    parent = None
    page = federatable_page(in_reply_to)
    if page is None:
        parent = Comment.objects.filter(ap_id=in_reply_to).first()
        if parent is None:
            return None
        page = parent.page
        if page.status != PageStatus.PUBLISHED or not federation_plan(page).emit:
            return None
    if is_banned(page.series.comic, actor):
        return None
    return Comment.objects.create(
        actor=actor,
        page=page,
        parent=parent,
        ap_id=ap_id,
        in_reply_to=in_reply_to,
        content=sanitize_html(note.get("content", "")),
        status=comment_status_for(page),
    )
```

- [ ] **Step 5: Point the handlers at the shared helpers**

In `social/handlers.py`, delete the local `_federatable_page` and `_object_iri`, and use the shared ones. Replace the import block's `from social.comments import comment_status_for` with:

```python
from social.comments import (
    comment_status_for,
    federatable_page,
    reply_target,
    store_inbound_reply,
)
```

Update `_resolve_reaction_target` to call `federatable_page`. Replace `handle_create` with:

```python
@handlers.register("Create")
def handle_create(activity):
    from social.tasks import crawl_comment_thread

    comment = store_inbound_reply(activity.payload.get("object"), activity.actor)
    if comment is not None:
        crawl_comment_thread.enqueue(str(comment.id))
```

Where `handle_undo`/`handle_like`/`handle_announce` used `_object_iri(...)`, call `reply_target(...)` instead.

- [ ] **Step 6: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations social
uv run python manage.py migrate
uv run pytest tests/test_social_crawl.py -q
```
Expected: PASS (4 passed).

- [ ] **Step 7: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green (the refactor must not change comment behavior).

- [ ] **Step 8: Commit**

```bash
git add social/models.py social/comments.py social/handlers.py social/migrations tests/test_social_crawl.py
git commit -m "refactor: share inbound-reply storage and add Comment.crawled_at"
```

---

### Task 2: The crawl

**Files:**
- Create: `social/crawl.py`
- Test: `tests/test_social_crawl.py`

**Interfaces:**
- Consumes: `federation.client.fetch_json`, `federation.remotes.fetch_remote_actor`, Task 1 `store_inbound_reply`/`federatable_page`/`reply_target`, `actors.models.Actor`.
- Produces: `social.crawl.{fetch_object, resolve_author, store_reply, crawl_comment}`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_crawl.py` (add to imports: `from social import crawl`):

```python
@pytest.mark.django_db
def test_store_reply_resolves_author_and_stores(monkeypatch):
    _, _, page = scene()
    bob = remote("bob")
    note = _note("https://bob.test/notes/1", bob, page.ap_id)
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: note if url == note["id"] else None)
    comment = crawl.store_reply(note)
    assert comment is not None
    assert comment.actor == bob


@pytest.mark.django_db
def test_store_reply_drops_banned_author():
    owner, comic, page = scene()
    from social.moderation import ban_commenter

    bob = remote("bob")
    ban_commenter(owner, comic, bob)
    note = _note("https://bob.test/notes/1", bob, page.ap_id)
    assert crawl.store_reply(note) is None


@pytest.mark.django_db
def test_store_reply_drops_unresolvable_author(monkeypatch):
    _, _, page = scene()
    unknown = remote("ghost")
    note = _note("https://bob.test/notes/1", unknown, page.ap_id)
    monkeypatch.setattr("social.crawl.resolve_author", lambda note: None)
    assert crawl.store_reply(note) is None


@pytest.mark.django_db
def test_crawl_comment_stores_descendants(monkeypatch):
    _, _, page = scene()
    bob, carol = remote("bob"), remote("carol")
    parent = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>top</p>",
    )
    child = _note("https://carol.test/notes/2", carol, parent.ap_id)
    parent_note = {
        "id": parent.ap_id,
        "type": "Note",
        "attributedTo": bob.ap_id,
        "inReplyTo": page.ap_id,
        "content": "<p>top</p>",
        "replies": {"type": "Collection", "orderedItems": [child]},
    }
    documents = {parent.ap_id: parent_note, child["id"]: child}
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: documents.get(url))

    stored = crawl.crawl_comment(parent)

    assert stored == 1
    assert Comment.objects.filter(ap_id=child["id"], parent=parent).exists()
    parent.refresh_from_db()
    assert parent.crawled_at is not None


@pytest.mark.django_db
def test_crawl_comment_backfills_missing_ancestor(monkeypatch):
    _, _, page = scene()
    bob, carol = remote("bob"), remote("carol")
    # A stored reply whose parent (carol's note) we never received.
    stored_reply = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to="https://carol.test/notes/0",
        content="<p>reply</p>",
    )
    ancestor = _note("https://carol.test/notes/0", carol, page.ap_id)
    documents = {
        stored_reply.ap_id: {
            "id": stored_reply.ap_id,
            "type": "Note",
            "attributedTo": bob.ap_id,
            "inReplyTo": ancestor["id"],
            "content": "<p>reply</p>",
        },
        ancestor["id"]: ancestor,
    }
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: documents.get(url))

    crawl.crawl_comment(stored_reply)

    assert Comment.objects.filter(ap_id=ancestor["id"], page=page).exists()


@pytest.mark.django_db
def test_crawl_comment_respects_depth(monkeypatch):
    _, _, page = scene()
    bob = remote("bob")
    parent = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>top</p>",
    )
    documents = {}
    chain = [parent.ap_id]
    for index in range(4):
        ap_id = f"https://bob.test/notes/child-{index}"
        in_reply_to = chain[-1]
        author = remote(f"author{index}")
        documents[in_reply_to] = {
            "id": in_reply_to,
            "type": "Note",
            "attributedTo": bob.ap_id,
            "inReplyTo": page.ap_id if in_reply_to == parent.ap_id else "x",
            "content": "<p>x</p>",
            "replies": {
                "type": "Collection",
                "orderedItems": [_note(ap_id, author, in_reply_to)],
            },
        }
        chain.append(ap_id)
        documents[ap_id] = {
            "id": ap_id,
            "type": "Note",
            "attributedTo": author.ap_id,
            "inReplyTo": in_reply_to,
            "content": "<p>x</p>",
        }
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: documents.get(url))

    crawl.crawl_comment(parent)

    # depth 3 -> child-0, child-1 stored; child-2 is one level too deep.
    assert Comment.objects.filter(ap_id="https://bob.test/notes/child-1").exists()
    assert not Comment.objects.filter(ap_id="https://bob.test/notes/child-2").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_crawl.py -q -k "crawl or store_reply"`
Expected: ERROR — `social.crawl` does not exist.

- [ ] **Step 3: Implement `social/crawl.py`**

```python
from urllib.parse import urlparse

from django.core.cache import cache
from django.utils import timezone

from actors.models import Actor
from federation import client, remotes
from social.comments import federatable_page, reply_target, store_inbound_reply
from social.models import Comment

MAX_DEPTH = 3
MAX_NOTES = 20
CRAWL_LIMIT = 30
CRAWL_WINDOW = 60


def _throttle_allows(url) -> bool:
    domain = urlparse(url).netloc
    key = f"social:crawl:{domain}"
    if cache.add(key, 1, timeout=CRAWL_WINDOW):
        return True
    try:
        return cache.incr(key) <= CRAWL_LIMIT
    except ValueError:
        cache.set(key, 1, timeout=CRAWL_WINDOW)
        return True


def fetch_object(url):
    if not url or not isinstance(url, str) or not _throttle_allows(url):
        return None
    return client.fetch_json(url)


def resolve_author(note):
    attributed = note.get("attributedTo")
    if isinstance(attributed, dict):
        attributed = attributed.get("id")
    if not attributed:
        return None
    actor = Actor.objects.filter(ap_id=attributed).first()
    if actor is not None:
        return actor
    return remotes.fetch_remote_actor(attributed)


def store_reply(note):
    actor = resolve_author(note) if isinstance(note, dict) else None
    if actor is None:
        return None
    return store_inbound_reply(note, actor)


def _reply_items(note):
    replies = note.get("replies")
    if isinstance(replies, str):
        replies = fetch_object(replies)
    if not isinstance(replies, dict):
        return []
    items = replies.get("orderedItems") or replies.get("items") or []
    return list(items)


class _Budget:
    def __init__(self, limit=MAX_NOTES):
        self.remaining = limit

    def take(self):
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _crawl_descendants(comment, budget, depth):
    stored = 0
    note = fetch_object(comment.ap_id)
    if not isinstance(note, dict):
        return stored
    for item in _reply_items(note):
        if not budget.take():
            break
        child_note = item if isinstance(item, dict) else fetch_object(item)
        if not isinstance(child_note, dict):
            continue
        child = store_reply(child_note)
        if child is None:
            continue
        stored += 1
        if depth + 1 < MAX_DEPTH:
            stored += _crawl_descendants(child, budget, depth + 1)
    return stored


def _backfill_ancestors(comment, budget):
    stored = 0
    target = comment.in_reply_to
    for _ in range(MAX_DEPTH):
        if not target or Comment.objects.filter(ap_id=target).exists():
            break
        if federatable_page(target) is not None:
            break
        if not budget.take():
            break
        note = fetch_object(target)
        if not isinstance(note, dict):
            break
        ancestor = store_reply(note)
        if ancestor is None:
            break
        stored += 1
        target = reply_target(note.get("inReplyTo"))
    return stored


def crawl_comment(comment):
    budget = _Budget()
    stored = _backfill_ancestors(comment, budget)
    stored += _crawl_descendants(comment, budget, depth=1)
    Comment.objects.filter(pk=comment.pk).update(crawled_at=timezone.now())
    return stored
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_crawl.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add social/crawl.py tests/test_social_crawl.py
git commit -m "feat: best-effort reply-tree crawl"
```

---

### Task 3: Crawl task, trigger, and sweep command

**Files:**
- Create: `social/tasks.py`, `social/management/__init__.py`, `social/management/commands/__init__.py`, `social/management/commands/crawl_comment_threads.py`
- Modify: `social/handlers.py` (already enqueues from Task 1)
- Test: `tests/test_social_crawl.py`

**Interfaces:**
- Consumes: Task 2 `crawl_comment`; `django.tasks.task`.
- Produces: `social.tasks.crawl_comment_thread(comment_id)`; the `crawl_comment_threads` command.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_crawl.py` (add to imports: `from django.core.management import call_command`, `from django_tasks_db.models import DBTaskResult`, `from federation import handlers`, `from federation.models import Activity, ActivityDirection`, `from social.models import Comment` [already] and `from social.tasks import crawl_comment_thread`):

```python
@pytest.mark.django_db
def test_inbound_create_enqueues_a_crawl():
    _, _, page = scene()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": _note("https://bob.test/notes/1", bob, page.ap_id),
    }
    activity = Activity.objects.create(
        ap_id=payload["id"],
        type="Create",
        actor=bob,
        direction=ActivityDirection.INBOUND,
        payload=payload,
    )
    handlers.dispatch(activity)
    assert Comment.objects.count() == 1
    assert DBTaskResult.objects.filter(
        task_path__contains="crawl_comment_thread"
    ).exists()


@pytest.mark.django_db
def test_crawl_command_enqueues_stale_comments(monkeypatch):
    _, _, page = scene()
    bob = remote("bob")
    Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>x</p>",
    )
    call_command("crawl_comment_threads")
    assert DBTaskResult.objects.filter(
        task_path__contains="crawl_comment_thread"
    ).count() == 1


@pytest.mark.django_db
def test_crawl_task_runs(monkeypatch):
    _, _, page = scene()
    bob = remote("bob")
    comment = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>x</p>",
    )
    monkeypatch.setattr("social.crawl.fetch_object", lambda url: None)
    crawl_comment_thread.func(str(comment.id))
    comment.refresh_from_db()
    assert comment.crawled_at is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_crawl.py -q -k "enqueues or command or task_runs"`
Expected: ERROR/FAIL — no task/command; crawl not enqueued.

- [ ] **Step 3: Create the task**

Create `social/tasks.py`:

```python
from django.tasks import task

from social import crawl
from social.models import Comment


@task
def crawl_comment_thread(comment_id):
    comment = Comment.objects.filter(pk=comment_id).first()
    if comment is None:
        return
    crawl.crawl_comment(comment)
```

- [ ] **Step 4: Create the management command**

Create empty `social/management/__init__.py` and `social/management/commands/__init__.py`.

Create `social/management/commands/crawl_comment_threads.py`:

```python
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from social.models import Comment
from social.tasks import crawl_comment_thread

CRAWL_TTL = timedelta(hours=24)


class Command(BaseCommand):
    help = "Enqueue crawls for comment threads that are stale."

    def handle(self, *args, **options):
        cutoff = timezone.now() - CRAWL_TTL
        stale = Comment.objects.filter(
            Q(crawled_at__isnull=True) | Q(crawled_at__lt=cutoff)
        )
        enqueued = 0
        for comment in stale:
            crawl_comment_thread.enqueue(str(comment.id))
            enqueued += 1
        self.stdout.write(self.style.SUCCESS(f"Enqueued {enqueued} crawl(s)."))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_crawl.py -q`
Expected: PASS (13 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add social/tasks.py social/management tests/test_social_crawl.py
git commit -m "feat: crawl task, ingest trigger, and cron sweep"
```

---

## Self-Review

**Spec coverage (Ticket 8c scope):** crawl the replies collection and walk `inReplyTo` chains (§5) via `crawl_comment`; background task + cron sweep (§3/§5); rate-limited (§13) via the per-domain throttle and `fetch_remote_actor`; incomplete-thread completeness improved best-effort. Out of scope by design: outbound comment delivery, object caching beyond the throttle, UI/read-path assembly.

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `store_inbound_reply` is the single ingest path for inbound and crawled replies; `federatable_page`/`reply_target` are shared helpers; `store_reply` is the single crawl-side store; `_Budget` is the single depth/size limiter; `crawl_comment` is the single entry point (task + command).

**Review Focus coverage:** banned/unresolvable author dropped — Tasks 1, 2; foreign `inReplyTo` stores nothing — Task 1; depth/size budget — Task 2; idempotent re-crawl — Tasks 1, 2; handler stores + enqueues — Task 3.