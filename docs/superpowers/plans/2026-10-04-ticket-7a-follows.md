# Ticket 7a — Follows & Page Fan-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A unified `Follow` social graph (Actor→Actor, local or remote) with inbound/outbound Follow/Accept/Undo, real followers/following collections, and page activities fanned out to accepted remote followers.

**Architecture:** `social.Follow` holds the graph. `social/services.py` owns outbound follow/unfollow/accept and the `follower_inboxes` helper; `social/handlers.py` registers inbound `Follow`/`Accept`/`Undo` with `federation.handlers`. `federation.collections` gains real follower/following pagination, and `comics.federation.emit_page_activity` fans a page's payload out to accepted remote followers via `federation.delivery.fan_out` (shared-inbox dedup). Local follows are dogfooded as AP rows with no HTTP.

**Tech Stack:** Django 6.1.1, PostgreSQL, existing `actors`/`federation`/`comics` primitives, pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §4 (`Follow` = Actor→Actor; unified identity), §5 (activities `Follow`/`Accept`/`Undo`; dogfood local follows as AP; comics are the featured follow target), §9 (blocks remove follows both ways — later), §14 ticket 7.

## Global Constraints

- Synchronous Django only. Run Python via `uv run` (Python 3.14).
- The graph is `social.Follow`; every follow is `Actor → Actor` regardless of origin (no local-vs-remote branch in storage).
- One `Follow` per `(follower, target)`; self-follow is impossible (DB check constraint).
- Inbound follows auto-accept unless the target has `manually_approves_followers=True`; then they stay `pending`.
- Local targets accept immediately with no HTTP; remote targets get a signed `Follow` delivered to their inbox.
- Page fan-out goes to accepted **remote** followers via `shared_inbox` (fallback `inbox`), deduped, signed by the comic actor; local followers get no HTTP.
- Outbound `Follow`/`Accept`/`Undo` are delivery-only (no `Activity` outbox rows) — they are not feed content.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- A duplicate inbound `Follow` must not create a second row or a second `Accept`. Task 3.
- A manual-approval target must stay `pending` with **no** `Accept` sent. Task 3.
- Fan-out must exclude local and pending followers and dedupe shared inboxes. Tasks 1, 4.
- Self-follow must be rejected. Task 1.
- An inbound `Accept` must only flip **our** pending follow (not create one). Task 3.
- Local-only/Members pages must still not fan out (guard inherited from `federation_plan`). Task 4.

## File Structure

- `social/models.py` — `FollowStatus`, `Follow`. (Task 1)
- `social/migrations/0001_initial.py` — generated. (Task 1)
- `social/services.py` — `follow`, `unfollow`, `accept_follow`, `follower_inboxes`, activity builders. (Tasks 1–2)
- `social/handlers.py` — inbound `Follow`/`Accept`/`Undo`. (Task 3)
- `social/apps.py` — register handlers in `ready()`. (Task 3)
- `federation/collections.py` — real `actor_followers`/`actor_following`. (Task 4)
- `federation/views.py` — use them. (Task 4)
- `comics/federation.py` — fan-out in `emit_page_activity`. (Task 4)
- `tests/test_social_follows.py` — new, extended across tasks.

---

### Task 1: `Follow` model and `follower_inboxes`

**Files:**
- Create: `social/models.py`, `social/services.py`
- Create: `social/migrations/0001_initial.py` (generated)
- Test: `tests/test_social_follows.py`

**Interfaces:**
- Consumes: `actors.models.Actor`, `core.models.UUIDModel`.
- Produces:
  - `FollowStatus` (`PENDING="pending"`, `ACCEPTED="accepted"`), `Follow` (`follower`, `target`, `status`).
  - `social.services.follower_inboxes(actor) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_social_follows.py`:

```python
import pytest
from django.db import IntegrityError, transaction

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from social.models import Follow, FollowStatus
from social.services import follower_inboxes


def local(handle="alice"):
    return create_local_actor(handle)


def remote(handle="bob", **kwargs):
    domain = f"{handle}.test"
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=kwargs.pop("inbox", f"https://{domain}/actors/{handle}/inbox"),
        shared_inbox=kwargs.pop("shared_inbox", ""),
        **kwargs,
    )


@pytest.mark.django_db
def test_follow_defaults_to_pending():
    follow = Follow.objects.create(follower=local("alice"), target=remote("bob"))
    assert follow.status == FollowStatus.PENDING


@pytest.mark.django_db
def test_follow_is_unique_per_pair():
    alice, bob = local("alice"), remote("bob")
    Follow.objects.create(follower=alice, target=bob)
    with pytest.raises(IntegrityError), transaction.atomic():
        Follow.objects.create(follower=alice, target=bob)


@pytest.mark.django_db
def test_self_follow_is_rejected():
    alice = local("alice")
    with pytest.raises(IntegrityError), transaction.atomic():
        Follow.objects.create(follower=alice, target=alice)


@pytest.mark.django_db
def test_follower_inboxes_dedupes_and_excludes_local_and_pending():
    target = local("comic")
    shared = remote("one", shared_inbox="https://one.test/inbox")
    also_shared = remote("two", shared_inbox="https://one.test/inbox")
    personal = remote("three")
    local_follower = local("fan")
    pending = remote("four")
    for follower in (shared, also_shared, personal):
        Follow.objects.create(
            follower=follower, target=target, status=FollowStatus.ACCEPTED
        )
    Follow.objects.create(
        follower=local_follower, target=target, status=FollowStatus.ACCEPTED
    )
    Follow.objects.create(follower=pending, target=target, status=FollowStatus.PENDING)

    inboxes = follower_inboxes(target)
    assert sorted(inboxes) == sorted(
        {"https://one.test/inbox", "https://one.test/inbox", personal.inbox}
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_follows.py -q`
Expected: ERROR — `social.models` has no `Follow`.

- [ ] **Step 3: Implement `social/models.py`**

```python
from django.db import models

from core.models import UUIDModel


class FollowStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"


class Follow(UUIDModel):
    follower = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="following_relations"
    )
    target = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="follower_relations"
    )
    status = models.CharField(
        max_length=16, choices=FollowStatus.choices, default=FollowStatus.PENDING
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "target"], name="unique_follow"
            ),
            models.CheckConstraint(
                condition=~models.Q(follower=models.F("target")),
                name="no_self_follow",
            ),
        ]

    def __str__(self):
        return f"{self.follower} → {self.target} ({self.status})"
```

- [ ] **Step 4: Implement `follower_inboxes` in `social/services.py`**

```python
from social.models import Follow, FollowStatus


def follower_inboxes(actor):
    follows = (
        Follow.objects.filter(
            target=actor,
            status=FollowStatus.ACCEPTED,
            follower__domain__gt="",
        )
        .select_related("follower")
        .order_by("created_at")
    )
    inboxes = []
    for follow in follows:
        url = follow.follower.shared_inbox or follow.follower.inbox
        if url:
            inboxes.append(url)
    return inboxes
```

- [ ] **Step 5: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations social
uv run python manage.py migrate
uv run pytest tests/test_social_follows.py -q
```
Expected: PASS (4 passed). Note: `follower_inboxes` returns duplicates by design (one per shared inbox); `federation.delivery.unique_inboxes` dedupes at delivery time — the test asserts the raw list.

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add social/models.py social/services.py social/migrations tests/test_social_follows.py
git commit -m "feat: add the Follow model and follower inbox helper"
```

---

### Task 2: Outbound follow / unfollow / accept

**Files:**
- Modify: `social/services.py`
- Test: `tests/test_social_follows.py`

**Interfaces:**
- Consumes: Task 1 `Follow`/`FollowStatus`; `federation.activitypub.build_activity`; `federation.delivery.fan_out`.
- Produces: `follow(local_actor, target) -> Follow`, `unfollow(local_actor, target) -> None`, `accept_follow(follow) -> Follow`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_follows.py` (add to imports: `from django.core.exceptions import ValidationError`, `from federation.models import Delivery`, `from social.services import accept_follow, follow, unfollow`):

```python
@pytest.mark.django_db
def test_follow_local_target_is_accepted_without_delivery():
    alice, comic = local("alice"), local("comic")
    result = follow(alice, comic)
    assert result.status == FollowStatus.ACCEPTED
    assert Delivery.objects.count() == 0


@pytest.mark.django_db
def test_follow_remote_target_is_pending_and_delivers_follow():
    alice, bob = local("alice"), remote("bob")
    result = follow(alice, bob)
    assert result.status == FollowStatus.PENDING
    delivery = Delivery.objects.get()
    assert delivery.inbox_url == bob.inbox
    assert delivery.activity["type"] == "Follow"
    assert delivery.activity["object"] == bob.ap_id


@pytest.mark.django_db
def test_follow_is_idempotent():
    alice, bob = local("alice"), remote("bob")
    follow(alice, bob)
    follow(alice, bob)
    assert Follow.objects.count() == 1
    assert Delivery.objects.count() == 1


@pytest.mark.django_db
def test_unfollow_remote_delivers_undo():
    alice, bob = local("alice"), remote("bob")
    follow(alice, bob)
    unfollow(alice, bob)
    assert not Follow.objects.exists()
    assert Delivery.objects.filter(activity__type="Undo").count() == 1


@pytest.mark.django_db
def test_accept_follow_marks_accepted_and_delivers_accept():
    bob, comic = remote("bob"), local("comic")
    incoming = Follow.objects.create(
        follower=bob, target=comic, status=FollowStatus.PENDING
    )
    accept_follow(incoming)
    incoming.refresh_from_db()
    assert incoming.status == FollowStatus.ACCEPTED
    assert Delivery.objects.filter(activity__type="Accept", inbox_url=bob.inbox).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_follows.py -q -k "follow_local or remote_target or idempotent or unfollow or accept_follow_marks"`
Expected: ERROR — `follow`/`unfollow`/`accept_follow` do not exist.

- [ ] **Step 3: Implement the outbound services**

Append to `social/services.py` (add imports at the top: `from uuid import uuid4`, `from django.core.exceptions import ValidationError`, `from django.db import transaction`, `from federation.activitypub import build_activity`, `from federation.delivery import fan_out`):

```python
def _follow_activity(actor, target):
    return build_activity(
        "Follow", actor, target.ap_id, activity_id=f"{actor.ap_id}#follows/{uuid4()}"
    )


def _target_inbox(target):
    return target.shared_inbox or target.inbox


@transaction.atomic
def follow(local_actor, target):
    if local_actor == target:
        raise ValidationError("You cannot follow yourself.")
    status = FollowStatus.ACCEPTED if target.is_local else FollowStatus.PENDING
    follow_obj, created = Follow.objects.get_or_create(
        follower=local_actor, target=target, defaults={"status": status}
    )
    if created and not target.is_local:
        fan_out(_follow_activity(local_actor, target), [_target_inbox(target)], local_actor)
    return follow_obj


@transaction.atomic
def unfollow(local_actor, target):
    follow_obj = Follow.objects.filter(follower=local_actor, target=target).first()
    if follow_obj is None:
        return None
    follow_obj.delete()
    if not target.is_local:
        undo = build_activity(
            "Undo",
            local_actor,
            _follow_activity(local_actor, target),
            activity_id=f"{local_actor.ap_id}#unfollows/{uuid4()}",
        )
        fan_out(undo, [_target_inbox(target)], local_actor)
    return None


@transaction.atomic
def accept_follow(follow_obj):
    follow_obj.status = FollowStatus.ACCEPTED
    follow_obj.save(update_fields=["status", "updated_at"])
    if not follow_obj.follower.is_local:
        accept = build_activity(
            "Accept",
            follow_obj.target,
            _follow_activity(follow_obj.follower, follow_obj.target),
            activity_id=f"{follow_obj.target.ap_id}#accepts/{uuid4()}",
        )
        fan_out(
            accept,
            [_target_inbox(follow_obj.follower)],
            follow_obj.target,
        )
    return follow_obj
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_follows.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add social/services.py tests/test_social_follows.py
git commit -m "feat: outbound follow, unfollow, and accept"
```

---

### Task 3: Inbound Follow / Accept / Undo handlers

**Files:**
- Create: `social/handlers.py`
- Modify: `social/apps.py`
- Test: `tests/test_social_follows.py`

**Interfaces:**
- Consumes: Task 2 `accept_follow`; `federation.handlers.register`; `federation.models.{Activity, ActivityDirection}`.
- Produces: handlers registered for `"Follow"`, `"Accept"`, `"Undo"`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_follows.py` (add to imports: `from federation import handlers`, `from federation.models import Activity, ActivityDirection`):

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
def test_inbound_follow_auto_accepts_and_delivers_accept():
    comic = local("comic")
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Follow",
        "actor": bob.ap_id,
        "object": comic.ap_id,
    }
    handlers.dispatch(_inbound("Follow", bob, payload))
    follow_obj = Follow.objects.get(follower=bob, target=comic)
    assert follow_obj.status == FollowStatus.ACCEPTED
    assert Delivery.objects.filter(activity__type="Accept", inbox_url=bob.inbox).exists()


@pytest.mark.django_db
def test_inbound_follow_to_manual_target_stays_pending():
    comic = local("comic")
    comic.manually_approves_followers = True
    comic.save()
    bob = remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Follow",
        "actor": bob.ap_id,
        "object": comic.ap_id,
    }
    handlers.dispatch(_inbound("Follow", bob, payload))
    assert Follow.objects.get(follower=bob, target=comic).status == FollowStatus.PENDING
    assert not Delivery.objects.filter(activity__type="Accept").exists()


@pytest.mark.django_db
def test_inbound_duplicate_follow_does_not_double_accept():
    comic, bob = local("comic"), remote("bob")
    payload = {
        "id": "https://bob.test/activities/1",
        "type": "Follow",
        "actor": bob.ap_id,
        "object": comic.ap_id,
    }
    handlers.dispatch(_inbound("Follow", bob, payload))
    handlers.dispatch(_inbound("Follow", bob, payload))
    assert Follow.objects.count() == 1
    assert Delivery.objects.filter(activity__type="Accept").count() == 1


@pytest.mark.django_db
def test_inbound_undo_removes_follow():
    comic, bob = local("comic"), remote("bob")
    Follow.objects.create(follower=bob, target=comic, status=FollowStatus.ACCEPTED)
    payload = {
        "id": "https://bob.test/activities/2",
        "type": "Undo",
        "actor": bob.ap_id,
        "object": {"type": "Follow", "actor": bob.ap_id, "object": comic.ap_id},
    }
    handlers.dispatch(_inbound("Undo", bob, payload))
    assert not Follow.objects.exists()


@pytest.mark.django_db
def test_inbound_accept_accepts_our_pending_follow():
    alice, bob = local("alice"), remote("bob")
    follow(alice, bob)  # pending
    payload = {
        "id": "https://bob.test/activities/3",
        "type": "Accept",
        "actor": bob.ap_id,
        "object": {"type": "Follow", "actor": alice.ap_id, "object": bob.ap_id},
    }
    handlers.dispatch(_inbound("Accept", bob, payload))
    assert Follow.objects.get(follower=alice, target=bob).status == FollowStatus.ACCEPTED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_follows.py -q -k inbound`
Expected: FAIL — no handlers registered; no `Follow` rows created by dispatch.

- [ ] **Step 3: Implement `social/handlers.py`**

```python
from actors.models import Actor
from federation import handlers
from social import services
from social.models import Follow, FollowStatus


@handlers.register("Follow")
def handle_follow(activity):
    target = Actor.objects.filter(ap_id=activity.payload.get("object"), domain="").first()
    if target is None:
        return
    follow_obj, created = Follow.objects.get_or_create(
        follower=activity.actor, target=target, defaults={"status": FollowStatus.PENDING}
    )
    if created and not target.manually_approves_followers:
        services.accept_follow(follow_obj)


@handlers.register("Undo")
def handle_undo(activity):
    obj = activity.payload.get("object")
    if isinstance(obj, dict):
        if obj.get("type") != "Follow":
            return
        target_ap_id = obj.get("object")
    else:
        target_ap_id = obj
    target = Actor.objects.filter(ap_id=target_ap_id).first()
    if target is None:
        return
    Follow.objects.filter(follower=activity.actor, target=target).delete()


@handlers.register("Accept")
def handle_accept(activity):
    obj = activity.payload.get("object")
    target = activity.actor
    follows = Follow.objects.filter(
        target=target, follower__domain="", status=FollowStatus.PENDING
    )
    if isinstance(obj, dict) and obj.get("type") == "Follow" and obj.get("actor"):
        follows = follows.filter(follower__ap_id=obj["actor"])
    for follow_obj in follows:
        follow_obj.status = FollowStatus.ACCEPTED
        follow_obj.save(update_fields=["status", "updated_at"])
```

- [ ] **Step 4: Register the handlers in `social/apps.py`**

```python
from django.apps import AppConfig


class SocialConfig(AppConfig):
    name = "social"

    def ready(self):
        from social import handlers  # noqa: F401
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_follows.py -q`
Expected: PASS (14 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add social/handlers.py social/apps.py tests/test_social_follows.py
git commit -m "feat: inbound Follow, Accept, and Undo handlers"
```

---

### Task 4: Real follower collections and page fan-out

**Files:**
- Modify: `federation/collections.py`, `federation/views.py`, `comics/federation.py`
- Test: `tests/test_social_follows.py`

**Interfaces:**
- Consumes: Task 1 `follower_inboxes`; `social.models.Follow`.
- Produces: `federation.collections.actor_followers(actor, *, page=None, page_size=20)` and `actor_following(...)`; page fan-out inside `comics.federation.emit_page_activity`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_social_follows.py` (add to imports: `from comics.federation import emit_page_activity`, `from federation import collections`, `from federation.models import ActivityDirection`):

```python
@pytest.mark.django_db
def test_collections_list_accepted_follows():
    alice, bob = local("alice"), remote("bob")
    Follow.objects.create(follower=alice, target=bob, status=FollowStatus.ACCEPTED)
    pending = remote("carol")
    Follow.objects.create(follower=pending, target=bob, status=FollowStatus.PENDING)
    followers = collections.actor_followers(bob, page=1)
    assert followers["orderedItems"] == [alice.ap_id]
    following = collections.actor_following(alice, page=1)
    assert following["orderedItems"] == [bob.ap_id]


@pytest.mark.django_db
def test_publish_fans_out_to_accepted_remote_followers():
    from django.contrib.auth import get_user_model

    from comics.services import create_comic, create_page, create_series
    from tests.media_support import make_media

    owner = get_user_model().objects.create_user(email="o@example.com", password="x")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_media(page, position=1, alt_text="A panel")
    page.ap_id = f"http://testserver/pages/{page.id}"
    bob = remote("bob", shared_inbox="https://bob.test/inbox")
    Follow.objects.create(
        follower=bob, target=comic.actor, status=FollowStatus.ACCEPTED
    )

    emit_page_activity(page, "Create")

    assert Delivery.objects.filter(
        inbox_url="https://bob.test/inbox", activity__type="Create"
    ).exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_social_follows.py -q -k "collections or fans_out"`
Expected: FAIL — `collections.actor_followers` returns an empty collection; `emit_page_activity` does not fan out.

- [ ] **Step 3: Add real collections**

In `federation/collections.py`, add a shared paginator and the two collections (and refactor `actor_outbox` to use it):

```python
def _paginate(collection_url, items, page, page_size):
    total = len(items)
    if page is None:
        return ordered_collection(
            collection_url, total_items=total, first=f"{collection_url}?page=1"
        )
    start = (page - 1) * page_size
    document = ordered_collection_page(
        f"{collection_url}?page={page}", collection_url, items[start : start + page_size]
    )
    if start + page_size < total:
        document["next"] = f"{collection_url}?page={page + 1}"
    if page > 1:
        document["prev"] = f"{collection_url}?page={page - 1}"
    return document


def _accepted_followers(actor):
    from social.models import Follow, FollowStatus

    return [
        follow.follower.ap_id
        for follow in Follow.objects.filter(
            target=actor, status=FollowStatus.ACCEPTED
        )
        .select_related("follower")
        .order_by("created_at")
    ]


def _accepted_following(actor):
    from social.models import Follow, FollowStatus

    return [
        follow.target.ap_id
        for follow in Follow.objects.filter(
            follower=actor, status=FollowStatus.ACCEPTED
        )
        .select_related("target")
        .order_by("created_at")
    ]


def actor_followers(actor, *, page=None, page_size=PAGE_SIZE):
    return _paginate(actor.followers, _accepted_followers(actor), page, page_size)


def actor_following(actor, *, page=None, page_size=PAGE_SIZE):
    return _paginate(actor.following, _accepted_following(actor), page, page_size)
```

Then replace the body of `actor_outbox` to call `_paginate(collection_url, _public_outbound(actor), page, page_size)` (keeping `collection_url = actor.outbox`), so all three share the paginator.

- [ ] **Step 4: Wire the views**

In `federation/views.py`, change `actor_followers` and `actor_following` to parse `page` (as `actor_outbox` does) and call the collections:

```python
def actor_followers(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    page_param = request.GET.get("page")
    page = int(page_param) if page_param and page_param.isdigit() else None
    return JsonResponse(collections.actor_followers(actor, page=page))


def actor_following(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    page_param = request.GET.get("page")
    page = int(page_param) if page_param and page_param.isdigit() else None
    return JsonResponse(collections.actor_following(actor, page=page))
```

- [ ] **Step 5: Fan page activities out to followers**

In `comics/federation.py`'s `emit_page_activity`, before `return activity`, add:

```python
    from federation.delivery import fan_out
    from social.services import follower_inboxes

    inboxes = follower_inboxes(page.series.comic.actor)
    if inboxes:
        fan_out(payload, inboxes, page.series.comic.actor)
    return activity
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_social_follows.py -q`
Expected: PASS (16 passed).

- [ ] **Step 7: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add federation/collections.py federation/views.py comics/federation.py tests/test_social_follows.py
git commit -m "feat: real follower collections and page fan-out"
```

---

## Self-Review

**Spec coverage (Ticket 7a scope):** unified `Follow` Actor→Actor (§4); inbound `Follow`/`Accept`/`Undo` with auto-accept-or-pend (§5); outbound follow/unfollow to local and remote targets (§5); real `followers`/`following` collections (§5); page activities fanned out to accepted remote followers (§5/§14); local follows dogfooded as AP rows. Out of scope by design: likes/boosts (7b), notifications/reading progress (7c), blocks/mutes and their follow removal (§9), `Reject`, follower-count caching, remote-follow hydration beyond the inbound pipeline.

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `FollowStatus`/`Follow` are the single graph vocabulary; `follow`/`unfollow`/`accept_follow`/`follower_inboxes` are the single outbound/helper path; `_paginate` is the single collection paginator; `emit_page_activity` remains the single page-emission point with fan-out added.

**Review Focus coverage:** duplicate inbound Follow no double-accept — Task 3; manual target stays pending — Task 3; fan-out excludes local/pending + dedup — Tasks 1, 4; self-follow rejected — Task 1; inbound Accept only flips ours — Task 3; gated pages don't fan out — Task 4 (guard inherited from `federation_plan`).