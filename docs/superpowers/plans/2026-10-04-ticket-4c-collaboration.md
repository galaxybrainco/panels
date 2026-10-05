# Ticket 4c — Collaboration & Access Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Owner-only collaborator management for a comic: invite an existing local user by email, change/remove their role, and transfer ownership to an existing collaborator.

**Architecture:** A new `comics/collaboration.py` holds four thin, `transaction.atomic` service functions over the existing `ComicRole` model, all gated by a new `permissions.can_manage_collaborators` (= owner). Authorization failures raise `PermissionDenied`; business-rule failures raise `ValidationError`, matching `comics/services.py` and `comics/publishing.py`. No model changes, no migration. The one-owner partial-unique constraint and the `unique_user_role_per_comic` constraint backstop races.

**Tech Stack:** Django 6.1.1, PostgreSQL, pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §4 (`ComicRole` roles: owner invites/removes collaborators and transfers ownership), §6 (creator collaboration), §13 (a mis-scoped role is a security bug).

## Global Constraints

- Synchronous Django only. Run Python via `uv run` (Python 3.14).
- Role management is **owner-only**; `permissions.can_manage_collaborators` is the single authorization gate.
- Roles: `owner`, `editor`, `contributor`, `moderator`. Exactly one `owner` per comic; one role row per `(comic, user)`.
- Assignable roles are editor/contributor/moderator only — never `owner` (ownership moves only through `transfer_ownership`).
- Authorization violations raise `django.core.exceptions.PermissionDenied`; business-rule violations raise `ValidationError` with a field-keyed message dict.
- Entities inherit `core.UUIDModel`; `ComicRole` is the one plain integer-pk model in `comics`.
- Invites target **existing local users by email** only (no pending-email invites, no notification/email sending). Self-leave is out of scope.
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- A non-owner (editor, contributor, moderator, or outsider) must never invite, change, remove, or transfer. Tasks 1–3.
- The owner must never be demoted, role-changed, or removed by these services — a comic must never be left with zero owners. Tasks 2–3.
- `transfer_ownership` must be atomic and leave exactly one owner (demote-then-promote under the partial-unique constraint). Task 3.
- `invite_user` must not target a non-existent account and must reject duplicate invites (case-insensitive email lookup). Task 1.
- Ownership may only move to an existing collaborator; transferring to self or a stranger is rejected. Task 3.

## File Structure

- `comics/permissions.py` — add `can_manage_collaborators(user, comic) -> bool`. (Task 1)
- `comics/collaboration.py` — new: `invite_user`, `change_role`, `remove_collaborator`, `transfer_ownership` plus internal `_require_owner`/`_validate_assignable`. (Tasks 1–3)
- Tests: `tests/test_comics_collaboration.py`.

---

### Task 1: Permission gate and `invite_user`

**Files:**
- Modify: `comics/permissions.py`
- Create: `comics/collaboration.py`
- Test: `tests/test_comics_collaboration.py`

**Interfaces:**
- Consumes: `comics.permissions.is_owner`, `comics.models.ComicRole`, `django.contrib.auth.get_user_model`.
- Produces:
  - `can_manage_collaborators(user, comic) -> bool`.
  - `collaboration.ASSIGNABLE_ROLES: set[str]` = {editor, contributor, moderator}.
  - `collaboration.invite_user(owner, comic, email, role) -> ComicRole`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_comics_collaboration.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError

from comics.collaboration import invite_user
from comics.models import ComicRole
from comics.permissions import can_manage_collaborators
from comics.services import create_comic


def _user(email):
    return get_user_model().objects.create_user(email=email, password="x")


@pytest.fixture
def scene():
    owner = _user("owner@example.com")
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    return owner, comic


@pytest.mark.django_db
def test_can_manage_collaborators_is_owner_only(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    assert can_manage_collaborators(owner, comic) is True
    assert can_manage_collaborators(editor, comic) is False
    assert can_manage_collaborators(None, comic) is False


@pytest.mark.django_db
def test_owner_invites_existing_user_case_insensitive(scene):
    owner, comic = scene
    artist = _user("Artist@Example.com")
    membership = invite_user(owner, comic, "artist@example.com", ComicRole.Role.EDITOR)
    assert membership.comic == comic
    assert membership.user == artist
    assert membership.role == ComicRole.Role.EDITOR


@pytest.mark.django_db
def test_invite_unknown_email_is_rejected(scene):
    owner, comic = scene
    with pytest.raises(ValidationError):
        invite_user(owner, comic, "nobody@example.com", ComicRole.Role.EDITOR)


@pytest.mark.django_db
def test_invite_existing_collaborator_is_rejected(scene):
    owner, comic = scene
    _user("editor@example.com")
    invite_user(owner, comic, "editor@example.com", ComicRole.Role.EDITOR)
    with pytest.raises(ValidationError):
        invite_user(owner, comic, "editor@example.com", ComicRole.Role.MODERATOR)


@pytest.mark.django_db
def test_invite_owner_role_is_rejected(scene):
    owner, comic = scene
    _user("editor@example.com")
    with pytest.raises(ValidationError):
        invite_user(owner, comic, "editor@example.com", ComicRole.Role.OWNER)


@pytest.mark.django_db
def test_non_owner_cannot_invite(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    outsider = _user("outsider@example.com")
    with pytest.raises(PermissionDenied):
        invite_user(editor, comic, "outsider@example.com", ComicRole.Role.CONTRIBUTOR)
    with pytest.raises(PermissionDenied):
        invite_user(outsider, comic, "outsider@example.com", ComicRole.Role.CONTRIBUTOR)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_comics_collaboration.py -q`
Expected: ERROR — `comics.collaboration` does not exist, `can_manage_collaborators` not importable.

- [ ] **Step 3: Add `can_manage_collaborators` to `comics/permissions.py`**

Append to `comics/permissions.py`:

```python
def can_manage_collaborators(user, comic) -> bool:
    return is_owner(user, comic)
```

- [ ] **Step 4: Create `comics/collaboration.py`**

```python
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from comics import permissions
from comics.models import ComicRole

ASSIGNABLE_ROLES = {
    ComicRole.Role.EDITOR,
    ComicRole.Role.CONTRIBUTOR,
    ComicRole.Role.MODERATOR,
}


def _require_owner(user, comic) -> None:
    if not permissions.can_manage_collaborators(user, comic):
        raise PermissionDenied("You cannot manage this comic's collaborators.")


def _validate_assignable(role):
    if role not in ASSIGNABLE_ROLES:
        raise ValidationError({"role": "Choose editor, contributor, or moderator."})
    return role


@transaction.atomic
def invite_user(owner, comic, email, role):
    _require_owner(owner, comic)
    _validate_assignable(role)
    normalized = (email or "").strip()
    if not normalized:
        raise ValidationError({"email": "An email address is required."})
    matches = list(get_user_model().objects.filter(email__iexact=normalized))
    if len(matches) != 1:
        raise ValidationError({"email": "No local account uses that email address."})
    user = matches[0]
    if ComicRole.objects.filter(comic=comic, user=user).exists():
        raise ValidationError({"email": "That user already has a role on this comic."})
    try:
        return ComicRole.objects.create(comic=comic, user=user, role=role)
    except IntegrityError as exc:
        raise ValidationError(
            {"email": "That user already has a role on this comic."}
        ) from exc
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_comics_collaboration.py -q`
Expected: PASS (7 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green, no missing migrations.

- [ ] **Step 7: Commit**

```bash
git add comics/permissions.py comics/collaboration.py tests/test_comics_collaboration.py
git commit -m "feat: owner-only comic invitations"
```

---

### Task 2: `change_role` and `remove_collaborator`

**Files:**
- Modify: `comics/collaboration.py`
- Test: `tests/test_comics_collaboration.py`

**Interfaces:**
- Consumes: Task 1's `_require_owner`, `_validate_assignable`, `invite_user`.
- Produces:
  - `collaboration.change_role(owner, comic, user, role) -> ComicRole`.
  - `collaboration.remove_collaborator(owner, comic, user) -> None`.

- [ ] **Step 1: Update the test imports and add failing tests**

In `tests/test_comics_collaboration.py`, change the collaboration import line to:

```python
from comics.collaboration import change_role, invite_user, remove_collaborator
```

Append:

```python
@pytest.mark.django_db
def test_change_role_updates_membership(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    invite_user(owner, comic, "editor@example.com", ComicRole.Role.EDITOR)
    membership = change_role(owner, comic, editor, ComicRole.Role.MODERATOR)
    assert membership.role == ComicRole.Role.MODERATOR
    assert ComicRole.objects.get(comic=comic, user=editor).role == (
        ComicRole.Role.MODERATOR
    )


@pytest.mark.django_db
def test_change_role_requires_existing_collaborator(scene):
    owner, comic = scene
    stranger = _user("stranger@example.com")
    with pytest.raises(ValidationError):
        change_role(owner, comic, stranger, ComicRole.Role.EDITOR)


@pytest.mark.django_db
def test_change_role_cannot_change_owner(scene):
    owner, comic = scene
    with pytest.raises(ValidationError):
        change_role(owner, comic, owner, ComicRole.Role.EDITOR)


@pytest.mark.django_db
def test_change_role_rejects_owner_role(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    invite_user(owner, comic, "editor@example.com", ComicRole.Role.EDITOR)
    with pytest.raises(ValidationError):
        change_role(owner, comic, editor, ComicRole.Role.OWNER)


@pytest.mark.django_db
def test_remove_collaborator_deletes_membership(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    invite_user(owner, comic, "editor@example.com", ComicRole.Role.EDITOR)
    remove_collaborator(owner, comic, editor)
    assert not ComicRole.objects.filter(comic=comic, user=editor).exists()


@pytest.mark.django_db
def test_remove_owner_is_rejected(scene):
    owner, comic = scene
    with pytest.raises(ValidationError):
        remove_collaborator(owner, comic, owner)


@pytest.mark.django_db
def test_remove_non_collaborator_is_rejected(scene):
    owner, comic = scene
    stranger = _user("stranger@example.com")
    with pytest.raises(ValidationError):
        remove_collaborator(owner, comic, stranger)


@pytest.mark.django_db
def test_non_owner_cannot_change_or_remove(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    contributor = _user("contributor@example.com")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    with pytest.raises(PermissionDenied):
        change_role(editor, comic, contributor, ComicRole.Role.MODERATOR)
    with pytest.raises(PermissionDenied):
        remove_collaborator(editor, comic, contributor)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_comics_collaboration.py -q`
Expected: ERROR — cannot import `change_role`/`remove_collaborator`.

- [ ] **Step 3: Implement `change_role` and `remove_collaborator`**

Append to `comics/collaboration.py`:

```python
@transaction.atomic
def change_role(owner, comic, user, role):
    _require_owner(owner, comic)
    _validate_assignable(role)
    try:
        membership = ComicRole.objects.select_for_update().get(comic=comic, user=user)
    except ComicRole.DoesNotExist as exc:
        raise ValidationError(
            {"user": "That user is not a collaborator on this comic."}
        ) from exc
    if membership.role == ComicRole.Role.OWNER:
        raise ValidationError(
            {"user": "Transfer ownership to change the owner's role."}
        )
    membership.role = role
    membership.save(update_fields=["role"])
    return membership


@transaction.atomic
def remove_collaborator(owner, comic, user):
    _require_owner(owner, comic)
    try:
        membership = ComicRole.objects.select_for_update().get(comic=comic, user=user)
    except ComicRole.DoesNotExist as exc:
        raise ValidationError(
            {"user": "That user is not a collaborator on this comic."}
        ) from exc
    if membership.role == ComicRole.Role.OWNER:
        raise ValidationError(
            {"user": "Transfer ownership before removing the owner."}
        )
    membership.delete()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_comics_collaboration.py -q`
Expected: PASS (15 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add comics/collaboration.py tests/test_comics_collaboration.py
git commit -m "feat: change and remove comic collaborators"
```

---

### Task 3: `transfer_ownership`

**Files:**
- Modify: `comics/collaboration.py`
- Test: `tests/test_comics_collaboration.py`

**Interfaces:**
- Consumes: Task 1's `_require_owner`; `comics.models.ComicRole`.
- Produces: `collaboration.transfer_ownership(owner, comic, to_user) -> ComicRole` (the promoted membership).

- [ ] **Step 1: Update the test imports and add failing tests**

In `tests/test_comics_collaboration.py`, change the collaboration import line to:

```python
from comics.collaboration import (
    change_role,
    invite_user,
    remove_collaborator,
    transfer_ownership,
)
```

Append:

```python
@pytest.mark.django_db
def test_transfer_ownership_demotes_old_owner_and_promotes_target(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    invite_user(owner, comic, "editor@example.com", ComicRole.Role.EDITOR)
    membership = transfer_ownership(owner, comic, editor)
    assert membership.role == ComicRole.Role.OWNER
    assert ComicRole.objects.get(comic=comic, user=owner).role == ComicRole.Role.EDITOR
    assert (
        ComicRole.objects.filter(comic=comic, role=ComicRole.Role.OWNER).count() == 1
    )


@pytest.mark.django_db
def test_transfer_requires_existing_collaborator(scene):
    owner, comic = scene
    stranger = _user("stranger@example.com")
    with pytest.raises(ValidationError):
        transfer_ownership(owner, comic, stranger)


@pytest.mark.django_db
def test_transfer_to_self_is_rejected(scene):
    owner, comic = scene
    with pytest.raises(ValidationError):
        transfer_ownership(owner, comic, owner)


@pytest.mark.django_db
def test_non_owner_cannot_transfer(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    ComicRole.objects.create(comic=comic, user=editor, role=ComicRole.Role.EDITOR)
    with pytest.raises(PermissionDenied):
        transfer_ownership(editor, comic, owner)


@pytest.mark.django_db
def test_management_rights_follow_transfer(scene):
    owner, comic = scene
    editor = _user("editor@example.com")
    invite_user(owner, comic, "editor@example.com", ComicRole.Role.EDITOR)
    transfer_ownership(owner, comic, editor)
    _user("newcomer@example.com")
    assert can_manage_collaborators(editor, comic) is True
    assert can_manage_collaborators(owner, comic) is False
    invite_user(editor, comic, "newcomer@example.com", ComicRole.Role.CONTRIBUTOR)
    with pytest.raises(PermissionDenied):
        invite_user(owner, comic, "newcomer@example.com", ComicRole.Role.MODERATOR)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_comics_collaboration.py -q`
Expected: ERROR — cannot import `transfer_ownership`.

- [ ] **Step 3: Implement `transfer_ownership`**

Append to `comics/collaboration.py`:

```python
@transaction.atomic
def transfer_ownership(owner, comic, to_user):
    _require_owner(owner, comic)
    if to_user == owner:
        raise ValidationError({"user": "You already own this comic."})
    memberships = {
        membership.user_id: membership
        for membership in ComicRole.objects.select_for_update().filter(comic=comic)
    }
    target = memberships.get(to_user.id)
    if target is None:
        raise ValidationError(
            {"user": "Ownership can only be transferred to an existing collaborator."}
        )
    current = memberships[owner.id]
    current.role = ComicRole.Role.EDITOR
    current.save(update_fields=["role"])
    target.role = ComicRole.Role.OWNER
    target.save(update_fields=["role"])
    return target
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_comics_collaboration.py -q`
Expected: PASS (20 passed).

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add comics/collaboration.py tests/test_comics_collaboration.py
git commit -m "feat: transfer comic ownership to a collaborator"
```

---

## Self-Review

**Spec coverage (Ticket 4c scope):** owner-only collaborator management — invite existing user by email (Task 1), change/remove roles (Task 2), transfer ownership to an existing collaborator with the old owner demoted to editor (Task 3) — per §4/§6. Authorization centralized in `can_manage_collaborators` (§13). Out of scope by design: self-leave, pending email invites/notifications, billing reassignment (§7, later), view/URL layer.

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `ComicRole.Role` is the single role vocabulary; `ASSIGNABLE_ROLES` excludes `owner`; `_require_owner` is the single authorization path; `invite_user`/`change_role`/`remove_collaborator`/`transfer_ownership` keep the `(actor, comic, ...)` argument order; `can_manage_collaborators` is the single permission predicate.

**Review Focus coverage:** non-owner denial — Tasks 1–3; owner can't be demoted/removed — Tasks 2–3; transfer atomicity + one-owner — Task 3; invite lookup/duplicate — Task 1; transfer target restrictions — Task 3.
