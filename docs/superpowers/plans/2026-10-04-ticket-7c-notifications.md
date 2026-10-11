# Ticket 7c — Notifications & Reading Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local notifications for new pages, replies, likes/boosts, and new followers, plus per-series reading progress for resume.

**Architecture:** `social.ReadingProgress` (per `(user, series)`) and `social.Notification` (recipient User, type, triggering Actor, links). `social/progress.py` records/`resumes`; `social/notifications.py` has a single `notify(...)` plus per-event generators, called from existing hooks (publish, accept-follow, comment store, reactions). Notifications are local-only (remote actors get AP); the local feed is a later ticket.

**Tech Stack:** Django 6.1.1, PostgreSQL, existing `accounts`/`actors`/`comics`/`social` primitives, pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §4 (`Notification`, `ReadingProgress`), §6 (personal feed + resume), §14 ticket 7.

## Global Constraints

- Synchronous Django only. Run Python via `uv run` (Python 3.14).
- One `ReadingProgress` per `(user, series)` (upsert).
- Notifications target **local users only**, resolved to the per-content author; a missing recipient or a self-notification (the triggering actor *is* the recipient) creates nothing.
- Generator hooks live at the existing seams; no view/URL layer.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- The triggering actor must never be notified about their own action. Tasks 1–3.
- Remote followers/authors must never produce a local notification; only local users are recipients. Tasks 2, 3.
- A reply to a comment notifies the parent author; a top-level reply notifies the page's publishing user. Task 3.
- Publishing must notify each local follower once. Task 2.
- `record_progress` must upsert (no duplicate rows per series). Task 1.

## File Structure

- `social/models.py` — add `ReadingProgress`, `NotificationType`, `Notification`. (Task 1)
- `social/migrations/0008_*.py` — generated. (Task 1)
- `social/progress.py` — new. (Task 1)
- `social/notifications.py` — new: `notify`, `mark_read`/`mark_all_read` (Task 1); generators (Tasks 2–3).
- `comics/publishing.py` — notify new page. (Task 2)
- `social/services.py` — notify on `accept_follow`. (Task 2)
- `social/comments.py` — notify reply. (Task 3)
- `social/reactions.py` + `social/handlers.py` — notify like/boost. (Task 3)
- Tests: `tests/test_social_progress.py`, `tests/test_social_notifications.py`.

---

### Task 1: Progress model and the notification core

**Files:**
- Modify: `social/models.py`
- Create: `social/progress.py`, `social/notifications.py`, `social/migrations/0008_*.py` (generated)
- Test: `tests/test_social_progress.py`, `tests/test_social_notifications.py`

**Interfaces:**
- Consumes: `django.conf.settings.AUTH_USER_MODEL`, `comics.models.{Series, Page}`, `actors.models.Actor`, `social.models.Comment`.
- Produces:
  - `ReadingProgress` (`user`, `series`, `page`), `NotificationType`, `Notification` (`recipient`, `type`, `actor`, `page`, `comment`, `read`).
  - `social.progress.{record_progress, resume_point}`.
  - `social.notifications.{notify, mark_read, mark_all_read}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_social_progress.py`:

```python
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from social.progress import record_progress, resume_point
from tests.media_support import make_media


def _page(owner, handle, series_title="Main"):
    comic = create_comic(owner, handle, "Lunar Baboon")
    series = create_series(owner, comic, series_title)
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    return series, page


@pytest.mark.django_db
def test_record_progress_upserts_last_page():
    owner = get_user_model().objects.create_user(
        email=f"o-{uuid4().hex[:8]}@example.com", password="x"
    )
    reader = get_user_model().objects.create_user(
        email=f"r-{uuid4().hex[:8]}@example.com", password="x"
    )
    series, first = _page(owner, f"comic{uuid4().hex[:6]}")
    second = create_page(owner, series)
    record_progress(reader, first)
    record_progress(reader, second)
    assert resume_point(reader, series).page == second
    assert resume_point(reader, series).__class__.objects.filter(
        user=reader, series=series
    ).count() == 1


@pytest.mark.django_db
def test_resume_point_is_none_without_progress():
    owner = get_user_model().objects.create_user(
        email=f"o-{uuid4().hex[:8]}@example.com", password="x"
    )
    reader = get_user_model().objects.create_user(
        email=f"r-{uuid4().hex[:8]}@example.com", password="x"
    )
    series, _ = _page(owner, f"comic{uuid4().hex[:6]}")
    assert resume_point(reader, series) is None
```

Create `tests/test_social_notifications.py`:

```python
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from social.models import Notification, NotificationType
from social.notifications import mark_all_read, mark_read, notify


def user(email=None):
    return get_user_model().objects.create_user(
        email=email or f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


def remote(handle="bob"):
    domain = f"{handle}.test"
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
    )


@pytest.mark.django_db
def test_notify_creates_a_notification():
    recipient = user()
    actor = remote("bob")
    note = notify(recipient, type=NotificationType.FOLLOW, actor=actor)
    assert note.recipient == recipient
    assert note.type == NotificationType.FOLLOW


@pytest.mark.django_db
def test_notify_skips_missing_recipient():
    assert notify(None, type=NotificationType.FOLLOW) is None
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_notify_skips_self():
    actor = create_local_actor("solo", user=None)
    owner = user()
    actor.user = owner
    actor.save()
    assert notify(owner, type=NotificationType.FOLLOW, actor=actor) is None


@pytest.mark.django_db
def test_mark_read_and_all_read():
    recipient = user()
    keep = notify(recipient, type=NotificationType.FOLLOW)
    mark_read(keep)
    keep.refresh_from_db()
    assert keep.read is True
    notify(recipient, type=NotificationType.LIKE)
    mark_all_read(recipient)
    assert not Notification.objects.filter(recipient=recipient, read=False).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_progress.py tests/test_social_notifications.py -q`
Expected: ERROR — `social.models` has no `ReadingProgress`/`Notification`; `social.progress`/`social.notifications` do not exist.

- [ ] **Step 3: Add the models**

In `social/models.py`, add `from django.conf import settings` to the imports, then append:

```python
class ReadingProgress(UUIDModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reading_progress",
    )
    series = models.ForeignKey(
        "comics.Series", on_delete=models.CASCADE, related_name="reading_progress"
    )
    page = models.ForeignKey(
        "comics.Page",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "series"], name="unique_progress_per_series"
            )
        ]

    def __str__(self):
        return f"{self.user} @ {self.series}"


class NotificationType(models.TextChoices):
    NEW_PAGE = "new_page", "New page"
    REPLY = "reply", "Reply"
    LIKE = "like", "Like"
    BOOST = "boost", "Boost"
    FOLLOW = "follow", "Follow"


class Notification(UUIDModel):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    type = models.CharField(max_length=16, choices=NotificationType.choices)
    actor = models.ForeignKey(
        "actors.Actor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
    )
    page = models.ForeignKey(
        "comics.Page",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    comment = models.ForeignKey(
        "social.Comment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "read"])]

    def __str__(self):
        return f"{self.type} → {self.recipient}"
```

- [ ] **Step 4: Implement `social/progress.py` and the core of `social/notifications.py`**

Create `social/progress.py`:

```python
from social.models import ReadingProgress


def record_progress(user, page):
    progress, _ = ReadingProgress.objects.update_or_create(
        user=user, series=page.series, defaults={"page": page}
    )
    return progress


def resume_point(user, series):
    return ReadingProgress.objects.filter(user=user, series=series).first()
```

Create `social/notifications.py`:

```python
from social.models import Notification


def _actor_user(actor):
    if actor is not None and actor.user_id:
        return actor.user
    return None


def notify(recipient, *, type, actor=None, page=None, comment=None):
    if recipient is None:
        return None
    actor_user = _actor_user(actor)
    if actor_user is not None and actor_user.pk == recipient.pk:
        return None
    return Notification.objects.create(
        recipient=recipient, type=type, actor=actor, page=page, comment=comment
    )


def mark_read(notification):
    if not notification.read:
        notification.read = True
        notification.save(update_fields=["read", "updated_at"])
    return notification


def mark_all_read(user):
    return Notification.objects.filter(recipient=user, read=False).update(read=True)
```

- [ ] **Step 5: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations social
uv run python manage.py migrate
uv run pytest tests/test_social_progress.py tests/test_social_notifications.py -q
```
Expected: PASS (6 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add social/models.py social/progress.py social/notifications.py social/migrations tests/test_social_progress.py tests/test_social_notifications.py
git commit -m "feat: ReadingProgress and Notification core"
```

---

### Task 2: New-page and follow notifications

**Files:**
- Modify: `social/notifications.py`, `comics/publishing.py`, `social/services.py`
- Test: `tests/test_social_notifications.py`

**Interfaces:**
- Consumes: Task 1 `notify`, `NotificationType`; `social.models.{Follow, FollowStatus}`; `comics.models.ComicRole`; existing `publish`, `accept_follow`.
- Produces: `social.notifications.{notify_new_page, notify_follow}` + hooks.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_notifications.py` (add to imports: `from comics.models import ComicRole`; `from comics.services import create_comic, create_page, create_series`; `from social.models import Follow, FollowStatus`; `from social.services import accept_follow`; `from tests.media_support import make_media`; `from social.follower_fixtures import scene` is unnecessary — inline a helper):

```python
def scene():
    owner = user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)
    page.refresh_from_db()
    return owner, comic, page


def _local_actor_with_user(handle, owner):
    return create_local_actor(handle, user=owner)


@pytest.mark.django_db
def test_publish_notifies_local_followers():
    owner, comic, _ = scene()
    follower = user()
    follower_actor = _local_actor_with_user(f"fan{uuid4().hex[:6]}", follower)
    Follow.objects.create(
        follower=follower_actor, target=comic.actor, status=FollowStatus.ACCEPTED
    )
    series_page = create_page(owner, comic.series.first())
    make_media(series_page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, series_page)

    assert Notification.objects.filter(
        recipient=follower, type=NotificationType.NEW_PAGE, page=series_page
    ).exists()


@pytest.mark.django_db
def test_publish_does_not_notify_remote_followers():
    owner, comic, _ = scene()
    remote_follower = remote("remotefan")
    Follow.objects.create(
        follower=remote_follower, target=comic.actor, status=FollowStatus.ACCEPTED
    )
    page = create_page(owner, comic.series.first())
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)

    assert not Notification.objects.filter(type=NotificationType.NEW_PAGE).exists()


@pytest.mark.django_db
def test_accept_follow_notifies_comic_owner():
    owner, comic, _ = scene()
    bob = remote("bob")
    follow = Follow.objects.create(
        follower=bob, target=comic.actor, status=FollowStatus.PENDING
    )
    accept_follow(follow)
    assert Notification.objects.filter(
        recipient=owner, type=NotificationType.FOLLOW, actor=bob
    ).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_notifications.py -q -k "publish or accept"`
Expected: FAIL — no notifications.

- [ ] **Step 3: Implement the generators and hooks**

Append to `social/notifications.py`, and extend its top imports to `from actors.models import Actor`, `from comics.models import ComicRole`, and `from social.models import Follow, FollowStatus, Notification, NotificationType`:

```python
def notify_new_page(page):
    comic = page.series.comic
    followers = (
        Follow.objects.filter(
            target=comic.actor,
            status=FollowStatus.ACCEPTED,
            follower__domain="",
            follower__user__isnull=False,
        )
        .select_related("follower__user")
        .order_by("created_at")
    )
    created = []
    for follow in followers:
        recipient = follow.follower.user
        if recipient.pk == page.published_by_id:
            continue
        note = notify(
            recipient,
            type=NotificationType.NEW_PAGE,
            actor=comic.actor,
            page=page,
        )
        if note is not None:
            created.append(note)
    return created


def notify_follow(follow):
    target = follow.target
    comic = getattr(target, "comic", None)
    if comic is not None:
        owner_role = (
            comic.roles.filter(role=ComicRole.Role.OWNER)
            .select_related("user")
            .first()
        )
        recipient = owner_role.user if owner_role is not None else None
    else:
        recipient = _actor_user(target)
    return notify(recipient, type=NotificationType.FOLLOW, actor=follow.follower)
```

In `comics/publishing.py`'s `publish(...)`, after the Create emission (before `return locked`), add:

```python
    from social.notifications import notify_new_page

    notify_new_page(locked)
```

In `social/services.py`'s `accept_follow(...)`, before `return follow_obj`, add:

```python
    from social.notifications import notify_follow

    notify_follow(follow_obj)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_notifications.py tests/test_social_progress.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add social/notifications.py comics/publishing.py social/services.py tests/test_social_notifications.py
git commit -m "feat: new-page and follow notifications"
```

---

### Task 3: Reply and like/boost notifications

**Files:**
- Modify: `social/notifications.py`, `social/comments.py`, `social/reactions.py`, `social/handlers.py`
- Test: `tests/test_social_notifications.py`

**Interfaces:**
- Consumes: Task 1 `notify`; `social.models.NotificationType`; existing `add_comment`/`store_inbound_reply`, reaction actions, and inbound `Like`/`Announce` handlers.
- Produces: `social.notifications.{notify_reply, notify_like, notify_boost}` + hooks.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_notifications.py` (add to imports: `from actors.services import create_local_actor` [already], `from comics.services import create_page` [already], `from social.comments import add_comment`, `from social.reactions import like_comment`, `from tests.media_support import make_media` [already]):

```python
@pytest.mark.django_db
def test_reply_to_comment_notifies_parent_author():
    owner, comic, page = scene()
    author = user()
    author_actor = create_local_actor(f"author{uuid4().hex[:6]}", user=author)
    parent = add_comment(author_actor, page, "<p>top</p>")
    replier = create_local_actor(f"replier{uuid4().hex[:6]}", user=user())
    add_comment(replier, page, "<p>reply</p>", parent=parent)
    assert Notification.objects.filter(
        recipient=author, type=NotificationType.REPLY, comment__parent=parent
    ).exists()


@pytest.mark.django_db
def test_top_level_reply_notifies_page_publisher():
    owner, comic, page = scene()
    replier = create_local_actor(f"replier{uuid4().hex[:6]}", user=user())
    add_comment(replier, page, "<p>reply</p>")
    assert Notification.objects.filter(
        recipient=owner, type=NotificationType.REPLY, page=page
    ).exists()


@pytest.mark.django_db
def test_like_on_page_notifies_publisher():
    owner, comic, page = scene()
    other = create_local_actor(f"liker{uuid4().hex[:6]}", user=user())
    from social.reactions import like

    like(other, page)
    assert Notification.objects.filter(
        recipient=owner, type=NotificationType.LIKE, page=page
    ).exists()


@pytest.mark.django_db
def test_like_on_own_page_does_not_notify():
    owner, comic, page = scene()
    owner_actor = create_local_actor(f"own{uuid4().hex[:6]}", user=owner)
    from social.reactions import like

    like(owner_actor, page)
    assert not Notification.objects.filter(type=NotificationType.LIKE).exists()


@pytest.mark.django_db
def test_like_on_comment_notifies_comment_author():
    owner, comic, page = scene()
    author = user()
    author_actor = create_local_actor(f"author{uuid4().hex[:6]}", user=author)
    comment = add_comment(author_actor, page, "<p>x</p>")
    liker = create_local_actor(f"liker{uuid4().hex[:6]}", user=user())
    like_comment(liker, comment)
    assert Notification.objects.filter(
        recipient=author, type=NotificationType.LIKE, comment=comment
    ).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_notifications.py -q -k "reply or like"`
Expected: FAIL — no reply/like notifications.

- [ ] **Step 3: Implement the generators and hooks**

Append to `social/notifications.py`:

```python
def notify_reply(comment):
    recipient = None
    if comment.parent is not None:
        recipient = _actor_user(comment.parent.actor)
    if recipient is None:
        recipient = comment.page.published_by
    return notify(
        recipient,
        type=NotificationType.REPLY,
        actor=comment.actor,
        page=comment.page,
        comment=comment,
    )


def _reaction_recipient(reaction):
    if reaction.comment_id:
        recipient = _actor_user(reaction.comment.actor)
        if recipient is not None:
            return recipient
    if reaction.page_id:
        return reaction.page.published_by
    return None


def notify_like(like):
    return notify(
        _reaction_recipient(like),
        type=NotificationType.LIKE,
        actor=like.actor,
        page=like.page,
        comment=like.comment,
    )


def notify_boost(boost):
    return notify(
        _reaction_recipient(boost),
        type=NotificationType.BOOST,
        actor=boost.actor,
        page=boost.page,
        comment=boost.comment,
    )
```

In `social/comments.py`, after creating the comment in both `add_comment` and `store_inbound_reply` (just before `return comment`), add:

```python
    from social.notifications import notify_reply

    notify_reply(comment)
```

In `social/reactions.py`, capture `created` and notify for each of `like`/`like_comment`/`boost`/`boost_comment`:

```python
    obj, created = Like.objects.get_or_create(...)  # existing call
    if created:
        from social.notifications import notify_like

        notify_like(obj)
    return obj
```

(Same pattern with `notify_boost` for `boost`/`boost_comment`.)

In `social/handlers.py`, in `handle_like`/`handle_announce`, notify when a row is newly created:

```python
    if created:
        from social.notifications import notify_like  # or notify_boost

        notify_like(like_obj)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_notifications.py tests/test_social_progress.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add social/notifications.py social/comments.py social/reactions.py social/handlers.py tests/test_social_notifications.py
git commit -m "feat: reply and like/boost notifications"
```

---

## Self-Review

**Spec coverage (Ticket 7c scope):** `Notification` for new page, reply, like/boost, new follower (§4); `ReadingProgress` per (user, series) for resume (§4/§6). Out of scope by design: the local personal feed (reader UX), email/web-push, members-page audience filtering, batching/dedup.

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `NotificationType`/`Notification` are the single notification vocabulary; `notify`/`mark_read`/`mark_all_read` are the single notification API; `record_progress`/`resume_point` are the single progress API; generators are thin wrappers over `notify`.

**Review Focus coverage:** self-notify blocked — Tasks 1–3 (`notify` self-check plus publisher/owner skips); remote never a recipient — Tasks 2, 3; reply recipients — Task 3; one notification per local follower — Task 2; progress upsert — Task 1.