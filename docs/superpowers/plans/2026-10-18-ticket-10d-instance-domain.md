# Instance & Domain Moderation (10d) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Staff can limit/suspend/freeze/disable/warn accounts, force content sensitive, apply the domain policy suite with allowlist mode, and block registration email domains/IPs — enforced across inbox, fetch, delivery, public AP, publishing, and signup.

**Architecture:** One `AccountModeration` row per actor drives state helpers in `moderation/accounts.py`; `moderation/domains.py` manages `Instance` policy flags; `moderation/registration.py` owns signup blocks. Enforcement hooks live in the existing inbound/fetch/delivery/public-view/publishing/signup seams and reuse `ModerationAudit`.

**Tech Stack:** Python 3.14, Django 6.1.1, pytest, `monkeypatch`/locmem mail.

**Spec:** `docs/superpowers/specs/2026-10-18-instance-domain-moderation-design.md`

## Global Constraints

- Account/domain/registration actions are staff-only and every change writes one `ModerationAudit` row.
- `limit`/`suspend` work on local and remote actors; `freeze`/`disable`/`warn` are local-only (409 otherwise).
- A non-empty reason is required for every account action.
- Enforcement must never delete content; only visibility/delivery/posting gates change.
- IP matching uses `REMOTE_ADDR` only; email-domain matching is exact.
- Domain inputs are normalized like 10a (hostname/IDN→ASCII, ports dropped, local instance rejected).
- Keep `ruff check .` and `ruff format --check .` clean; run `uv run python manage.py makemigrations --check --dry-run` before final commits.

## Review Focus

- **State transitions:** limit/suspend/freeze/disable/restore/force-sensitive/warn correctness, local-only rejection, reason validation, reactivation on restore, audit names.
- **Enforcement gaps:** suspended inbound drop, allowlist mode, blocked/silenced public 404s, outbound blocked-domain skip, suspended follower exclusion, following suspended rejected, frozen publish denial, force-sensitive marking.
- **Domain policy:** normalization/creation, restore clears all flags, audit, allowlist toggle semantics, local domain rejection.
- **Registration:** email exact-match (case-insensitive), CIDR membership via REMOTE_ADDR, CRUD validation/audit, signup errors preserved.
- **No leaks:** staff-only endpoints, 404 for hidden content, no federation activities emitted by these actions.

---

### Task 1: Account moderation model, actions, and endpoints

**Files:**
- Modify: `moderation/models.py`
- Create: `moderation/migrations/0004_account_moderation.py` (generated)
- Create: `moderation/accounts.py`
- Modify: `moderation/reports.py` (reuse `record_action`)
- Modify: `moderation/views.py`, `moderation/urls.py`
- Create: `tests/test_moderation_accounts.py`

**Interfaces:**
- Consumes: `Actor`, `Page`, `ModerationAudit`, `reports.record_action`.
- Produces: `AccountModeration`, `AccountState`; `account_moderation`, `account_state`, `is_limited`, `is_suspended`, `is_frozen`, `is_disabled`, `can_post`, `is_force_sensitive`, `publicly_visible`, `apply_account_action`; errors `InvalidActionError`, `LocalOnlyError`.

- [ ] **Step 1: Write failing action tests**

Create `tests/test_moderation_accounts.py`:

```python
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core import mail

from actors.models import Actor, ActorType
from actors.services import create_local_actor
from comics.models import Page
from comics.services import create_comic, create_page, create_series
from moderation import accounts
from moderation.models import AccountModeration, AccountState, ModerationAudit


def _user(staff=False):
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x", is_staff=staff
    )


def _local(user=None):
    user = user or _user()
    return user, create_local_actor(f"u{uuid4().hex[:8]}", user=user)


def _remote(handle="bob"):
    return Actor.objects.create(
        ap_id=f"https://remote.test/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain="remote.test",
        inbox=f"https://remote.test/actors/{handle}/inbox",
    )


@pytest.mark.django_db
def test_limit_and_suspend_set_state_and_audit():
    staff = _user(staff=True)
    _, actor = _local()
    accounts.apply_account_action(actor, staff, "limit", reason="spam")
    assert accounts.account_state(actor) == AccountState.LIMITED
    accounts.apply_account_action(actor, staff, "suspend", reason="spam")
    assert accounts.is_suspended(actor) is True
    assert ModerationAudit.objects.filter(action="account.suspended").count() == 1


@pytest.mark.django_db
def test_freeze_is_local_only_and_blocks_posting():
    staff = _user(staff=True)
    remote = _remote()
    with pytest.raises(accounts.LocalOnlyError):
        accounts.apply_account_action(remote, staff, "freeze", reason="x")
    user, actor = _local()
    accounts.apply_account_action(actor, staff, "freeze", reason="x")
    assert accounts.is_frozen(actor) is True
    assert accounts.can_post(user) is False


@pytest.mark.django_db
def test_disable_and_restore_reactivate_the_user():
    staff = _user(staff=True)
    user, actor = _local()
    accounts.apply_account_action(actor, staff, "disable", reason="abuse")
    user.refresh_from_db()
    assert user.is_active is False
    accounts.apply_account_action(actor, staff, "restore", reason="appeal")
    user.refresh_from_db()
    assert user.is_active is True
    assert accounts.account_state(actor) == AccountState.NORMAL


@pytest.mark.django_db
def test_force_sensitive_marks_existing_pages():
    staff = _user(staff=True)
    user, actor = _local()
    comic = create_comic(user, f"comic{uuid4().hex[:6]}", "Comic")
    series = create_series(user, comic, "Main")
    page = create_page(user, series, title="Page")
    page.published_by = user
    page.save(update_fields=["published_by", "updated_at"])
    accounts.apply_account_action(
        actor, staff, "force_sensitive", reason="policy", enabled=True
    )
    page.refresh_from_db()
    assert page.sensitive is True
    assert accounts.is_force_sensitive(user) is True


@pytest.mark.django_db
def test_warn_sends_one_email():
    staff = _user(staff=True)
    user, actor = _local()
    accounts.apply_account_action(actor, staff, "warn", reason="be kind")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert ModerationAudit.objects.filter(action="account.warned").count() == 1


@pytest.mark.django_db
def test_actions_require_reason_and_staff():
    staff = _user(staff=True)
    _, actor = _local()
    with pytest.raises(accounts.InvalidActionError):
        accounts.apply_account_action(actor, staff, "limit", reason="  ")
    with pytest.raises(accounts.NotPermittedError):
        accounts.apply_account_action(actor, _user(), "limit", reason="x")
    with pytest.raises(accounts.InvalidActionError):
        accounts.apply_account_action(actor, staff, "bogus", reason="x")


@pytest.mark.django_db
def test_account_endpoints_are_staff_only(client):
    staff = _user(staff=True)
    _, actor = _local()
    client.force_login(_user())
    assert client.get(f"/moderation/accounts/{actor.id}").status_code == 403
    client.force_login(staff)
    response = client.post(
        f"/moderation/accounts/{actor.id}/action",
        data='{"action": "limit", "reason": "spam"}',
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["state"] == "limited"
    assert client.get(f"/moderation/accounts/{actor.id}").json()["state"] == "limited"
    assert (
        client.post(
            f"/moderation/accounts/{actor.id}/action",
            data='{"action": "freeze", "reason": "x"}',
            content_type="application/json",
        ).status_code
        == 200
    )
    remote = _remote()
    assert (
        client.post(
            f"/moderation/accounts/{remote.id}/action",
            data='{"action": "freeze", "reason": "x"}',
            content_type="application/json",
        ).status_code
        == 409
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_moderation_accounts.py -q`
Expected: `ImportError: cannot import name 'accounts'`.

- [ ] **Step 3: Implement the model**

Append to `moderation/models.py`:

```python
class AccountState(models.TextChoices):
    NORMAL = "normal", "Normal"
    LIMITED = "limited", "Limited"
    SUSPENDED = "suspended", "Suspended"
    FROZEN = "frozen", "Frozen"
    DISABLED = "disabled", "Disabled"


class AccountModeration(UUIDModel):
    actor = models.OneToOneField(
        "actors.Actor", on_delete=models.CASCADE, related_name="moderation"
    )
    state = models.CharField(
        max_length=16, choices=AccountState.choices, default=AccountState.NORMAL
    )
    force_sensitive = models.BooleanField(default=False)
    reason = models.TextField(blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="account_moderation_changes",
    )
```

Run: `uv run python manage.py makemigrations moderation`
Expected: `moderation/migrations/0004_account_moderation.py`.

- [ ] **Step 4: Implement the account services**

Create `moderation/accounts.py`:

```python
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q

from actors.models import Actor, Instance
from comics.models import Page
from moderation.models import AccountModeration, AccountState
from moderation.reports import NotPermittedError, record_action

logger = logging.getLogger(__name__)


class InvalidActionError(Exception):
    pass


class LocalOnlyError(Exception):
    pass


LOCAL_ONLY = {"freeze", "disable", "warn"}
AUDIT_ACTIONS = {
    "limit": "account.limited",
    "suspend": "account.suspended",
    "freeze": "account.frozen",
    "disable": "account.disabled",
    "restore": "account.restored",
    "warn": "account.warned",
    "force_sensitive": "account.force_sensitive",
}
STATES = {
    "limit": AccountState.LIMITED,
    "suspend": AccountState.SUSPENDED,
    "freeze": AccountState.FROZEN,
    "disable": AccountState.DISABLED,
    "restore": AccountState.NORMAL,
}


def account_moderation(actor):
    record, _ = AccountModeration.objects.get_or_create(actor=actor)
    return record


def account_state(actor):
    record = AccountModeration.objects.filter(actor=actor).first()
    return record.state if record is not None else AccountState.NORMAL


def is_limited(actor):
    return account_state(actor) == AccountState.LIMITED


def is_suspended(actor):
    return account_state(actor) == AccountState.SUSPENDED


def is_frozen(actor):
    return account_state(actor) == AccountState.FROZEN


def is_disabled(actor):
    return account_state(actor) == AccountState.DISABLED


def is_force_sensitive(user):
    if user is None:
        return False
    record = AccountModeration.objects.filter(actor__user=user).first()
    return record.force_sensitive if record is not None else False


def can_post(user):
    if user is None or not user.is_active:
        return False
    actor = Actor.objects.filter(user=user).first()
    if actor is None:
        return True
    return account_state(actor) not in {
        AccountState.FROZEN,
        AccountState.DISABLED,
        AccountState.SUSPENDED,
    }


def publicly_visible(actor):
    if is_limited(actor) or is_suspended(actor):
        return False
    if actor.domain:
        instance = Instance.objects.filter(domain=actor.domain).first()
        if instance is not None and (instance.blocked or instance.silenced):
            return False
    return True


def _warn(user, reason):
    if not user.email:
        return
    try:
        send_mail(
            "Moderation warning",
            f"A moderator sent you a warning:\n\n{reason}\n",
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
        )
    except Exception:
        logger.exception("Failed to send warning to %s", user.pk)


def apply_account_action(actor, staff, action, *, reason, enabled=None):
    if staff is None or not staff.is_staff:
        raise NotPermittedError
    if action not in AUDIT_ACTIONS:
        raise InvalidActionError
    if not isinstance(reason, str) or not reason.strip():
        raise InvalidActionError
    reason = reason.strip()
    if action in LOCAL_ONLY and actor.user is None:
        raise LocalOnlyError
    if action == "force_sensitive" and not isinstance(enabled, bool):
        raise InvalidActionError
    record = account_moderation(actor)
    if action == "force_sensitive":
        record.force_sensitive = enabled
        if enabled and actor.user is not None:
            Page.objects.filter(
                Q(author=actor.user) | Q(published_by=actor.user)
            ).update(sensitive=True)
    else:
        if action == "disable":
            actor.user.is_active = False
            actor.user.save(update_fields=["is_active"])
        elif action == "restore" and actor.user is not None:
            if not actor.user.is_active:
                actor.user.is_active = True
                actor.user.save(update_fields=["is_active"])
        elif action == "warn":
            _warn(actor.user, reason)
        if action in STATES:
            record.state = STATES[action]
    record.reason = reason
    record.changed_by = staff
    record.save()
    record_action(
        staff,
        AUDIT_ACTIONS[action],
        target=f"actor:{actor.id}",
        data={"reason": reason, "enabled": enabled},
    )
    return record
```

- [ ] **Step 5: Add the endpoints**

In `moderation/views.py`, import `from moderation import accounts as account_services` and add:

```python
def _serialize_account(actor):
    record = account_services.account_moderation(actor)
    return {
        "actor": str(actor.id),
        "state": record.state,
        "force_sensitive": record.force_sensitive,
        "reason": record.reason,
        "changed_by": record.changed_by.email if record.changed_by else None,
    }


@login_required
@require_http_methods(["GET"])
def account_detail(request, actor_id):
    if not request.user.is_staff:
        return _error("not_permitted", 403)
    actor = get_object_or_404(Actor, pk=actor_id)
    return JsonResponse(_serialize_account(actor))


@login_required
@require_http_methods(["POST"])
def account_action(request, actor_id):
    if not request.user.is_staff:
        return _error("not_permitted", 403)
    actor = get_object_or_404(Actor, pk=actor_id)
    try:
        data = _payload(request)
        record = account_services.apply_account_action(
            actor,
            request.user,
            data.get("action"),
            reason=data.get("reason"),
            enabled=data.get("enabled"),
        )
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
    except account_services.InvalidActionError:
        return _error("invalid_action", 400)
    except report_services.NotPermittedError:
        return _error("not_permitted", 403)
    except account_services.LocalOnlyError:
        return _error("local_only", 409)
    return JsonResponse(_serialize_account(actor))
```

URLs:

```python
    path(
        "moderation/accounts/<uuid:actor_id>",
        views.account_detail,
        name="moderation-account-detail",
    ),
    path(
        "moderation/accounts/<uuid:actor_id>/action",
        views.account_action,
        name="moderation-account-action",
    ),
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_moderation_accounts.py -q`
Expected: `7 passed`

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
uv run python manage.py makemigrations --check --dry-run
git add moderation/models.py moderation/migrations/0004_account_moderation.py moderation/accounts.py moderation/views.py moderation/urls.py tests/test_moderation_accounts.py
git commit -m "feat: add instance account moderation actions"
```

---

### Task 2: Account enforcement across federation, public AP, and publishing

**Files:**
- Modify: `config/settings/base.py`
- Modify: `federation/inbound.py`, `federation/remotes.py`, `federation/delivery.py`
- Modify: `social/services.py`, `comics/views.py`, `social/views.py`, `federation/views.py`
- Modify: `comics/publishing.py`
- Modify: `tests/test_moderation_accounts.py`

**Interfaces:**
- Consumes: Task 1 helpers.
- Produces: `FEDERATION_ALLOWLIST_ENABLED`; `accounts.should_drop_inbound(actor)`; `accounts.allowed_domain(domain)`; enforcement in the seams above.

- [ ] **Step 1: Add failing enforcement tests**

Append to `tests/test_moderation_accounts.py` (top imports: `from django.test import Client` if needed, `from social.models import Follow, FollowStatus`, `from social.services import follow, follower_inboxes`, `from federation.delivery import fan_out`, `from comics.views import page_detail`, `from social.views import comment_detail`, `from comics.publishing import publish_page`):

```python
from django.utils import timezone

from actors.models import Instance
from federation.delivery import fan_out
from federation.models import Activity, Delivery
from comics.publishing import publish_page
from social.models import Follow, FollowStatus
from social.services import follower_inboxes, follow
from comics.services import create_page as make_page


@pytest.mark.django_db
def test_suspended_accounts_are_dropped_inbound():
    _, actor = _local()
    staff = _user(staff=True)
    accounts.apply_account_action(actor, staff, "suspend", reason="x")
    assert accounts.should_drop_inbound(actor) is True


@pytest.mark.django_db
def test_allowlist_mode_drops_non_allowlisted_domains(settings):
    settings.FEDERATION_ALLOWLIST_ENABLED = True
    remote = _remote()
    assert accounts.should_drop_inbound(remote) is True
    Instance.objects.create(domain="remote.test", allowlisted=True)
    assert accounts.should_drop_inbound(remote) is False


@pytest.mark.django_db
def test_blocked_domain_delivery_is_skipped():
    _, actor = _local()
    remote = _remote()
    Instance.objects.create(domain="remote.test", blocked=True)
    activity = Activity.objects.create(
        ap_id="https://testserver/activities/1", type="Create", actor=actor, payload={}
    )
    assert fan_out(activity.payload, [remote.inbox], actor) == []
    assert Delivery.objects.count() == 0


@pytest.mark.django_db
def test_suspended_followers_are_excluded_and_following_suspended_is_rejected():
    _, actor = _local()
    follower_user, follower = _local()
    Follow.objects.create(follower=follower, target=actor, status=FollowStatus.ACCEPTED)
    staff = _user(staff=True)
    accounts.apply_account_action(follower, staff, "suspend", reason="x")
    assert follower_inboxes(actor) == []
    target = _local()[1]
    accounts.apply_account_action(target, staff, "suspend", reason="x")
    with pytest.raises(ValidationError):
        follow(actor, target)
```

(Import `ValidationError` from `django.core.exceptions`.)

```python
@pytest.mark.django_db
def test_public_pages_and_comments_hide_suspended_content(client):
    user, actor = _local()
    comic = create_comic(user, f"comic{uuid4().hex[:6]}", "Comic")
    series = create_series(user, comic, "Main")
    page = make_page(user, series, title="Page")
    Page.objects.filter(pk=page.pk).update(
        status="published",
        ap_id=f"https://testserver/pages/{page.id}",
        published_at=timezone.now(),
    )
    staff = _user(staff=True)
    accounts.apply_account_action(comic.actor, staff, "suspend", reason="x")
    assert client.get(f"/pages/{page.id}").status_code == 404


@pytest.mark.django_db
def test_frozen_users_cannot_publish_and_force_sensitive_marks_pages():
    user, actor = _local()
    comic = create_comic(user, f"comic{uuid4().hex[:6]}", "Comic")
    series = create_series(user, comic, "Main")
    page = make_page(user, series, title="Page")
    staff = _user(staff=True)
    accounts.apply_account_action(actor, staff, "freeze", reason="x")
    with pytest.raises(PermissionDenied):
        publish_page(user, page)
```

(Import `PermissionDenied`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_moderation_accounts.py -q -k "inbound or allowlist or delivery or followers or frozen or public"`
Expected: missing `should_drop_inbound`, delivery still enqueues, 200 instead of 404, etc.

- [ ] **Step 3: Add settings and helpers**

`config/settings/base.py` after federation settings:

```python
FEDERATION_ALLOWLIST_ENABLED = env.bool(
    "FEDERATION_ALLOWLIST_ENABLED", default=False
)
```

`moderation/accounts.py`:

```python
def allowed_domain(domain):
    if not domain:
        return True
    instance = Instance.objects.filter(domain=domain).first()
    if instance is not None and instance.blocked:
        return False
    if settings.FEDERATION_ALLOWLIST_ENABLED:
        return instance is not None and instance.allowlisted
    return True


def should_drop_inbound(actor):
    return is_suspended(actor) or not allowed_domain(actor.domain)
```

- [ ] **Step 4: Hook inbound, fetch, delivery, feeds, public views, publishing**

`federation/inbound.py` after the existing blocked-domain check (so blocked
domains keep their 403):

```python
    from moderation import accounts

    if accounts.should_drop_inbound(actor):
        return JsonResponse({"accepted": True}, status=202)
```

`federation/remotes.py` in `fetch_remote_actor` after `domain = urlparse(ap_id).netloc`:

```python
    from moderation import accounts

    if not accounts.allowed_domain(domain):
        cache.set(failed_key, 1, timeout=FETCH_FAILED_TTL)
        return None
```

`federation/delivery.py`:

```python
from urllib.parse import urlparse
```

```python
def _allowed(url):
    from moderation import accounts

    domain = urlparse(url).netloc
    return accounts.allowed_domain(domain)


def fan_out(activity, inbox_urls, actor: Actor):
    return [
        enqueue_delivery(activity, url, actor)
        for url in unique_inboxes(inbox_urls)
        if _allowed(url)
    ]
```

`social/services.py` in `follower_inboxes` add
`.exclude(follower__moderation__state__in=["suspended", "disabled"])`, and in
`follow` after the self check:

```python
    if accounts.is_suspended(target) or accounts.is_disabled(target):
        raise ValidationError("You cannot follow this account.")
```

(import `from moderation import accounts`).

`comics/views.py` `page_detail` after fetching the page:

```python
    from moderation import accounts

    if not accounts.publicly_visible(page.series.comic.actor):
        raise Http404
```

`social/views.py` `comment_detail` after fetching the comment:

```python
    from moderation import accounts

    if not accounts.publicly_visible(comment.actor):
        raise Http404
```

`federation/views.py` in each of `actor_outbox`, `actor_followers`,
`actor_following` after fetching the actor:

```python
    from moderation import accounts

    if not accounts.publicly_visible(actor):
        raise Http404
```

`comics/publishing.py` in `publish_page` after the permission check:

```python
    from moderation import accounts

    if not accounts.can_post(user):
        raise PermissionDenied("This account cannot publish.")
    if accounts.is_force_sensitive(user):
        page.sensitive = True
        page.save(update_fields=["sensitive", "updated_at"])
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_moderation_accounts.py -q`
Expected: `12 passed`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add config/settings/base.py federation/inbound.py federation/remotes.py federation/delivery.py social/services.py comics/views.py social/views.py federation/views.py comics/publishing.py tests/test_moderation_accounts.py
git commit -m "feat: enforce account moderation across federation"
```

---

### Task 3: Domain policy suite

**Files:**
- Modify: `moderation/services.py` (expose `normalize_domain`)
- Create: `moderation/domains.py`
- Modify: `moderation/views.py`, `moderation/urls.py`
- Create: `tests/test_moderation_domains.py`

**Interfaces:**
- Consumes: `Instance`, `record_action`, 10a normalization.
- Produces: `normalize_domain`; `apply_domain_action`, `domain_policy`; errors `InvalidActionError`, `InvalidDomainError`.

- [ ] **Step 1: Write failing domain tests**

Create `tests/test_moderation_domains.py`:

```python
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from actors.models import Instance
from moderation import domains
from moderation.models import ModerationAudit


def _user(staff=False):
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x", is_staff=staff
    )


@pytest.mark.django_db
def test_domain_actions_set_and_restore_flags():
    staff = _user(staff=True)
    domains.apply_domain_action("Remote.Test", staff, "suspend")
    instance = Instance.objects.get(domain="remote.test")
    assert instance.blocked is True
    domains.apply_domain_action("remote.test", staff, "silence")
    domains.apply_domain_action("remote.test", staff, "reject_media")
    domains.apply_domain_action("remote.test", staff, "reject_reports")
    domains.apply_domain_action("remote.test", staff, "allowlist")
    instance.refresh_from_db()
    assert instance.silenced and instance.reject_media and instance.reject_reports
    assert instance.allowlisted is True
    domains.apply_domain_action("remote.test", staff, "restore")
    instance.refresh_from_db()
    assert not any(
        [instance.blocked, instance.silenced, instance.reject_media,
         instance.reject_reports, instance.allowlisted]
    )
    assert ModerationAudit.objects.filter(action="domain.suspended").count() == 1


@pytest.mark.django_db
def test_domain_validation_and_permissions():
    staff = _user(staff=True)
    with pytest.raises(domains.InvalidDomainError):
        domains.apply_domain_action("testserver", staff, "suspend")
    with pytest.raises(domains.InvalidActionError):
        domains.apply_domain_action("remote.test", staff, "nope")
    with pytest.raises(domains.NotPermittedError):
        domains.apply_domain_action("remote.test", _user(), "suspend")


@pytest.mark.django_db
def test_domain_endpoints(client):
    staff = _user(staff=True)
    client.force_login(_user())
    assert (
        client.post(
            "/moderation/domains/remote.test/action",
            data='{"action": "suspend"}',
            content_type="application/json",
        ).status_code
        == 403
    )
    client.force_login(staff)
    response = client.post(
        "/moderation/domains/remote.test/action",
        data='{"action": "suspend"}',
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["blocked"] is True
    assert client.get("/moderation/domains/remote.test").json()["blocked"] is True
    assert (
        client.post(
            "/moderation/domains/remote.test/action",
            data='{"action": "nope"}',
            content_type="application/json",
        ).status_code
        == 400
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_moderation_domains.py -q`
Expected: `ImportError: cannot import name 'domains'`.

- [ ] **Step 3: Expose normalization and implement the service**

In `moderation/services.py`, rename `_host` to `normalize_domain` and keep
`_host = normalize_domain` for existing callers.

Create `moderation/domains.py`:

```python
from actors.models import Instance
from moderation.models import ModerationAudit
from moderation.reports import NotPermittedError, record_action
from moderation.services import InvalidDomainError, normalize_domain


class InvalidActionError(Exception):
    pass


ACTIONS = {
    "suspend": ("blocked", True, "domain.suspended"),
    "silence": ("silenced", True, "domain.silenced"),
    "reject_media": ("reject_media", True, "domain.reject_media"),
    "reject_reports": ("reject_reports", True, "domain.reject_reports"),
    "allowlist": ("allowlisted", True, "domain.allowlisted"),
    "restore": (None, False, "domain.restored"),
}


def _instance(domain, create=True):
    host = normalize_domain(domain)
    if not host:
        raise InvalidDomainError
    if create:
        instance, _ = Instance.objects.get_or_create(domain=host)
        return instance
    return Instance.objects.filter(domain=host).first()


def domain_policy(domain):
    instance = _instance(domain, create=False)
    if instance is None:
        return None
    return {
        "domain": instance.domain,
        "blocked": instance.blocked,
        "silenced": instance.silenced,
        "reject_media": instance.reject_media,
        "reject_reports": instance.reject_reports,
        "allowlisted": instance.allowlisted,
    }


def apply_domain_action(domain, staff, action):
    if staff is None or not staff.is_staff:
        raise NotPermittedError
    if action not in ACTIONS:
        raise InvalidActionError
    instance = _instance(domain)
    field, value, audit_action = ACTIONS[action]
    if action == "restore":
        instance.blocked = False
        instance.silenced = False
        instance.reject_media = False
        instance.reject_reports = False
        instance.allowlisted = False
        instance.save(
            update_fields=[
                "blocked",
                "silenced",
                "reject_media",
                "reject_reports",
                "allowlisted",
                "updated_at",
            ]
        )
    else:
        setattr(instance, field, value)
        instance.save(update_fields=[field, "updated_at"])
    record_action(staff, audit_action, target=f"domain:{instance.domain}")
    return instance
```

- [ ] **Step 4: Add endpoints and URLs**

In `moderation/views.py` (import `from moderation import domains as domain_services`):

```python
@login_required
@require_http_methods(["GET"])
def domain_detail(request, domain):
    if not request.user.is_staff:
        return _error("not_permitted", 403)
    try:
        policy = domain_services.domain_policy(domain)
    except domain_services.InvalidDomainError:
        return _error("invalid_domain", 400)
    if policy is None:
        raise Http404
    return JsonResponse(policy)


@login_required
@require_http_methods(["POST"])
def domain_action(request, domain):
    if not request.user.is_staff:
        return _error("not_permitted", 403)
    try:
        data = _payload(request)
        instance = domain_services.apply_domain_action(
            domain, request.user, data.get("action")
        )
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
    except domain_services.InvalidDomainError:
        return _error("invalid_domain", 400)
    except domain_services.InvalidActionError:
        return _error("invalid_action", 400)
    except report_services.NotPermittedError:
        return _error("not_permitted", 403)
    return JsonResponse(
        {
            "domain": instance.domain,
            "blocked": instance.blocked,
            "silenced": instance.silenced,
            "reject_media": instance.reject_media,
            "reject_reports": instance.reject_reports,
            "allowlisted": instance.allowlisted,
        }
    )
```

URLs:

```python
    path(
        "moderation/domains/<str:domain>",
        views.domain_detail,
        name="moderation-domain-detail",
    ),
    path(
        "moderation/domains/<str:domain>/action",
        views.domain_action,
        name="moderation-domain-action",
    ),
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_moderation_domains.py -q`
Expected: `3 passed`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add moderation/services.py moderation/domains.py moderation/views.py moderation/urls.py tests/test_moderation_domains.py
git commit -m "feat: add domain moderation actions"
```

---

### Task 4: Registration email/IP blocks

**Files:**
- Modify: `moderation/models.py`
- Create: `moderation/migrations/0005_registration_blocks.py` (generated)
- Create: `moderation/registration.py`
- Modify: `moderation/views.py`, `moderation/urls.py`
- Modify: `accounts/forms.py`, `accounts/views.py`
- Create: `tests/test_moderation_registration.py`

**Interfaces:**
- Consumes: `normalize_domain`, `record_action`.
- Produces: `BlockedEmailDomain`, `BlockedIP`; `add_email_domain`, `remove_email_domain`, `email_domain_blocked`, `add_ip`, `remove_ip`, `ip_blocked`; staff endpoints.

- [ ] **Step 1: Write failing registration tests**

Create `tests/test_moderation_registration.py`:

```python
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from moderation import registration
from moderation.models import BlockedEmailDomain, BlockedIP, ModerationAudit


def _user(staff=False):
    return get_user_model().objects.create_user(
        email=f"u-{uuid4().hex[:8]}@example.com", password="x", is_staff=staff
    )


@pytest.mark.django_db
def test_email_domain_blocks_are_exact_and_case_insensitive():
    staff = _user(staff=True)
    registration.add_email_domain("Spam.Example", staff)
    assert BlockedEmailDomain.objects.get(domain="spam.example")
    assert registration.email_domain_blocked("x@spam.example") is True
    assert registration.email_domain_blocked("x@SPAM.example") is True
    assert registration.email_domain_blocked("x@notspam.example") is False
    registration.remove_email_domain("spam.example", staff)
    assert BlockedEmailDomain.objects.count() == 0
    assert ModerationAudit.objects.filter(
        action="registration.email_domain_added"
    ).count() == 1


@pytest.mark.django_db
def test_ip_blocks_support_cidr():
    staff = _user(staff=True)
    registration.add_ip("203.0.113.0/24", staff)
    assert registration.ip_blocked("203.0.113.9") is True
    assert registration.ip_blocked("198.51.100.1") is False
    with pytest.raises(registration.InvalidAddressError):
        registration.add_ip("not-an-ip", staff)


@pytest.mark.django_db
def test_registration_endpoints(client):
    staff = _user(staff=True)
    client.force_login(_user())
    assert client.get("/moderation/registration-blocks/email-domains").status_code == 403
    client.force_login(staff)
    response = client.post(
        "/moderation/registration-blocks/email-domains",
        data='{"domain": "spam.example"}',
        content_type="application/json",
    )
    assert response.status_code == 201
    listing = client.get("/moderation/registration-blocks/email-domains").json()
    assert listing["domains"] == ["spam.example"]
    response = client.delete(
        "/moderation/registration-blocks/email-domains",
        data='{"domain": "spam.example"}',
        content_type="application/json",
    )
    assert response.status_code == 200
    response = client.post(
        "/moderation/registration-blocks/ip-blocks",
        data='{"address": "203.0.113.0/24"}',
        content_type="application/json",
    )
    assert response.status_code == 201
    assert client.get("/moderation/registration-blocks/ip-blocks").json()[
        "addresses"
    ] == ["203.0.113.0/24"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_moderation_registration.py -q`
Expected: `ImportError: cannot import name 'registration'`.

- [ ] **Step 3: Implement models and service**

Append to `moderation/models.py`:

```python
class BlockedEmailDomain(UUIDModel):
    domain = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class BlockedIP(UUIDModel):
    address = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

Run: `uv run python manage.py makemigrations moderation`.

Create `moderation/registration.py`:

```python
import ipaddress

from moderation.models import BlockedEmailDomain, BlockedIP
from moderation.reports import NotPermittedError, record_action
from moderation.services import InvalidDomainError, normalize_domain


class InvalidAddressError(Exception):
    pass


def _require_staff(staff):
    if staff is None or not staff.is_staff:
        raise NotPermittedError


def add_email_domain(domain, staff):
    _require_staff(staff)
    host = normalize_domain(domain)
    if not host:
        raise InvalidDomainError
    row, created = BlockedEmailDomain.objects.get_or_create(domain=host)
    if created:
        record_action(
            staff, "registration.email_domain_added", target=f"email:{host}"
        )
    return row


def remove_email_domain(domain, staff):
    _require_staff(staff)
    host = normalize_domain(domain)
    if not host:
        raise InvalidDomainError
    deleted, _ = BlockedEmailDomain.objects.filter(domain=host).delete()
    if deleted:
        record_action(
            staff, "registration.email_domain_removed", target=f"email:{host}"
        )
    return bool(deleted)


def email_domains():
    return list(BlockedEmailDomain.objects.order_by("domain"))


def email_domain_blocked(email):
    if not isinstance(email, str) or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    return BlockedEmailDomain.objects.filter(domain=domain).exists()


def _network(address):
    try:
        return ipaddress.ip_network(str(address).strip(), strict=False)
    except (ValueError, TypeError) as exc:
        raise InvalidAddressError from exc


def add_ip(address, staff):
    _require_staff(staff)
    network = _network(address)
    value = str(network)
    row, created = BlockedIP.objects.get_or_create(address=value)
    if created:
        record_action(staff, "registration.ip_added", target=f"ip:{value}")
    return row


def remove_ip(address, staff):
    _require_staff(staff)
    network = _network(address)
    value = str(network)
    deleted, _ = BlockedIP.objects.filter(address=value).delete()
    if deleted:
        record_action(staff, "registration.ip_removed", target=f"ip:{value}")
    return bool(deleted)


def ips():
    return list(BlockedIP.objects.order_by("address"))


def ip_blocked(address):
    try:
        ip = ipaddress.ip_address(str(address))
    except ValueError:
        return False
    return any(ip in _network(row.address) for row in BlockedIP.objects.all())
```

- [ ] **Step 4: Add endpoints and signup checks**

In `moderation/views.py` (import `from moderation import registration as registration_services`):

```python
@login_required
@require_http_methods(["GET", "POST", "DELETE"])
def registration_email_domains(request):
    if not request.user.is_staff:
        return _error("not_permitted", 403)
    if request.method == "GET":
        return JsonResponse(
            {"domains": [row.domain for row in registration_services.email_domains()]}
        )
    try:
        data = _payload(request)
        if request.method == "POST":
            row = registration_services.add_email_domain(
                data.get("domain"), request.user
            )
            return JsonResponse({"domain": row.domain}, status=201)
        registration_services.remove_email_domain(data.get("domain"), request.user)
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
    except (registration_services.InvalidDomainError, report_services.NotPermittedError):
        return _error("invalid_domain", 400)
    return JsonResponse({"removed": True})


@login_required
@require_http_methods(["GET", "POST", "DELETE"])
def registration_ips(request):
    if not request.user.is_staff:
        return _error("not_permitted", 403)
    if request.method == "GET":
        return JsonResponse(
            {"addresses": [row.address for row in registration_services.ips()]}
        )
    try:
        data = _payload(request)
        if request.method == "POST":
            row = registration_services.add_ip(data.get("address"), request.user)
            return JsonResponse({"address": row.address}, status=201)
        registration_services.remove_ip(data.get("address"), request.user)
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
    except (registration_services.InvalidAddressError, report_services.NotPermittedError):
        return _error("invalid_address", 400)
    return JsonResponse({"removed": True})
```

URLs:

```python
    path(
        "moderation/registration-blocks/email-domains",
        views.registration_email_domains,
        name="moderation-registration-email-domains",
    ),
    path(
        "moderation/registration-blocks/ip-blocks",
        views.registration_ips,
        name="moderation-registration-ips",
    ),
```

`accounts/forms.py`: in `clean_email`:

```python
    def clean_email(self):
        email = super().clean_email()
        from moderation import registration

        if registration.email_domain_blocked(email):
            raise ValidationError("Signups from this email domain are not allowed.")
        return email
```

`accounts/views.py`: in `SignupView.form_valid` before `super()`:

```python
        from moderation import registration

        if registration.ip_blocked(self.request.META.get("REMOTE_ADDR", "")):
            form.add_error(None, "Signups from your network are not allowed.")
            return self.form_invalid(form)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_moderation_registration.py -q`
Expected: `3 passed`

- [ ] **Step 6: Full suite, lint, migration check**

Run: `uv run pytest -q` (expect existing + new, no failures).

Run: `uv run ruff check .`, `uv run ruff format --check .`, and
`uv run python manage.py makemigrations --check --dry-run`.

- [ ] **Step 7: Commit**

```bash
git add moderation/models.py moderation/migrations/0005_registration_blocks.py moderation/registration.py moderation/views.py moderation/urls.py accounts/forms.py accounts/views.py tests/test_moderation_registration.py
git commit -m "feat: block abusive registration emails and IPs"
```
