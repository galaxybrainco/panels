# Ticket 8a — Comments: Model, Creation, Inbound Replies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A threaded `Comment` on a page (local or remote, authored by an Actor), with sanitized content, per-comic approve-before-show, inbound `Create{Note}` reply handling, and a dereferenceable comment Note.

**Architecture:** `social.Comment` points at a `comics.Page` and optionally a parent `Comment`; `social/sanitize.py` cleans untrusted HTML with nh3; `social/comments.py` owns local creation + the reply Note serializer; `social/handlers.py` registers inbound `Create` to store replies (gated by `federation_plan`); `social/views.py` serves a local comment's Note. Moderation actions, comment-targeted reactions, and the reply-tree crawl are 8b.

**Tech Stack:** Django 6.1.1, PostgreSQL, `nh3` (HTML sanitizer), existing `actors`/`federation`/`comics`/`social` primitives, pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §4 (`Comment` = reply object, `inReplyTo` a page or comment, Actor-authored, threaded, moderation state), §5 (comments = inbound replies; per-page reply moderation is v1), §6 (inline reply moderation), §14 ticket 8.

## Global Constraints

- Synchronous Django only. Run Python via `uv run` (Python 3.14).
- Comment content is untrusted remote HTML: **always** pass it through `social.sanitize.sanitize_html` before storing.
- A comment belongs to exactly one page; `parent` (nullable) is the commented-on comment; `in_reply_to` is the parent's or page's Note URL.
- Default status is `visible`; when the page's comic has `require_reply_approval=True`, new comments are `pending`.
- Inbound replies are stored only when the page is published and `federation_plan(page).emit` is true; foreign/unknown `inReplyTo`, non-`Note` objects, and gated/unknown pages are ignored.
- Inbound replies are idempotent by the reply Note `ap_id`.
- Local comments are dogfooded (no HTTP); outbound reply delivery is out of scope for 8a.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- Comment content must be sanitized (scripts/handlers stripped) before storage. Task 1.
- A comic's `require_reply_approval` must make new comments `pending`. Task 1.
- A reply to a comment must link `parent` and inherit the parent's page. Task 2.
- An inbound reply with a foreign/unknown `inReplyTo`, a non-`Note` object, or on a Members/local-only page must store nothing. Task 2.
- A duplicate inbound reply (same `ap_id`) must not create a second row. Task 2.
- The endpoint must 404 for a non-visible comment and must not serve a remote comment at a local URL. Task 3.

## File Structure

- `pyproject.toml` — add `nh3`. (Task 1)
- `social/sanitize.py` — new: `sanitize_html`. (Task 1)
- `social/models.py` — add `CommentStatus`, `Comment`. (Task 1)
- `comics/models.py` — add `Comic.require_reply_approval`. (Task 1)
- `social/comments.py` — new: `add_comment`, `comment_status_for`, `comment_to_note`. (Task 1)
- `social/handlers.py` — register `Create`. (Task 2)
- `social/views.py`, `social/urls.py`, `config/urls.py` — comment endpoint. (Task 3)
- `tests/test_social_comments.py` — new, extended across tasks.

---

### Task 1: Sanitizer, `Comment` model, and local creation

**Files:**
- Modify: `pyproject.toml` (`uv add nh3`), `social/models.py`, `comics/models.py`
- Create: `social/sanitize.py`, `social/comments.py`, `social/migrations/0004_comment.py` (generated), `comics/migrations/0004_comic_require_reply_approval.py` (generated)
- Test: `tests/test_social_comments.py`

**Interfaces:**
- Consumes: `actors.models.Actor`, `comics.models.{Page, PageStatus}`, `federation.activitypub.activity_context`.
- Produces:
  - `CommentStatus` (`VISIBLE`/`PENDING`/`HIDDEN`/`REPORTED`), `Comment` (`actor`, `page`, `parent`, `ap_id`, `in_reply_to`, `content`, `status`, `activity_id`).
  - `Comic.require_reply_approval`.
  - `social.sanitize.sanitize_html(str) -> str`.
  - `social.comments.{add_comment, comment_status_for, comment_to_note}`.

- [ ] **Step 1: Add nh3 and write the failing tests**

Run: `uv add nh3`

Create `tests/test_social_comments.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from social.comments import add_comment, comment_to_note
from social.models import CommentStatus
from social.sanitize import sanitize_html
from tests.media_support import make_media


def local(handle="alice"):
    return create_local_actor(handle)


def remote(handle="bob"):
    domain = f"{handle}.test"
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
    )


def published_page(require_reply_approval=False):
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    if require_reply_approval:
        comic.require_reply_approval = True
        comic.save()
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)
    page.refresh_from_db()
    return comic, page


def test_sanitize_html_strips_scripts_and_handlers():
    assert sanitize_html("<p>hi</p><script>bad()</script>") == "<p>hi</p>"
    cleaned = sanitize_html('<a href="https://a.test" onclick="x()">l</a>')
    assert 'href="https://a.test"' in cleaned
    assert "onclick" not in cleaned
    assert 'rel="noopener noreferrer"' in cleaned


@pytest.mark.django_db
def test_add_comment_sanitizes_and_links_to_page():
    _, page = published_page()
    comment = add_comment(local("alice"), page, "<p>Nice</p><script>x()</script>")
    assert comment.content == "<p>Nice</p>"
    assert comment.in_reply_to == page.ap_id
    assert comment.ap_id.startswith("http://testserver/comments/")
    assert comment.status == CommentStatus.VISIBLE


@pytest.mark.django_db
def test_add_comment_reply_links_parent():
    _, page = published_page()
    top = add_comment(local("alice"), page, "<p>Top</p>")
    reply = add_comment(local("bob"), page, "<p>Reply</p>", parent=top)
    assert reply.parent == top
    assert reply.in_reply_to == top.ap_id
    assert reply.page == page


@pytest.mark.django_db
def test_add_comment_pending_when_comic_requires_approval():
    _, page = published_page(require_reply_approval=True)
    comment = add_comment(local("alice"), page, "<p>x</p>")
    assert comment.status == CommentStatus.PENDING


@pytest.mark.django_db
def test_add_comment_requires_a_published_page():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    draft = create_page(owner, series)
    with pytest.raises(ValidationError):
        add_comment(local("alice"), draft, "<p>x</p>")


@pytest.mark.django_db
def test_comment_to_note_shape():
    _, page = published_page()
    comment = add_comment(local("alice"), page, "<p>Nice</p>")
    note = comment_to_note(comment)
    assert note["type"] == "Note"
    assert note["id"] == comment.ap_id
    assert note["attributedTo"] == comment.actor.ap_id
    assert note["inReplyTo"] == page.ap_id
    assert note["content"] == "<p>Nice</p>"
    assert "published" in note
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_comments.py -q`
Expected: ERROR — `social.models` has no `Comment`; `social.sanitize`/`social.comments` do not exist.

- [ ] **Step 3: Implement `social/sanitize.py`**

```python
import nh3

ALLOWED_TAGS = {
    "p",
    "br",
    "a",
    "span",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "ul",
    "ol",
    "li",
    "del",
    "blockquote",
    "code",
    "pre",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "rel", "class"},
    "span": {"class"},
}


def sanitize_html(html: str) -> str:
    return nh3.clean(
        html or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
    )
```

- [ ] **Step 4: Add the `Comment` model and the comic flag**

Append to `social/models.py`:

```python
class CommentStatus(models.TextChoices):
    VISIBLE = "visible", "Visible"
    PENDING = "pending", "Pending"
    HIDDEN = "hidden", "Hidden"
    REPORTED = "reported", "Reported"


class Comment(UUIDModel):
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="comments"
    )
    page = models.ForeignKey(
        "comics.Page", on_delete=models.CASCADE, related_name="comments"
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
    )
    ap_id = models.URLField(unique=True)
    in_reply_to = models.URLField()
    content = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16, choices=CommentStatus.choices, default=CommentStatus.VISIBLE
    )
    activity_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.actor} on {self.page}"
```

In `comics/models.py`, add to `Comic` (after `default_federation`):

```python
    require_reply_approval = models.BooleanField(default=False)
```

- [ ] **Step 5: Implement `social/comments.py`**

```python
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction

from comics.models import PageStatus
from federation.activitypub import activity_context
from social.models import Comment, CommentStatus
from social.sanitize import sanitize_html


def comment_status_for(page):
    if page.series.comic.require_reply_approval:
        return CommentStatus.PENDING
    return CommentStatus.VISIBLE


@transaction.atomic
def add_comment(actor, page, content, parent=None):
    if page.status != PageStatus.PUBLISHED or not page.ap_id:
        raise ValidationError("Comments require a published page.")
    comment = Comment(
        actor=actor,
        page=page,
        parent=parent,
        ap_id=f"{settings.INSTANCE_URL}/comments/{uuid4()}",
        in_reply_to=parent.ap_id if parent else page.ap_id,
        content=sanitize_html(content),
        status=comment_status_for(page),
    )
    comment.save()
    return comment


def comment_to_note(comment):
    return {
        "@context": activity_context(),
        "id": comment.ap_id,
        "type": "Note",
        "attributedTo": comment.actor.ap_id,
        "inReplyTo": comment.in_reply_to,
        "content": comment.content,
        "published": comment.created_at.isoformat(),
    }
```

Add `from django.conf import settings` to the imports of `social/comments.py`.

- [ ] **Step 6: Create the migrations and run the tests**

Run:
```bash
uv run python manage.py makemigrations social comics
uv run python manage.py migrate
uv run pytest tests/test_social_comments.py -q
```
Expected: PASS (6 passed).

- [ ] **Step 7: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock social/sanitize.py social/models.py social/comments.py social/migrations comics/models.py comics/migrations tests/test_social_comments.py
git commit -m "feat: Comment model, sanitizer, and local comment creation"
```

---

### Task 2: Inbound `Create` replies

**Files:**
- Modify: `social/handlers.py`
- Test: `tests/test_social_comments.py`

**Interfaces:**
- Consumes: Task 1 `Comment`/`CommentStatus`/`comment_status_for`, `sanitize_html`; `comics.federation.federation_plan`; `comics.models.{Page, PageStatus}`; `federation.handlers.register`.
- Produces: a handler registered for `"Create"` storing reply Notes.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_comments.py` (add to imports: `from federation import handlers`, `from federation.activitypub import Audience`, `from federation.models import Activity, ActivityDirection`, `from social.models import Comment`):

```python
def _inbound(activity_type, actor, payload):
    return Activity.objects.create(
        ap_id=payload["id"],
        type=activity_type,
        actor=actor,
        direction=ActivityDirection.INBOUND,
        payload=payload,
    )


@pytest.mark.django_db
def test_inbound_create_reply_to_page():
    _, page = published_page()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "https://bob.test/notes/1",
            "type": "Note",
            "inReplyTo": page.ap_id,
            "content": "<p>Great page</p><script>x()</script>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    comment = Comment.objects.get(ap_id="https://bob.test/notes/1")
    assert comment.page == page
    assert comment.parent is None
    assert comment.content == "<p>Great page</p>"
    assert comment.status == CommentStatus.VISIBLE


@pytest.mark.django_db
def test_inbound_create_reply_to_comment_links_parent():
    _, page = published_page()
    parent = add_comment(local("alice"), page, "<p>Top</p>")
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "https://bob.test/notes/1",
            "type": "Note",
            "inReplyTo": parent.ap_id,
            "content": "<p>Reply</p>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    comment = Comment.objects.get(ap_id="https://bob.test/notes/1")
    assert comment.parent == parent
    assert comment.page == page


@pytest.mark.django_db
def test_inbound_create_ignored_for_foreign_in_reply_to():
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "https://bob.test/notes/1",
            "type": "Note",
            "inReplyTo": "https://elsewhere.test/notes/nope",
            "content": "<p>x</p>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    assert not Comment.objects.exists()


@pytest.mark.django_db
def test_inbound_create_ignored_for_members_page():
    _, page = published_page()
    page.audience = Audience.MEMBERS
    page.save()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "https://bob.test/notes/1",
            "type": "Note",
            "inReplyTo": page.ap_id,
            "content": "<p>x</p>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    assert not Comment.objects.exists()


@pytest.mark.django_db
def test_inbound_create_ignored_for_non_note():
    _, page = published_page()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {"id": page.ap_id, "type": "Article", "inReplyTo": page.ap_id},
    }
    handlers.dispatch(_inbound("Create", bob, payload))
    assert not Comment.objects.exists()


@pytest.mark.django_db
def test_inbound_create_is_idempotent():
    _, page = published_page()
    bob = remote("bob")
    first = {
        "id": "https://bob.test/activities/1",
        "type": "Create",
        "actor": bob.ap_id,
        "object": {
            "id": "https://bob.test/notes/1",
            "type": "Note",
            "inReplyTo": page.ap_id,
            "content": "<p>x</p>",
        },
    }
    handlers.dispatch(_inbound("Create", bob, first))
    handlers.dispatch(_inbound("Create", bob, {**first, "id": "https://bob.test/a/2"}))
    assert Comment.objects.count() == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_comments.py -q -k inbound`
Expected: FAIL — no `Create` handler; no comments stored.

- [ ] **Step 3: Register the `Create` handler**

In `social/handlers.py`, add imports and the handler:

```python
from comics.models import Page, PageStatus
from social.comments import comment_status_for
from social.models import Boost, Comment, Follow, FollowStatus, Like
from social.sanitize import sanitize_html


@handlers.register("Create")
def handle_create(activity):
    note = activity.payload.get("object")
    if not isinstance(note, dict) or note.get("type") != "Note":
        return
    in_reply_to = note.get("inReplyTo")
    ap_id = note.get("id")
    if not in_reply_to or not ap_id:
        return
    parent = None
    page = _federatable_page(in_reply_to)
    if page is None:
        parent = Comment.objects.filter(ap_id=in_reply_to).first()
        if parent is None:
            return
        page = parent.page
        if page.status != PageStatus.PUBLISHED or not federation_plan(page).emit:
            return
    if Comment.objects.filter(ap_id=ap_id).exists():
        return
    Comment.objects.create(
        actor=activity.actor,
        page=page,
        parent=parent,
        ap_id=ap_id,
        in_reply_to=in_reply_to,
        content=sanitize_html(note.get("content", "")),
        status=comment_status_for(page),
        activity_id=activity.ap_id,
    )
```

(`Page`, `PageStatus`, `federation_plan`, `Comment`, `sanitize_html`, and `comment_status_for` are the only additions; keep the existing Follow/Like/Boost/Accept imports.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_comments.py -q`
Expected: PASS (12 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add social/handlers.py tests/test_social_comments.py
git commit -m "feat: inbound Create replies become comments"
```

---

### Task 3: Dereferenceable comment Note endpoint

**Files:**
- Create: `social/views.py`, `social/urls.py`
- Modify: `config/urls.py`, `FEDERATION.md`
- Test: `tests/test_social_comments.py`

**Interfaces:**
- Consumes: Task 1 `comment_to_note`, `Comment`, `CommentStatus`.
- Produces: `social.views.comment_detail(request, comment_id)`, route name `comment-detail` at `comments/<uuid:comment_id>`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_comments.py`:

```python
@pytest.mark.django_db
def test_comment_endpoint_serves_visible_local_comment(client):
    _, page = published_page()
    comment = add_comment(local("alice"), page, "<p>Nice</p>")
    response = client.get(f"/comments/{comment.id}")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/activity+json")
    assert response.json()["type"] == "Note"
    assert response.json()["id"] == comment.ap_id


@pytest.mark.django_db
def test_comment_endpoint_404_for_pending_comment(client):
    _, page = published_page(require_reply_approval=True)
    comment = add_comment(local("alice"), page, "<p>Nice</p>")
    assert comment.status == CommentStatus.PENDING
    assert client.get(f"/comments/{comment.id}").status_code == 404


@pytest.mark.django_db
def test_comment_endpoint_404_for_remote_comment(client):
    _, page = published_page()
    bob = remote("bob")
    comment = Comment.objects.create(
        actor=bob,
        page=page,
        ap_id="https://bob.test/notes/1",
        in_reply_to=page.ap_id,
        content="<p>x</p>",
    )
    assert client.get(f"/comments/{comment.id}").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_comments.py -q -k endpoint`
Expected: FAIL — no route/view.

- [ ] **Step 3: Implement the view and route**

Create `social/views.py`:

```python
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from actors.activitypub import ACTIVITYPUB_CONTENT_TYPE
from social.comments import comment_to_note
from social.models import Comment, CommentStatus


def comment_detail(request, comment_id):
    comment = get_object_or_404(
        Comment,
        pk=comment_id,
        status=CommentStatus.VISIBLE,
        ap_id__startswith=settings.INSTANCE_URL,
    )
    response = JsonResponse(
        comment_to_note(comment), content_type=ACTIVITYPUB_CONTENT_TYPE
    )
    response["Access-Control-Allow-Origin"] = "*"
    return response
```

Create `social/urls.py`:

```python
from django.urls import path

from social import views

urlpatterns = [
    path("comments/<uuid:comment_id>", views.comment_detail, name="comment-detail"),
]
```

In `config/urls.py`, add `path("", include("social.urls"))`.

In `FEDERATION.md`, under `## Implemented`, add:

```markdown
- **Comment `Note` objects** — replies on pages are stored as threaded `Comment`s and served at `{INSTANCE_URL}/comments/{id}` (`application/activity+json`); inbound `Create`/`Note` replies are accepted.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_comments.py -q`
Expected: PASS (15 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add social/views.py social/urls.py config/urls.py FEDERATION.md tests/test_social_comments.py
git commit -m "feat: serve comment Notes at their object id"
```

---

## Self-Review

**Spec coverage (Ticket 8a scope):** threaded `Comment` with `inReplyTo` a page or comment, Actor-authored (§4); inbound replies stored as comments (§5); per-comic approve-before-show via `require_reply_approval` (§5/§6 groundwork); sanitized content (untrusted HTML); dereferenceable comment Note. Out of scope by design (8b): moderation actions (approve/hide/report/block), comment-targeted likes/boosts (`comment` FK + resolver), reply-tree crawl, outbound reply delivery, comment `Delete`.

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `CommentStatus`/`Comment` are the single comment vocabulary; `sanitize_html` is the single sanitization path; `comment_status_for` is the single approval decision; `comment_to_note` is the single reply serializer; `federation_plan` is the single gating source.

**Review Focus coverage:** sanitization — Task 1; approval flag — Task 1; reply-to-comment parent — Task 2; foreign/non-Note/gated ignored — Task 2; idempotency — Task 2; endpoint 404s — Task 3.