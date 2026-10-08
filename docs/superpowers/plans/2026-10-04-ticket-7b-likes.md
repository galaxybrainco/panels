# Ticket 7b — Likes & Boosts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local and remote likes and boosts on comic pages, dogfooded as AP `Like`/`Announce`, with counts and inbound `Undo` handling.

**Architecture:** `social.Like` and `social.Boost` target an AP `object_id` (the Note URL) with a nullable `page` FK for counts; `social/reactions.py` owns local `like`/`unlike`/`boost`/`unboost` (local content → local row, no HTTP); `social/handlers.py` gains inbound `Like`/`Announce` handlers (gated by `federation_plan`) and extends `Undo` to Like/Announce alongside Follow. Comments (Ticket 8) re-use `object_id` and add a nullable `comment` FK.

**Tech Stack:** Django 6.1.1, PostgreSQL, existing `actors`/`federation`/`comics`/`social` primitives, pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §4 (`Follow`/`Like`/`Boost(Announce)` = Actor→object, local and remote uniform), §5 (activities `Like`, `Announce`, `Undo` for unlike/unboost), §7 (gated content never federates), §14 ticket 7.

## Global Constraints

- Synchronous Django only. Run Python via `uv run` (Python 3.14).
- One `Like`/`Boost` per `(actor, object_id)`; the target is the Note URL, with a nullable `page` FK for local resolution and counts.
- Local content interactions are dogfooded: the acting actor is local and the target is ours → store a row, **no HTTP**. Remote interactions arrive via the inbound inbox and target the same `object_id` (one code path).
- Inbound `Like`/`Announce` are stored **only** for a published page whose `federation_plan(page).emit` is true; Members/Tier, local-only, drafts, and unknown objects are ignored.
- No outbound fan-out for likes/boosts in this ticket.
- Reuse the `activity_id` correlation pattern established for `Follow` (store the original activity IRI; `Undo` matches by it for string objects).
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- Liking/boosting an unpublished page must be rejected (no Note exists). Task 1.
- A duplicate inbound `Like`/`Announce` must not create a second row. Task 2.
- An inbound `Like`/`Announce` on a Members/Tier or local-only page, or an unknown object, must store nothing. Task 2.
- `Undo{Like}`/`Undo{Announce}` must remove the row in both the embedded-dict and bare-IRI object shapes. Task 2.
- `Undo` must not regress the existing `Undo{Follow}` handling. Task 2.

## File Structure

- `social/models.py` — add `Like`, `Boost`. (Task 1)
- `social/migrations/0003_like_boost.py` — generated. (Task 1)
- `social/reactions.py` — new: `like`, `unlike`, `boost`, `unboost`, `like_count`, `boost_count`. (Task 1)
- `social/handlers.py` — add `Like`/`Announce` handlers; extend `Undo`. (Task 2)
- `tests/test_social_reactions.py` — new, extended across tasks.

---

### Task 1: `Like`/`Boost` models and local actions

**Files:**
- Modify: `social/models.py`
- Create: `social/reactions.py`, `social/migrations/0003_like_boost.py` (generated)
- Test: `tests/test_social_reactions.py`

**Interfaces:**
- Consumes: `comics.models.{Page, PageStatus}`, `core.models.UUIDModel`.
- Produces:
  - `Like` (`actor`, `object_id`, `page`, `activity_id`), `Boost` (same).
  - `social.reactions.{like, unlike, boost, unboost, like_count, boost_count}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_social_reactions.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from social.models import Boost, Like
from social.reactions import (
    boost,
    boost_count,
    like,
    like_count,
    unboost,
    unlike,
)
from tests.media_support import make_media


def published_page(**page_fields):
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series, **page_fields)
    make_media(page, position=1, alt_text="A panel")
    from comics.publishing import publish_page

    publish_page(owner, page)
    page.refresh_from_db()
    return page


@pytest.mark.django_db
def test_like_creates_row_and_count():
    actor, page = create_local_actor("alice"), published_page()
    like(actor, page)
    assert like_count(page) == 1
    assert Like.objects.get().object_id == page.ap_id
    assert Like.objects.get().page == page


@pytest.mark.django_db
def test_like_is_idempotent_per_actor_and_object():
    actor, page = create_local_actor("alice"), published_page()
    like(actor, page)
    like(actor, page)
    assert Like.objects.count() == 1


@pytest.mark.django_db
def test_unlike_removes_row():
    actor, page = create_local_actor("alice"), published_page()
    like(actor, page)
    unlike(actor, page)
    assert like_count(page) == 0


@pytest.mark.django_db
def test_boost_creates_and_unboost_removes():
    actor, page = create_local_actor("alice"), published_page()
    boost(actor, page)
    assert boost_count(page) == 1
    unboost(actor, page)
    assert boost_count(page) == 0


@pytest.mark.django_db
def test_like_requires_a_published_page():
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    draft = create_page(owner, series)
    actor = create_local_actor("alice")
    with pytest.raises(ValidationError):
        like(actor, draft)
    with pytest.raises(ValidationError):
        boost(actor, draft)


@pytest.mark.django_db
def test_like_unique_constraint():
    actor, page = create_local_actor("alice"), published_page()
    Like.objects.create(actor=actor, object_id=page.ap_id, page=page)
    with pytest.raises(IntegrityError), transaction.atomic():
        Like.objects.create(actor=actor, object_id=page.ap_id, page=page)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_reactions.py -q`
Expected: ERROR — `social.models` has no `Like`/`Boost`.

- [ ] **Step 3: Add the models**

Append to `social/models.py`:

```python
class Like(UUIDModel):
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="likes"
    )
    object_id = models.URLField()
    page = models.ForeignKey(
        "comics.Page",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="likes",
    )
    activity_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["actor", "object_id"], name="unique_like"
            )
        ]

    def __str__(self):
        return f"{self.actor} likes {self.object_id}"


class Boost(UUIDModel):
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="boosts"
    )
    object_id = models.URLField()
    page = models.ForeignKey(
        "comics.Page",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="boosts",
    )
    activity_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["actor", "object_id"], name="unique_boost"
            )
        ]

    def __str__(self):
        return f"{self.actor} boosted {self.object_id}"
```

- [ ] **Step 4: Implement `social/reactions.py`**

```python
from django.core.exceptions import ValidationError
from django.db import transaction

from comics.models import PageStatus
from social.models import Boost, Like


def _require_ap_id(page):
    if page.status != PageStatus.PUBLISHED or not page.ap_id:
        raise ValidationError("Only published pages can be liked or boosted.")
    return page.ap_id


@transaction.atomic
def like(actor, page):
    like_obj, _ = Like.objects.get_or_create(
        actor=actor, object_id=_require_ap_id(page), defaults={"page": page}
    )
    return like_obj


@transaction.atomic
def unlike(actor, page):
    if page.ap_id:
        Like.objects.filter(actor=actor, object_id=page.ap_id).delete()


@transaction.atomic
def boost(actor, page):
    boost_obj, _ = Boost.objects.get_or_create(
        actor=actor, object_id=_require_ap_id(page), defaults={"page": page}
    )
    return boost_obj


@transaction.atomic
def unboost(actor, page):
    if page.ap_id:
        Boost.objects.filter(actor=actor, object_id=page.ap_id).delete()


def like_count(page):
    return Like.objects.filter(object_id=page.ap_id).count() if page.ap_id else 0


def boost_count(page):
    return Boost.objects.filter(object_id=page.ap_id).count() if page.ap_id else 0
```

- [ ] **Step 5: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations social
uv run python manage.py migrate
uv run pytest tests/test_social_reactions.py -q
```
Expected: PASS (6 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add social/models.py social/reactions.py social/migrations tests/test_social_reactions.py
git commit -m "feat: Like and Boost models with local actions"
```

---

### Task 2: Inbound Like/Announce and Undo

**Files:**
- Modify: `social/handlers.py`
- Test: `tests/test_social_reactions.py`

**Interfaces:**
- Consumes: Task 1 `Like`/`Boost`; `comics.federation.federation_plan`; `comics.models.{Page, PageStatus}`; `federation.handlers.register`.
- Produces: handlers registered for `"Like"` and `"Announce"`; `"Undo"` extended.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_reactions.py` (add to imports: `from federation import handlers`, `from federation.activitypub import Audience`, `from federation.models import Activity, ActivityDirection`, `from social.models import Follow, FollowStatus`, `from actors.models import Actor, ActorType`):

```python
def remote(handle="bob"):
    domain = f"{handle}.test"
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
    )


def _inbound(activity_type, actor, payload):
    return Activity.objects.create(
        ap_id=payload["id"],
        type=activity_type,
        actor=actor,
        direction=ActivityDirection.INBOUND,
        payload=payload,
    )


@pytest.mark.django_db
def test_inbound_like_stores_for_published_federatable_page():
    page, bob = published_page(), remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Like", bob, payload))
    assert Like.objects.get(actor=bob).page == page


@pytest.mark.django_db
def test_inbound_like_is_idempotent():
    page, bob = published_page(), remote("bob")
    first = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Like", bob, first))
    handlers.dispatch(_inbound("Like", bob, {**first, "id": "https://bob.test/a/2"}))
    assert Like.objects.count() == 1


@pytest.mark.django_db
def test_inbound_like_ignored_for_members_page():
    page, bob = published_page(audience=Audience.MEMBERS), remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Like", bob, payload))
    assert not Like.objects.exists()


@pytest.mark.django_db
def test_inbound_like_ignored_for_unknown_object():
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Like",
        "actor": bob.ap_id,
        "object": "https://elsewhere.test/pages/nope",
    }
    handlers.dispatch(_inbound("Like", bob, payload))
    assert not Like.objects.exists()


@pytest.mark.django_db
def test_inbound_announce_stores_boost():
    page, bob = published_page(), remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Announce",
        "actor": bob.ap_id,
        "object": page.ap_id,
    }
    handlers.dispatch(_inbound("Announce", bob, payload))
    assert Boost.objects.get(actor=bob).page == page


@pytest.mark.django_db
def test_inbound_undo_like_removes_row_dict_form():
    page, bob = published_page(), remote("bob")
    Like.objects.create(
        actor=bob, object_id=page.ap_id, page=page, activity_id="https://bob.test/a/1"
    )
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": {"type": "Like", "actor": bob.ap_id, "object": page.ap_id},
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Like.objects.exists()


@pytest.mark.django_db
def test_inbound_undo_like_removes_row_string_form():
    page, bob = published_page(), remote("bob")
    Like.objects.create(
        actor=bob, object_id=page.ap_id, page=page, activity_id="https://bob.test/a/1"
    )
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": "https://bob.test/a/1",
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Like.objects.exists()


@pytest.mark.django_db
def test_inbound_undo_announce_removes_boost():
    page, bob = published_page(), remote("bob")
    Boost.objects.create(
        actor=bob, object_id=page.ap_id, page=page, activity_id="https://bob.test/a/1"
    )
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": {"type": "Announce", "actor": bob.ap_id, "object": page.ap_id},
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Boost.objects.exists()


@pytest.mark.django_db
def test_undo_follow_still_removed():
    comic, bob = create_local_actor("comic"), remote("bob")
    Follow.objects.create(follower=bob, target=comic, status=FollowStatus.ACCEPTED)
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": {"type": "Follow", "actor": bob.ap_id, "object": comic.ap_id},
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Follow.objects.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_reactions.py -q -k "inbound or undo"`
Expected: FAIL — no `Like`/`Announce` handler; `Undo{Like}`/`Undo{Announce}` not removed.

- [ ] **Step 3: Implement the handlers**

In `social/handlers.py`, add the imports and handlers, and extend `handle_undo`:

```python
from comics.federation import federation_plan
from comics.models import Page, PageStatus
from social.models import Boost, Follow, FollowStatus, Like


def _federatable_page(object_id):
    page = Page.objects.filter(ap_id=object_id, status=PageStatus.PUBLISHED).first()
    if page is None or not federation_plan(page).emit:
        return None
    return page


@handlers.register("Like")
def handle_like(activity):
    object_id = activity.payload.get("object")
    page = _federatable_page(object_id)
    if page is None:
        return
    Like.objects.get_or_create(
        actor=activity.actor,
        object_id=object_id,
        defaults={"page": page, "activity_id": activity.ap_id},
    )


@handlers.register("Announce")
def handle_announce(activity):
    object_id = activity.payload.get("object")
    page = _federatable_page(object_id)
    if page is None:
        return
    Boost.objects.get_or_create(
        actor=activity.actor,
        object_id=object_id,
        defaults={"page": page, "activity_id": activity.ap_id},
    )
```

Replace `handle_undo` with:

```python
@handlers.register("Undo")
def handle_undo(activity):
    obj = activity.payload.get("object")
    if isinstance(obj, dict):
        obj_type = obj.get("type")
        if obj_type == "Follow":
            Follow.objects.filter(
                follower=activity.actor, target__ap_id=obj.get("object")
            ).delete()
        elif obj_type == "Like":
            Like.objects.filter(
                actor=activity.actor, object_id=obj.get("object")
            ).delete()
        elif obj_type == "Announce":
            Boost.objects.filter(
                actor=activity.actor, object_id=obj.get("object")
            ).delete()
    elif isinstance(obj, str):
        Follow.objects.filter(follower=activity.actor, activity_id=obj).delete()
        Like.objects.filter(actor=activity.actor, activity_id=obj).delete()
        Boost.objects.filter(actor=activity.actor, activity_id=obj).delete()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_reactions.py -q`
Expected: PASS (15 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add social/handlers.py tests/test_social_reactions.py
git commit -m "feat: inbound Like, Announce, and Undo handling"
```

---

## Self-Review

**Spec coverage (Ticket 7b scope):** `Like`/`Boost` as Actor→object, local and remote uniform (§4); `Like`/`Announce`/`Undo` activities (§5); gated content never federated — inbound reactions only for published+federatable pages (§7); the `object_id` target keeps comments (Actor→Note, local or remote) additive for Ticket 8. Out of scope by design: outbound Announce fan-out (no user feeds), like/boost collections, comment targets (Ticket 8 adds a `comment` FK + resolver), remote-object likes.

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `Like`/`Boost` share the `(actor, object_id, page, activity_id)` shape and `(actor, object_id)` uniqueness; `social.reactions` is the single local-action path; `federation_plan` is the single gating source; `activity_id` correlation mirrors `Follow`.

**Review Focus coverage:** unpublished page rejected — Task 1; duplicate inbound idempotent — Task 2; Members/local-only/unknown ignored — Task 2; `Undo` dict + string forms — Task 2; `Undo{Follow}` regression — Task 2.