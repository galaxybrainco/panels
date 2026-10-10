# Ticket 8b — Comment Moderation, Bans & Comment Reactions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-page comment moderation (approve/hide/report), per-comic commenter bans, and comment-targeted likes/boosts.

**Architecture:** `social/moderation.py` owns the moderation transitions and bans behind `comics.permissions.can_moderate`. `social.CommentBan` stores per-comic bans; `Like`/`Boost` gain a nullable `comment` FK. Bans are enforced at both write paths (`add_comment`, inbound `Create`). Inbound `Like`/`Announce` resolve a comment Note before falling back to a page. The reply-tree crawl is 8c.

**Tech Stack:** Django 6.1.1, PostgreSQL, existing `actors`/`federation`/`comics`/`social` primitives, pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §4 (`Comment` moderation state; `Follow`/`Like`/`Boost` Actor→object), §5/§6 (per-page reply moderation is v1: hide/approve/report; comic-owner commenter bans), §9 (comic-owner commenter ban is local-only, never emits `Block`), §14 ticket 8.

## Global Constraints

- Synchronous Django only. Run Python via `uv run` (Python 3.14).
- Moderation actions and bans are gated by `comics.permissions.can_moderate(user, comic)` (owner/editor/moderator).
- Bans are **local-only**: create/hide rows, never emit an AP `Block`.
- Comment reactions target a visible `Comment` by its `ap_id`; `page` stays nullable and is set alongside `comment` for counts.
- Inbound `Like`/`Announce` resolve a visible comment by `ap_id` first, then a federatable page; anything else stores nothing.
- Page reactions and page moderation from earlier tickets must keep working unchanged.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- A non-moderator (contributor/outsider) must not approve/hide/report/ban. Task 1.
- Banning an actor must hide their existing visible comments and drop both inbound and local replies. Tasks 1, 2.
- An inbound `Like`/`Announce` on a **hidden** comment must store nothing. Task 3.
- Comment reactions must set the `comment` (and `page`) link and count per comment. Task 3.
- Page likes/boosts and page moderation must not regress. Tasks 1, 3.

## File Structure

- `social/models.py` — add `CommentBan`; add `Like.comment`, `Boost.comment`. (Task 1)
- `social/migrations/0005_*.py` — generated. (Task 1)
- `social/moderation.py` — new. (Task 1)
- `social/comments.py` — ban check on `add_comment`. (Task 2)
- `social/handlers.py` — ban check on inbound `Create`; comment resolution in `Like`/`Announce`. (Tasks 2, 3)
- `social/reactions.py` — comment action variants + counts. (Task 3)
- Tests: `tests/test_social_moderation.py` (new), `tests/test_social_reactions.py` (extended).

---

### Task 1: Bans, reaction links, and moderation services

**Files:**
- Modify: `social/models.py`
- Create: `social/moderation.py`, `social/migrations/0005_commentban_comment_links.py` (generated)
- Test: `tests/test_social_moderation.py`

**Interfaces:**
- Consumes: `comics.permissions.can_moderate`, `comics.Comic`, `actors.Actor`, `social.models.{Comment, CommentStatus, CommentBan}`.
- Produces:
  - `CommentBan` (`comic`, `actor`), `Like.comment`, `Boost.comment`.
  - `social.moderation.{approve_comment, hide_comment, report_comment, ban_commenter, unban_commenter, is_banned}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_social_moderation.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from uuid import uuid4

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.models import ComicRole
from comics.services import create_comic, create_page, create_series
from social.comments import add_comment
from social.models import Comment, CommentBan, CommentStatus
from social.moderation import (
    approve_comment,
    ban_commenter,
    hide_comment,
    is_banned,
    report_comment,
    unban_commenter,
)
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


def scene(require_reply_approval=False):
    suffix = uuid4().hex[:8]
    owner = get_user_model().objects.create_user(
        email=f"owner-{suffix}@example.com", password="x"
    )
    comic = create_comic(owner, f"comic{suffix}", "Lunar Baboon")
    if require_reply_approval:
        comic.require_reply_approval = True
        comic.save()
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)
    page.refresh_from_db()
    return owner, comic, page


@pytest.mark.django_db
def test_approve_makes_pending_visible():
    owner, _, page = scene(require_reply_approval=True)
    comment = add_comment(create_local_actor("alice"), page, "<p>x</p>")
    assert comment.status == CommentStatus.PENDING
    approve_comment(owner, comment)
    comment.refresh_from_db()
    assert comment.status == CommentStatus.VISIBLE


@pytest.mark.django_db
def test_hide_and_report():
    owner, _, page = scene()
    comment = add_comment(create_local_actor("alice"), page, "<p>x</p>")
    hide_comment(owner, comment)
    comment.refresh_from_db()
    assert comment.status == CommentStatus.HIDDEN
    report_comment(owner, comment)
    comment.refresh_from_db()
    assert comment.status == CommentStatus.REPORTED


@pytest.mark.django_db
def test_moderation_permissions():
    owner, comic, page = scene()
    contributor = get_user_model().objects.create_user(
        email="contributor@example.com", password="x"
    )
    outsider = get_user_model().objects.create_user(
        email="outsider@example.com", password="x"
    )
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    comment = add_comment(create_local_actor("alice"), page, "<p>x</p>")
    with pytest.raises(PermissionDenied):
        hide_comment(contributor, comment)
    with pytest.raises(PermissionDenied):
        hide_comment(outsider, comment)
    with pytest.raises(PermissionDenied):
        ban_commenter(contributor, comic, create_local_actor("mallory"))


@pytest.mark.django_db
def test_ban_hides_existing_comments_and_records_ban():
    owner, comic, page = scene()
    mallory = create_local_actor("mallory")
    comment = add_comment(mallory, page, "<p>x</p>")
    ban_commenter(owner, comic, mallory)
    comment.refresh_from_db()
    assert comment.status == CommentStatus.HIDDEN
    assert is_banned(comic, mallory) is True
    assert CommentBan.objects.filter(comic=comic, actor=mallory).exists()


@pytest.mark.django_db
def test_unban_removes_ban():
    owner, comic, _ = scene()
    mallory = create_local_actor("mallory")
    ban_commenter(owner, comic, mallory)
    unban_commenter(owner, comic, mallory)
    assert is_banned(comic, mallory) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_moderation.py -q`
Expected: ERROR — `social.moderation` does not exist; `CommentBan`/`comment` missing.

- [ ] **Step 3: Add the models**

Append to `social/models.py`:

```python
class CommentBan(UUIDModel):
    comic = models.ForeignKey(
        "comics.Comic", on_delete=models.CASCADE, related_name="comment_bans"
    )
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="comment_bans"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "actor"], name="unique_comment_ban"
            )
        ]

    def __str__(self):
        return f"{self.actor} banned from {self.comic}"
```

Add a nullable `comment` FK to both `Like` and `Boost` (after their `page` field):

```python
    comment = models.ForeignKey(
        "social.Comment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="likes",
    )
```

(For `Boost`, use `related_name="boosts"`.)

- [ ] **Step 4: Implement `social/moderation.py`**

```python
from django.core.exceptions import PermissionDenied
from django.db import transaction

from comics import permissions
from social.models import Comment, CommentBan, CommentStatus


def _require_moderator(user, comic):
    if not permissions.can_moderate(user, comic):
        raise PermissionDenied("You cannot moderate this comic's comments.")


def _set_status(user, comment, status):
    _require_moderator(user, comment.page.series.comic)
    comment.status = status
    comment.save(update_fields=["status", "updated_at"])
    return comment


def approve_comment(user, comment):
    return _set_status(user, comment, CommentStatus.VISIBLE)


def hide_comment(user, comment):
    return _set_status(user, comment, CommentStatus.HIDDEN)


def report_comment(user, comment):
    return _set_status(user, comment, CommentStatus.REPORTED)


@transaction.atomic
def ban_commenter(user, comic, actor):
    _require_moderator(user, comic)
    ban, _ = CommentBan.objects.get_or_create(comic=comic, actor=actor)
    Comment.objects.filter(
        page__series__comic=comic, actor=actor, status=CommentStatus.VISIBLE
    ).update(status=CommentStatus.HIDDEN)
    return ban


@transaction.atomic
def unban_commenter(user, comic, actor):
    _require_moderator(user, comic)
    CommentBan.objects.filter(comic=comic, actor=actor).delete()


def is_banned(comic, actor):
    return CommentBan.objects.filter(comic=comic, actor=actor).exists()
```

- [ ] **Step 5: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations social
uv run python manage.py migrate
uv run pytest tests/test_social_moderation.py -q
```
Expected: PASS (5 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add social/models.py social/moderation.py social/migrations tests/test_social_moderation.py
git commit -m "feat: comment moderation and per-comic commenter bans"
```

---

### Task 2: Enforce bans on both reply paths

**Files:**
- Modify: `social/comments.py`, `social/handlers.py`
- Test: `tests/test_social_moderation.py`

**Interfaces:**
- Consumes: Task 1 `is_banned`; existing `add_comment`, inbound `Create` handler.
- Produces: banned actors cannot create local or inbound replies.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_moderation.py` (add to imports: `from django.core.exceptions import ValidationError`, `from federation import handlers`, `from federation.models import Activity, ActivityDirection`):

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
def test_banned_local_actor_cannot_comment():
    owner, comic, page = scene()
    mallory = create_local_actor("mallory")
    ban_commenter(owner, comic, mallory)
    with pytest.raises(ValidationError):
        add_comment(mallory, page, "<p>x</p>")


@pytest.mark.django_db
def test_banned_remote_actor_reply_is_dropped():
    owner, comic, page = scene()
    bob = remote("bob")
    ban_commenter(owner, comic, bob)
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_moderation.py -q -k banned`
Expected: FAIL — the local comment is created and the inbound reply is stored.

- [ ] **Step 3: Enforce the ban on both paths**

In `social/comments.py`, add the import and a check in `add_comment` (after the published check):

```python
from social.moderation import is_banned
```

```python
    if is_banned(page.series.comic, actor):
        raise ValidationError("You are banned from replying to this comic.")
```

In `social/handlers.py`, add the import and a check in `handle_create` after the page is resolved (and after the parent branch):

```python
from social.moderation import is_banned
```

```python
    if is_banned(page.series.comic, activity.actor):
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_moderation.py tests/test_social_comments.py -q`
Expected: PASS (all moderation + comment tests).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add social/comments.py social/handlers.py tests/test_social_moderation.py
git commit -m "feat: enforce commenter bans on local and inbound replies"
```

---

### Task 3: Comment-targeted likes and boosts

**Files:**
- Modify: `social/reactions.py`, `social/handlers.py`
- Test: `tests/test_social_reactions.py`

**Interfaces:**
- Consumes: Task 1 `Like.comment`/`Boost.comment`; `social.models.{Comment, CommentStatus}`; existing inbound `Like`/`Announce` handlers.
- Produces: `like_comment`/`unlike_comment`/`boost_comment`/`unboost_comment`, `comment_like_count`/`comment_boost_count`; comment-aware inbound resolution.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_reactions.py` (add to imports: `from social.comments import add_comment`, `from social.models import CommentStatus`, and extend the `social.reactions` import with `boost_comment`, `comment_boost_count`, `comment_like_count`, `like_comment`, `unboost_comment`, `unlike_comment`):

```python
@pytest.mark.django_db
def test_like_comment_stores_link_and_counts():
    page = published_page()
    comment = add_comment(create_local_actor("alice"), page, "<p>x</p>")
    liker = create_local_actor("liker")
    like_comment(liker, comment)
    assert comment_like_count(comment) == 1
    stored = Like.objects.get(actor=liker)
    assert stored.comment == comment
    assert stored.page == page
    unlike_comment(liker, comment)
    assert comment_like_count(comment) == 0


@pytest.mark.django_db
def test_boost_comment_and_unboost():
    page = published_page()
    comment = add_comment(create_local_actor("alice"), page, "<p>x</p>")
    booster = create_local_actor("booster")
    boost_comment(booster, comment)
    assert comment_boost_count(comment) == 1
    unboost_comment(booster, comment)
    assert comment_boost_count(comment) == 0


@pytest.mark.django_db
def test_inbound_like_targets_a_comment():
    page = published_page()
    comment = add_comment(create_local_actor("alice"), page, "<p>x</p>")
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": comment.ap_id,
    }
    handlers.dispatch(_inbound("Like", bob, payload))
    stored = Like.objects.get(actor=bob)
    assert stored.comment == comment
    assert stored.page == page


@pytest.mark.django_db
def test_inbound_like_on_hidden_comment_is_ignored():
    page = published_page()
    comment = add_comment(create_local_actor("alice"), page, "<p>x</p>")
    comment.status = CommentStatus.HIDDEN
    comment.save()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": comment.ap_id,
    }
    handlers.dispatch(_inbound("Like", bob, payload))
    assert not Like.objects.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_reactions.py -q -k "comment"`
Expected: ERROR — `boost_comment`/`comment_*_count`/`like_comment` do not exist.

- [ ] **Step 3: Add comment action variants**

In `social/reactions.py`, add `from social.models import Boost, Comment, CommentStatus, Like` (extend the import) and append:

```python
def _require_visible_comment(comment):
    if comment.status != CommentStatus.VISIBLE or not comment.ap_id:
        raise ValidationError("Only visible comments can be liked or boosted.")
    return comment.ap_id


@transaction.atomic
def like_comment(actor, comment):
    like_obj, _ = Like.objects.get_or_create(
        actor=actor,
        object_id=_require_visible_comment(comment),
        defaults={"comment": comment, "page": comment.page},
    )
    return like_obj


@transaction.atomic
def unlike_comment(actor, comment):
    if comment.ap_id:
        Like.objects.filter(actor=actor, object_id=comment.ap_id).delete()


@transaction.atomic
def boost_comment(actor, comment):
    boost_obj, _ = Boost.objects.get_or_create(
        actor=actor,
        object_id=_require_visible_comment(comment),
        defaults={"comment": comment, "page": comment.page},
    )
    return boost_obj


@transaction.atomic
def unboost_comment(actor, comment):
    if comment.ap_id:
        Boost.objects.filter(actor=actor, object_id=comment.ap_id).delete()


def comment_like_count(comment):
    return Like.objects.filter(comment=comment).count()


def comment_boost_count(comment):
    return Boost.objects.filter(comment=comment).count()
```

- [ ] **Step 4: Resolve comments in the inbound handlers**

In `social/handlers.py`, add `CommentStatus` to the `social.models` import, add a resolver, and use it in both handlers:

```python
def _resolve_reaction_target(object_id):
    comment = Comment.objects.filter(
        ap_id=object_id, status=CommentStatus.VISIBLE
    ).first()
    if comment is not None:
        return comment.page, comment
    page = _federatable_page(object_id)
    if page is None:
        return None, None
    return page, None
```

Replace the body of `handle_like` with:

```python
@handlers.register("Like")
def handle_like(activity):
    object_id = _object_iri(activity.payload.get("object"))
    page, comment = _resolve_reaction_target(object_id)
    if page is None:
        return
    like_obj, created = Like.objects.get_or_create(
        actor=activity.actor,
        object_id=object_id,
        defaults={"page": page, "comment": comment, "activity_id": activity.ap_id},
    )
    if not created and not like_obj.activity_id:
        like_obj.activity_id = activity.ap_id
        like_obj.save(update_fields=["activity_id", "updated_at"])
```

Replace the body of `handle_announce` the same way with `Boost`:

```python
@handlers.register("Announce")
def handle_announce(activity):
    object_id = _object_iri(activity.payload.get("object"))
    page, comment = _resolve_reaction_target(object_id)
    if page is None:
        return
    boost_obj, created = Boost.objects.get_or_create(
        actor=activity.actor,
        object_id=object_id,
        defaults={"page": page, "comment": comment, "activity_id": activity.ap_id},
    )
    if not created and not boost_obj.activity_id:
        boost_obj.activity_id = activity.ap_id
        boost_obj.save(update_fields=["activity_id", "updated_at"])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_reactions.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add social/reactions.py social/handlers.py tests/test_social_reactions.py
git commit -m "feat: comment-targeted likes and boosts"
```

---

## Self-Review

**Spec coverage (Ticket 8b scope):** per-page comment moderation (approve/hide/report) behind `can_moderate` (§5/§6/§4); per-comic commenter bans that are local-only and hide existing comments + drop future replies (§9); comment-targeted `Like`/`Announce` (§4). Out of scope by design (8c/ticket 10): reply-tree crawl, outbound comment delivery, the unified reports queue, federated `Block`.

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `CommentStatus`/`CommentBan` are the single moderation vocabulary; `can_moderate` is the single authorization gate; `is_banned` is the single ban predicate used by both write paths; `_resolve_reaction_target` is the single inbound reaction target resolver; `comment_like_count`/`comment_boost_count` count by the `comment` link.

**Review Focus coverage:** non-moderator denial — Task 1; ban hides + drops — Tasks 1, 2; hidden-comment reactions ignored — Task 3; comment link + counts — Task 3; page behavior unchanged — Tasks 1, 3 (existing suites).