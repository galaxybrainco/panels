# Ticket 9a — Supporters & On-Site Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Comic-defined tiers, verified supporter links/statuses, an on-site access gate, and a `ComicPlan` that overrides the storage cap.

**Architecture:** The `memberships` app owns `Tier`, `SupporterLink`, `SupporterStatus`, and `ComicPlan`; `memberships/services.py` links supporters and answers access. `comics.Page.required_tier` ties a `tier`-audience page to a tier; `memberships.services.can_view_page` gates it. `media.quota.comic_storage_quota` consults an active `ComicPlan` before the settings default. External ingest (Ko-fi/BMAC) and Stripe are 9b/9c.

**Tech Stack:** Django 6.1.1, PostgreSQL, existing `actors`/`comics` primitives, pytest-django, ruff.

**Spec:** `webcomic-fediverse-plan.md` §4 (`Tier`/`SupporterLink`/`SupporterStatus`/`ComicPlan`), §7 (off-platform members, email→Actor linking, access = active status at tier, per-comic plan + quota, gated content never federates), §14 ticket 9.

## Global Constraints

- Synchronous Django only. Run Python via `uv run` (Python 3.14).
- Access = an **active, unexpired** `SupporterStatus` at the required tier rank; works for local **and remote** reader actors.
- Gated (`members`/`tier`) pages never federate (already enforced by `federation_plan`); on-site gating is a separate service.
- An **active** `ComicPlan.quota_bytes` overrides the settings storage cap; any other plan status falls back to the settings default.
- No external HTTP in this ticket (Ko-fi/BMAC and Stripe are 9b/9c).
- Every task ends green on: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run python manage.py makemigrations --check --dry-run`.
- Dev DB: `docker compose up -d db`.

## Review Focus

Spec-implied failure modes no happy path exercises; each is pinned by a test in its owning task:

- An **expired** or **inactive** supporter status must not grant access. Tasks 1, 2.
- A status at a lower rank must not unlock a higher tier (rank comparison). Tasks 1, 2.
- A remote reader actor must be able to hold and use a supporter link. Task 1.
- A `members`-audience page must require *some* active supporter; a `tier` page its tier. Task 2.
- Only an **active** `ComicPlan` overrides the cap; a frozen one must not. Task 3.

## File Structure

- `memberships/models.py` — `Provider`, `Tier`, `SupporterLink`, `SupporterStatus`, `PlanStatus`, `ComicPlan`. (Tasks 1, 3)
- `memberships/migrations/0001_initial.py` / `0002_comicplan.py` — generated. (Tasks 1, 3)
- `memberships/services.py` — `link_supporter`, `grant_supporter_status`, `active_supporter_status`, `supporter_has_access`, `page_requires_tier`, `can_view_page`. (Tasks 1, 2)
- `comics/models.py` — `Page.required_tier`. (Task 2)
- `media/quota.py` — consult an active `ComicPlan`. (Task 3)
- Tests: `tests/test_memberships.py`.

---

### Task 1: Tiers, supporter links/statuses, and access

**Files:**
- Create: `memberships/models.py`, `memberships/services.py`, `memberships/migrations/0001_initial.py` (generated)
- Test: `tests/test_memberships.py`

**Interfaces:**
- Consumes: `comics.models.Comic`, `actors.models.Actor`, `core.models.UUIDModel`.
- Produces: `Provider`, `Tier`, `SupporterLink`, `SupporterStatus`; `memberships.services.{link_supporter, grant_supporter_status, active_supporter_status, supporter_has_access}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_memberships.py`:

```python
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.services import create_comic, create_page, create_series
from memberships.models import Provider, SupporterLink, SupporterStatus, Tier
from memberships.services import (
    active_supporter_status,
    grant_supporter_status,
    link_supporter,
    supporter_has_access,
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


def scene():
    owner = get_user_model().objects.create_user(
        email=f"o-{uuid4().hex[:8]}@example.com", password="x"
    )
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    return owner, comic, page


@pytest.mark.django_db
def test_tier_name_and_rank_unique_per_comic():
    _, comic, _ = scene()
    Tier.objects.create(comic=comic, name="Fan", rank=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        Tier.objects.create(comic=comic, name="Fan", rank=2)
    with pytest.raises(IntegrityError), transaction.atomic():
        Tier.objects.create(comic=comic, name="Other", rank=1)


@pytest.mark.django_db
def test_link_supporter_is_idempotent_and_verified():
    _, comic, _ = scene()
    actor = create_local_actor("alice")
    link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    again = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    assert SupporterLink.objects.filter(comic=comic, actor=actor).count() == 1
    assert again.verified_at is not None


@pytest.mark.django_db
def test_access_requires_active_unexpired_status():
    _, comic, _ = scene()
    actor = create_local_actor("alice")
    fan = Tier.objects.create(comic=comic, name="Fan", rank=1)
    link = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    assert supporter_has_access(actor, comic) is False
    grant_supporter_status(link, fan)
    assert supporter_has_access(actor, comic) is True
    grant_supporter_status(link, fan, active=False)
    assert active_supporter_status(actor, comic).active is False
    assert supporter_has_access(actor, comic) is False


@pytest.mark.django_db
def test_expired_status_grants_no_access():
    _, comic, _ = scene()
    actor = create_local_actor("alice")
    fan = Tier.objects.create(comic=comic, name="Fan", rank=1)
    link = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    grant_supporter_status(link, fan, valid_until=timezone.now() - timedelta(days=1))
    assert supporter_has_access(actor, comic) is False


@pytest.mark.django_db
def test_rank_gates_tier():
    _, comic, _ = scene()
    actor = create_local_actor("alice")
    fan = Tier.objects.create(comic=comic, name="Fan", rank=1)
    patron = Tier.objects.create(comic=comic, name="Patron", rank=3)
    link = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    grant_supporter_status(link, fan)
    assert supporter_has_access(actor, comic, fan) is True
    assert supporter_has_access(actor, comic, patron) is False


@pytest.mark.django_db
def test_remote_actor_can_support():
    _, comic, _ = scene()
    bob = remote("bob")
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    link = link_supporter(bob, comic, Provider.BMAC, "bob@remote.test")
    grant_supporter_status(link, tier)
    assert supporter_has_access(bob, comic) is True


@pytest.mark.django_db
def test_none_actor_has_no_access():
    _, comic, _ = scene()
    assert supporter_has_access(None, comic) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_memberships.py -q`
Expected: ERROR — `memberships.models`/`memberships.services` do not exist.

- [ ] **Step 3: Implement `memberships/models.py`**

```python
from django.db import models

from core.models import UUIDModel


class Provider(models.TextChoices):
    KOFI = "kofi", "Ko-fi"
    BMAC = "bmac", "Buy Me a Coffee"


class Tier(UUIDModel):
    comic = models.ForeignKey(
        "comics.Comic", on_delete=models.CASCADE, related_name="tiers"
    )
    name = models.CharField(max_length=100)
    rank = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "name"], name="unique_tier_name_per_comic"
            ),
            models.UniqueConstraint(
                fields=["comic", "rank"], name="unique_tier_rank_per_comic"
            ),
        ]
        ordering = ["comic", "rank", "id"]

    def __str__(self):
        return f"{self.comic}:{self.name}"


class SupporterLink(UUIDModel):
    comic = models.ForeignKey(
        "comics.Comic", on_delete=models.CASCADE, related_name="supporter_links"
    )
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="supporter_links"
    )
    provider = models.CharField(max_length=16, choices=Provider.choices)
    provider_email = models.EmailField()
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "actor"], name="unique_supporter_per_comic"
            )
        ]

    def __str__(self):
        return f"{self.actor} supports {self.comic}"


class SupporterStatus(UUIDModel):
    link = models.ForeignKey(
        SupporterLink, on_delete=models.CASCADE, related_name="statuses"
    )
    tier = models.ForeignKey(Tier, on_delete=models.CASCADE, related_name="statuses")
    active = models.BooleanField(default=True)
    valid_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.link} @ {self.tier}"
```

- [ ] **Step 4: Implement `memberships/services.py` (access portion)**

```python
from django.db import models
from django.utils import timezone

from memberships.models import SupporterLink, SupporterStatus


def link_supporter(actor, comic, provider, email):
    link, _ = SupporterLink.objects.update_or_create(
        comic=comic,
        actor=actor,
        defaults={
            "provider": provider,
            "provider_email": email,
            "verified_at": timezone.now(),
        },
    )
    return link


def grant_supporter_status(link, tier, *, valid_until=None, active=True):
    return SupporterStatus.objects.create(
        link=link, tier=tier, valid_until=valid_until, active=active
    )


def active_supporter_status(actor, comic):
    if actor is None:
        return None
    now = timezone.now()
    return (
        SupporterStatus.objects.filter(
            link__comic=comic, link__actor=actor, active=True
        )
        .filter(models.Q(valid_until__isnull=True) | models.Q(valid_until__gt=now))
        .select_related("tier")
        .order_by("-tier__rank", "-created_at")
        .first()
    )


def supporter_has_access(actor, comic, tier=None):
    status = active_supporter_status(actor, comic)
    if status is None:
        return False
    if tier is None:
        return True
    return status.tier.rank >= tier.rank
```

- [ ] **Step 5: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations memberships
uv run python manage.py migrate
uv run pytest tests/test_memberships.py -q
```
Expected: PASS (7 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add memberships/models.py memberships/services.py memberships/migrations tests/test_memberships.py
git commit -m "feat: comic tiers, supporter links/statuses, and access"
```

---

### Task 2: Page tier requirement and the view gate

**Files:**
- Modify: `comics/models.py`, `comics/migrations/` (generated), `memberships/services.py`
- Test: `tests/test_memberships.py`

**Interfaces:**
- Consumes: Task 1 `supporter_has_access`, `Tier`; `federation.activitypub.Audience`.
- Produces: `Page.required_tier`; `memberships.services.{ANY_SUPPORTER, page_requires_tier, can_view_page}`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_memberships.py` (add to imports: `from federation.activitypub import Audience`, `from memberships.services import ANY_SUPPORTER, can_view_page, page_requires_tier`):

```python
@pytest.mark.django_db
def test_public_page_requires_nothing():
    _, comic, page = scene()
    assert page_requires_tier(page) is None
    assert can_view_page(None, page) is True


@pytest.mark.django_db
def test_members_page_requires_any_supporter():
    _, comic, page = scene()
    page.audience = Audience.MEMBERS
    page.save()
    assert page_requires_tier(page) is ANY_SUPPORTER
    actor = create_local_actor("alice")
    assert can_view_page(actor, page) is False
    link = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    grant_supporter_status(link, Tier.objects.create(comic=comic, name="Fan", rank=1))
    assert can_view_page(actor, page) is True


@pytest.mark.django_db
def test_tier_page_requires_that_rank():
    _, comic, page = scene()
    fan = Tier.objects.create(comic=comic, name="Fan", rank=1)
    patron = Tier.objects.create(comic=comic, name="Patron", rank=3)
    page.audience = Audience.TIER
    page.required_tier = patron
    page.save()
    actor = create_local_actor("alice")
    link = link_supporter(actor, comic, Provider.KOFI, "alice@example.com")
    grant_supporter_status(link, fan)
    assert can_view_page(actor, page) is False
    grant_supporter_status(link, patron)
    assert can_view_page(actor, page) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_memberships.py -q -k "page"`
Expected: ERROR — `page_requires_tier`/`can_view_page` do not exist; `Page.required_tier` missing.

- [ ] **Step 3: Add `Page.required_tier`**

In `comics/models.py`, add to `Page` after `audience`:

```python
    required_tier = models.ForeignKey(
        "memberships.Tier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pages",
    )
```

- [ ] **Step 4: Add the gate to `memberships/services.py`**

Add to the imports `from federation.activitypub import Audience` and append:

```python
ANY_SUPPORTER = object()


def page_requires_tier(page):
    if page.audience not in (Audience.MEMBERS, Audience.TIER):
        return None
    if page.audience == Audience.TIER and page.required_tier_id:
        return page.required_tier
    return ANY_SUPPORTER


def can_view_page(actor, page):
    required = page_requires_tier(page)
    if required is None:
        return True
    if required is ANY_SUPPORTER:
        return supporter_has_access(actor, page.series.comic)
    return supporter_has_access(actor, page.series.comic, required)
```

- [ ] **Step 5: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations comics
uv run python manage.py migrate
uv run pytest tests/test_memberships.py -q
```
Expected: PASS (10 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add comics/models.py comics/migrations memberships/services.py tests/test_memberships.py
git commit -m "feat: page tier requirement and on-site access gate"
```

---

### Task 3: `ComicPlan` and the storage-cap override

**Files:**
- Modify: `memberships/models.py`, `media/quota.py`
- Create: `memberships/migrations/0002_comicplan.py` (generated)
- Test: `tests/test_memberships.py`

**Interfaces:**
- Consumes: `comics.models.Comic`, `media.quota.comic_storage_quota`.
- Produces: `PlanStatus`, `ComicPlan`; `comic_storage_quota` returns an active plan's `quota_bytes`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_memberships.py` (add to imports: `from memberships.models import ComicPlan, PlanStatus`, `from media.quota import comic_storage_quota`):

```python
@pytest.mark.django_db
def test_active_plan_overrides_the_storage_cap(settings):
    _, comic, _ = scene()
    settings.MEDIA_STORAGE_QUOTA_BYTES = 111
    ComicPlan.objects.create(comic=comic, status=PlanStatus.ACTIVE, quota_bytes=222)
    assert comic_storage_quota(comic) == 222


@pytest.mark.django_db
def test_inactive_plan_does_not_override(settings):
    _, comic, _ = scene()
    settings.MEDIA_STORAGE_QUOTA_BYTES = 111
    ComicPlan.objects.create(comic=comic, status=PlanStatus.FROZEN, quota_bytes=222)
    assert comic_storage_quota(comic) == 111


@pytest.mark.django_db
def test_no_plan_uses_the_settings_default(settings):
    _, comic, _ = scene()
    settings.MEDIA_STORAGE_QUOTA_BYTES = 111
    assert comic_storage_quota(comic) == 111
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_memberships.py -q -k "plan"`
Expected: ERROR — `ComicPlan` does not exist.

- [ ] **Step 3: Add `PlanStatus` and `ComicPlan`**

Append to `memberships/models.py`:

```python
class PlanStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    FROZEN = "frozen", "Frozen"
    ARCHIVED = "archived", "Archived"


class ComicPlan(UUIDModel):
    comic = models.OneToOneField(
        "comics.Comic", on_delete=models.CASCADE, related_name="plan"
    )
    status = models.CharField(
        max_length=16, choices=PlanStatus.choices, default=PlanStatus.ACTIVE
    )
    quota_bytes = models.PositiveBigIntegerField()
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    current_period_end = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Plan({self.comic}:{self.status})"
```

- [ ] **Step 4: Override the cap in `media/quota.py`**

Replace `comic_storage_quota` with:

```python
def comic_storage_quota(comic):
    plan = _active_plan(comic)
    if plan is not None:
        return plan.quota_bytes
    return getattr(settings, "MEDIA_STORAGE_QUOTA_BYTES", DEFAULT_QUOTA_BYTES)


def _active_plan(comic):
    from memberships.models import ComicPlan, PlanStatus

    plan = ComicPlan.objects.filter(comic=comic).first()
    if plan is not None and plan.status == PlanStatus.ACTIVE:
        return plan
    return None
```

- [ ] **Step 5: Create the migration and run the tests**

Run:
```bash
uv run python manage.py makemigrations memberships
uv run python manage.py migrate
uv run pytest tests/test_memberships.py -q
```
Expected: PASS (13 passed).

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add memberships/models.py memberships/migrations media/quota.py tests/test_memberships.py
git commit -m "feat: ComicPlan overriding the per-comic storage cap"
```

---

## Self-Review

**Spec coverage (Ticket 9a scope):** comic-defined `Tier`s, `SupporterLink` + `SupporterStatus` with validity, email→Actor linking service, `supporter_has_access`, on-site page gate, and `ComicPlan` overriding the storage cap (§4/§7). Out of scope by design: Ko-fi/BMAC webhooks + reconcile (9b), Stripe billing/webhooks/add-ons/lapse (9c), view layer, and authoring-path wiring of `required_tier`.

**Placeholder scan:** no TBD/TODO steps; every code and test step is complete.

**Type consistency:** `Provider`/`Tier`/`SupporterLink`/`SupporterStatus`/`PlanStatus`/`ComicPlan` are the single membership vocabulary; `link_supporter`/`grant_supporter_status`/`supporter_has_access` are the single supporter API; `page_requires_tier`/`can_view_page` are the single page gate; `comic_storage_quota` remains the single cap resolver.

**Review Focus coverage:** expired/inactive denied — Tasks 1, 2; rank compare — Tasks 1, 2; remote actor — Task 1; members vs tier pages — Task 2; only active plan overrides — Task 3.