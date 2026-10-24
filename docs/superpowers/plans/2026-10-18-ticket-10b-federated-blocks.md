# Federated Blocks (10b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Users block actors: outbound `Block`/`Undo Block` from their personal actor, inbound block storage and enforcement, and notification/feed hiding.

**Architecture:** `moderation.Block` links two actors. `moderation/blocks.py` owns block/unblock (including federation delivery through the existing `build_activity` + `fan_out` seam) and inbound recording. A split `moderation/handlers.py` handles inbound `Block`, `social.handlers.handle_undo` gains a `Block` branch, `process_inbox` drops activities from blocked actors (except `Undo`), and `notification_allowed` gains a block check.

**Tech Stack:** Python 3.14, Django 6.1.1, pytests with `monkeypatch` for delivery capture.

**Spec:** `docs/superpowers/specs/2026-10-18-federated-blocks-design.md`

## Global Constraints

- Blocks are core safety behavior: no feature flag.
- Only the user's personal actor (`Actor.user == user`) can originate a local block; no personal actor → 409 `no_actor`.
- Blocking a remote target emits exactly one `Block`; unblocking emits `Undo Block`. Local targets emit nothing.
- Blocks never delete likes, boosts, or comments; only follow rows are removed.
- Inbound activities from an actor with any block involving a local actor are dropped with 202 and not stored, except `Block` and `Undo`.
- Keep `ruff check .` and `ruff format --check .` clean; run `uv run python manage.py makemigrations --check --dry-run` before final commits.

## Review Focus

- **Federation correctness:** Block/Undo payload shape and ids, target inbox selection, exactly-once emission, idempotent local block, no emission for local targets.
- **Enforcement completeness:** inbound gate covers both block directions, allows `Undo`, and runs before storage/dispatch; inbound Block/Undo mutate the right rows.
- **No destructive side effects:** only follows are deleted; notifications/likes/boosts/comments remain.
- **Identity boundaries:** no personal actor, self-block, unknown actors, and non-local blocked targets.
- **Notification hiding in both directions:** user→actor and actor→user blocks suppress.

---

### Task 1: Block model, services, and outbound activities

**Files:**
- Modify: `moderation/models.py`
- Create: `moderation/migrations/0002_block.py` (generated)
- Create: `moderation/blocks.py`
- Create: `tests/test_moderation_blocks.py`

**Interfaces:**
- Consumes: `Actor`, `Follow`, `build_activity`, `fan_out`.
- Produces: `Block`; `blocks.NoActorError`, `blocks.SelfBlockError`; `personal_actor`, `block_actor`, `unblock_actor`, `record_inbound_block`, `handle_undo_block`, `has_block_with_local`, `should_drop_inbound`, `is_blocked`, `hidden_actor_ids`.

- [ ] **Step 1: Write the failing service tests**

Create `tests/test_moderation_blocks.py`:

```python
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from federation.activitypub import build_activity
from moderation import blocks
from moderation.models import Block
from social.models import Follow, FollowStatus


def _user():
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


def _personal(user):
    return create_local_actor(f"u{uuid4().hex[:8]}", user=user)


def _remote(handle="bob", domain="remote.test"):
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        inbox=f"https://{domain}/actors/{handle}/inbox",
        shared_inbox=f"https://{domain}/inbox",
    )


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        blocks, "fan_out", lambda payload, inboxes, actor: calls.append((payload, inboxes))
    )
    return calls


@pytest.mark.django_db
def test_block_removes_follows_both_directions(sent):
    user = _user()
    me = _personal(user)
    target = _remote()
    Follow.objects.create(follower=me, target=target, status=FollowStatus.PENDING)
    Follow.objects.create(follower=target, target=me, status=FollowStatus.ACCEPTED)

    block = blocks.block_actor(user, target)

    assert block.blocker == me
    assert block.blocked == target
    assert not Follow.objects.filter(follower=me, target=target).exists()
    assert not Follow.objects.filter(follower=target, target=me).exists()


@pytest.mark.django_db
def test_block_remote_emits_one_block(sent):
    user = _user()
    me = _personal(user)
    target = _remote()

    block = blocks.block_actor(user, target)
    blocks.block_actor(user, target)

    assert len(sent) == 1
    payload, inboxes = sent[0]
    assert payload["type"] == "Block"
    assert payload["actor"] == me.ap_id
    assert payload["object"] == target.ap_id
    assert payload["id"].startswith(f"{me.ap_id}#blocks/")
    assert inboxes == ["https://remote.test/inbox"]
    assert block.activity_id == payload["id"]


@pytest.mark.django_db
def test_block_local_emits_nothing(sent):
    user = _user()
    me = _personal(user)
    other = _personal(_user())

    block = blocks.block_actor(user, other)

    assert sent == []
    assert block.activity_id == ""


@pytest.mark.django_db
def test_unblock_remote_emits_undo_and_deletes(sent):
    user = _user()
    me = _personal(user)
    target = _remote()
    block = blocks.block_actor(user, target)
    sent.clear()

    assert blocks.unblock_actor(user, target) is True

    assert not Block.objects.filter(pk=block.pk).exists()
    assert len(sent) == 1
    payload, _ = sent[0]
    assert payload["type"] == "Undo"
    assert payload["object"]["type"] == "Block"
    assert payload["object"]["id"] == block.activity_id
    assert payload["object"]["object"] == target.ap_id
    assert blocks.unblock_actor(user, target) is False
    assert sent[0] == payload


@pytest.mark.django_db
def test_block_requires_personal_actor_and_rejects_self():
    user = _user()
    target = _remote()
    with pytest.raises(blocks.NoActorError):
        blocks.block_actor(user, target)
    me = _personal(user)
    with pytest.raises(blocks.SelfBlockError):
        blocks.block_actor(user, me)


@pytest.mark.django_db
def test_block_helpers_cover_both_directions(sent):
    user = _user()
    me = _personal(user)
    target = _remote()
    blocker = _remote(handle="blocker")

    assert blocks.has_block_with_local(target) is False
    blocks.block_actor(user, target)
    assert blocks.has_block_with_local(target) is True
    assert blocks.is_blocked(user, target) is True
    assert target.id in blocks.hidden_actor_ids(user)

    Block.objects.create(blocker=blocker, blocked=me)
    assert blocks.has_block_with_local(blocker) is True
    assert blocks.is_blocked(user, blocker) is True
    assert blocker.id in blocks.hidden_actor_ids(user)


@pytest.mark.django_db
def test_should_drop_inbound_allows_undo(sent):
    user = _user()
    _personal(user)
    target = _remote()
    blocks.block_actor(user, target)
    assert blocks.should_drop_inbound(target, "Create") is True
    assert blocks.should_drop_inbound(target, "Undo") is False
    assert blocks.should_drop_inbound(_remote(handle="other"), "Create") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_moderation_blocks.py -q`
Expected: `ImportError: cannot import name 'blocks'`.

- [ ] **Step 3: Add the model and migration**

In `moderation/models.py`:

```python
class Block(UUIDModel):
    blocker = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="blocks_made"
    )
    blocked = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="blocked_by"
    )
    activity_id = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["blocker", "blocked"], name="unique_block"
            ),
            models.CheckConstraint(
                condition=~models.Q(blocker=models.F("blocked")),
                name="no_self_block",
            ),
        ]
```

Run: `uv run python manage.py makemigrations moderation`
Expected: `moderation/migrations/0002_block.py` (rename if generated differently).

- [ ] **Step 4: Implement the services**

Create `moderation/blocks.py`:

```python
from uuid import uuid4

from django.db import transaction
from django.db.models import Q

from actors.models import Actor
from federation.activitypub import build_activity
from federation.delivery import fan_out
from moderation.models import Block
from social.models import Follow


class NoActorError(Exception):
    pass


class SelfBlockError(Exception):
    pass


def personal_actor(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return Actor.objects.filter(user=user).first()


def _remove_follows(left, right):
    Follow.objects.filter(
        Q(follower=left, target=right) | Q(follower=right, target=left)
    ).delete()


def _inbox(actor):
    return actor.shared_inbox or actor.inbox


def block_actor(user, target):
    actor = personal_actor(user)
    if actor is None:
        raise NoActorError
    if actor == target:
        raise SelfBlockError
    with transaction.atomic():
        block, created = Block.objects.get_or_create(blocker=actor, blocked=target)
        _remove_follows(actor, target)
        if created and not target.is_local:
            activity_id = f"{actor.ap_id}#blocks/{uuid4()}"
            payload = build_activity(
                "Block", actor, target.ap_id, activity_id=activity_id
            )
            fan_out(payload, [_inbox(target)], actor)
            block.activity_id = activity_id
            block.save(update_fields=["activity_id", "updated_at"])
    return block


def unblock_actor(user, target):
    actor = personal_actor(user)
    if actor is None:
        raise NoActorError
    block = Block.objects.filter(blocker=actor, blocked=target).first()
    if block is None:
        return False
    with transaction.atomic():
        if not target.is_local:
            block_activity = {
                "id": block.activity_id or f"{actor.ap_id}#blocks/{uuid4()}",
                "type": "Block",
                "actor": actor.ap_id,
                "object": target.ap_id,
            }
            undo = build_activity(
                "Undo",
                actor,
                block_activity,
                activity_id=f"{actor.ap_id}#unblocks/{uuid4()}",
            )
            fan_out(undo, [_inbox(target)], actor)
        block.delete()
    return True


def _object_ap_id(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("id") or value.get("object") or ""
    return ""


def record_inbound_block(activity):
    ap_id = _object_ap_id(activity.payload.get("object"))
    blocked = Actor.objects.filter(ap_id=ap_id, domain="").first() if ap_id else None
    if blocked is None:
        return None
    with transaction.atomic():
        block, created = Block.objects.get_or_create(
            blocker=activity.actor,
            blocked=blocked,
            defaults={"activity_id": activity.ap_id},
        )
        if not created and not block.activity_id:
            block.activity_id = activity.ap_id
            block.save(update_fields=["activity_id", "updated_at"])
        _remove_follows(activity.actor, blocked)
    return block


def handle_undo_block(activity, obj):
    blocked_ap_id = _object_ap_id(obj.get("object"))
    activity_id = obj.get("id") or ""
    query = Block.objects.filter(blocker=activity.actor)
    if blocked_ap_id:
        query = query.filter(blocked__ap_id=blocked_ap_id)
    elif activity_id:
        query = query.filter(activity_id=activity_id)
    else:
        return 0
    return query.delete()[0]


def has_block_with_local(actor):
    if actor is None:
        return False
    return Block.objects.filter(
        Q(blocker=actor, blocked__domain="") | Q(blocker__domain="", blocked=actor)
    ).exists()


def should_drop_inbound(actor, activity_type):
    if activity_type in {"Undo", "Block"}:
        return False
    return has_block_with_local(actor)


def is_blocked(user, actor):
    me = personal_actor(user)
    if me is None or actor is None:
        return False
    return Block.objects.filter(
        Q(blocker=me, blocked=actor) | Q(blocker=actor, blocked=me)
    ).exists()


def hidden_actor_ids(user):
    me = personal_actor(user)
    if me is None:
        return []
    ids = set(Block.objects.filter(blocker=me).values_list("blocked_id", flat=True))
    ids.update(Block.objects.filter(blocked=me).values_list("blocker_id", flat=True))
    return list(ids)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_moderation_blocks.py -q`
Expected: `7 passed`

- [ ] **Step 6: Lint, migration check, and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
uv run python manage.py makemigrations --check --dry-run
git add moderation/models.py moderation/migrations/0002_block.py moderation/blocks.py tests/test_moderation_blocks.py
git commit -m "feat: add federated block model and outbound activities"
```

---

### Task 2: Inbound block handling, inbox gate, and notification hiding

**Files:**
- Create: `moderation/handlers.py`
- Modify: `moderation/apps.py`
- Modify: `social/handlers.py`
- Modify: `federation/inbound.py`
- Modify: `moderation/services.py`
- Modify: `tests/test_moderation_blocks.py`

**Interfaces:**
- Consumes: Task 1 helpers, `federation.handlers.register`, `Activity`.
- Produces: inbound `Block` handler; `Undo` Block branch; `process_inbox` gate via `blocks.should_drop_inbound`; `notification_allowed` block check.

- [ ] **Step 1: Add failing handler and gate tests**

Append to `tests/test_moderation_blocks.py`:

```python
import json

from federation.models import Activity
from moderation.services import notification_allowed
from social.models import NotificationType


def _activity(actor, payload):
    return Activity.objects.create(
        ap_id=payload["id"],
        type=payload.get("type", ""),
        actor=actor,
        payload=payload,
    )


@pytest.mark.django_db
def test_inbound_block_stores_and_removes_follows(sent):
    me = _personal(_user())
    remote = _remote()
    Follow.objects.create(follower=remote, target=me, status=FollowStatus.ACCEPTED)
    payload = {
        "id": "https://remote.test/blocks/1",
        "type": "Block",
        "actor": remote.ap_id,
        "object": me.ap_id,
    }
    activity = _activity(remote, payload)

    from moderation.handlers import handle_block

    handle_block(activity)

    block = Block.objects.get(blocker=remote, blocked=me)
    assert block.activity_id == payload["id"]
    assert not Follow.objects.filter(follower=remote, target=me).exists()


@pytest.mark.django_db
def test_inbound_undo_block_removes_row(sent):
    me = _personal(_user())
    remote = _remote()
    Block.objects.create(
        blocker=remote, blocked=me, activity_id="https://remote.test/blocks/1"
    )
    payload = {
        "id": "https://remote.test/undos/1",
        "type": "Undo",
        "actor": remote.ap_id,
        "object": {
            "id": "https://remote.test/blocks/1",
            "type": "Block",
            "actor": remote.ap_id,
            "object": me.ap_id,
        },
    }
    activity = _activity(remote, payload)

    from moderation.blocks import handle_undo_block

    handle_undo_block(activity, payload["object"])

    assert not Block.objects.filter(blocker=remote, blocked=me).exists()


@pytest.mark.django_db
def test_inbound_block_of_unknown_actor_is_ignored(sent):
    remote = _remote()
    payload = {
        "id": "https://remote.test/blocks/2",
        "type": "Block",
        "actor": remote.ap_id,
        "object": "https://elsewhere.test/actors/nobody",
    }
    from moderation.blocks import record_inbound_block

    assert record_inbound_block(_activity(remote, payload)) is None


@pytest.mark.django_db
def test_notification_gate_suppresses_blocks_both_directions(sent):
    user = _user()
    me = _personal(user)
    target = _remote()
    blocks.block_actor(user, target)
    assert (
        notification_allowed(user, actor=target, page=None) is False
    )

    blocker = _remote(handle="blocker")
    Block.objects.create(blocker=blocker, blocked=me)
    assert (
        notification_allowed(user, actor=blocker, page=None) is False
    )
    assert (
        notification_allowed(user, actor=_remote(handle="fine"), page=None) is True
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_moderation_blocks.py -q -k "inbound or notification_gate"`
Expected: import/handler failures and block notifications not suppressed.

- [ ] **Step 3: Register the inbound Block handler**

Create `moderation/handlers.py`:

```python
from federation import handlers
from moderation import blocks


@handlers.register("Block")
def handle_block(activity):
    return blocks.record_inbound_block(activity)
```

In `moderation/apps.py`:

```python
from django.apps import AppConfig


class ModerationConfig(AppConfig):
    name = "moderation"

    def ready(self):
        from moderation import handlers  # noqa: F401
```

- [ ] **Step 4: Handle Undo Block and gate the inbox**

In `social/handlers.py`, inside `handle_undo`'s dict branch after the
`Announce` case:

```python
        elif obj_type == "Block":
            from moderation import blocks as block_services

            block_services.handle_undo_block(activity, obj)
```

In `federation/inbound.py`, add the import:

```python
from moderation import blocks
```

and the gate after the actor throttle check in `process_inbox`:

```python
    if blocks.should_drop_inbound(actor, document.get("type")):
        return JsonResponse({"accepted": True}, status=202)
```

- [ ] **Step 5: Hide blocked actors from notifications**

In `moderation/services.py`, import the block helpers and add the first check in
`notification_allowed`:

```python
from moderation import blocks as block_services
```

```python
def notification_allowed(user, *, actor=None, page=None, comment=None):
    if user is None:
        return True
    if block_services.is_blocked(user, actor):
        return False
    mute = active_mute(user, actor)
```

- [ ] **Step 6: Run the moderation and federation suites**

Run: `uv run pytest tests/test_moderation_blocks.py tests/test_moderation.py tests/test_social_notifications.py tests/test_federation_inbox.py -q`
Expected: all pass.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add moderation/handlers.py moderation/apps.py social/handlers.py federation/inbound.py moderation/services.py tests/test_moderation_blocks.py
git commit -m "feat: enforce blocks in the inbox and notifications"
```

---

### Task 3: Block endpoint

**Files:**
- Modify: `moderation/views.py`
- Modify: `moderation/urls.py`
- Modify: `tests/test_moderation_blocks.py`

**Interfaces:**
- Consumes: `blocks.block_actor`, `blocks.unblock_actor`, errors.
- Produces: `views.blocks`; URL name `moderation-blocks`, path
  `/moderation/blocks/<uuid:actor_id>`.

- [ ] **Step 1: Add failing endpoint tests**

Append to `tests/test_moderation_blocks.py`:

```python
@pytest.mark.django_db
def test_block_endpoint_round_trip(client, sent):
    user = _user()
    _personal(user)
    target = _remote()
    client.force_login(user)

    response = client.post(f"/moderation/blocks/{target.id}")
    assert response.status_code == 200
    assert response.json() == {"blocked": True}
    assert Block.objects.filter(blocked=target).exists()

    response = client.delete(f"/moderation/blocks/{target.id}")
    assert response.status_code == 200
    assert response.json() == {"blocked": False}
    assert not Block.objects.filter(blocked=target).exists()


@pytest.mark.django_db
def test_block_endpoint_requires_personal_actor(client):
    user = _user()
    target = _remote()
    client.force_login(user)
    response = client.post(f"/moderation/blocks/{target.id}")
    assert response.status_code == 409
    assert response.json() == {"error": "no_actor"}


@pytest.mark.django_db
def test_block_endpoint_rejects_self_and_unknown(client):
    user = _user()
    me = _personal(user)
    client.force_login(user)
    assert client.post(f"/moderation/blocks/{me.id}").status_code == 400
    assert client.post(f"/moderation/blocks/{uuid4()}").status_code == 404


@pytest.mark.django_db
def test_block_endpoint_requires_login(client):
    target = _remote()
    assert client.post(f"/moderation/blocks/{target.id}").status_code == 302
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_moderation_blocks.py -q -k block_endpoint`
Expected: `404 != 200` (route missing).

- [ ] **Step 3: Implement the view and URL**

Append to `moderation/views.py` (add `from moderation import blocks as block_services`):

```python
@login_required
@require_http_methods(["POST", "DELETE"])
def blocks(request, actor_id):
    actor = get_object_or_404(Actor, pk=actor_id)
    if request.method == "DELETE":
        try:
            block_services.unblock_actor(request.user, actor)
        except block_services.NoActorError:
            return _error("no_actor", 409)
        return JsonResponse({"blocked": False})
    try:
        block_services.block_actor(request.user, actor)
    except block_services.NoActorError:
        return _error("no_actor", 409)
    except block_services.SelfBlockError:
        return _error("invalid_target", 400)
    return JsonResponse({"blocked": True})
```

Add to `moderation/urls.py`:

```python
    path(
        "moderation/blocks/<uuid:actor_id>",
        views.blocks,
        name="moderation-blocks",
    ),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_moderation_blocks.py -q`
Expected: `15 passed`

- [ ] **Step 5: Run the full suite, lint, and migration check**

Run: `uv run pytest -q`
Expected: previous total + new tests, no failures.

Run: `uv run ruff check .` and `uv run ruff format --check .`
Expected: clean.

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`

- [ ] **Step 6: Commit**

```bash
git add moderation/views.py moderation/urls.py tests/test_moderation_blocks.py
git commit -m "feat: expose block and unblock endpoints"
```
