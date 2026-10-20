# Self-Service Moderation (10a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users mutes accounts, threads, keywords, and remote domains, and remove followers — locally, with suppression applied when notifications are created.

**Architecture:** `moderation` gains four preference models and a service layer used by JSON endpoints and by `social.notifications.notify`. All checks are user-scoped lookups over the unified `Actor`/`Page` rows; nothing federates and nothing is deleted except the local follow.

**Tech Stack:** Python 3.14, Django 6.1.1, pytest-django.

**Spec:** `docs/superpowers/specs/2026-10-18-self-service-moderation-design.md`

## Global Constraints

- Everything in 10a is **local-only**: no `Activity`/delivery rows, no federation calls.
- Expiry is evaluated in queries (`expires_at IS NULL OR expires_at > now`); never rely on a cleanup job.
- `notification_allowed` is the only notification-suppression gate; comment storage and serving are unchanged.
- Endpoints are `login_required`, CSRF-protected JSON, and return the documented `{"error": ...}` codes.
- Timezone-aware future timestamps only for `expires_at`; phrases are trimmed/1–255 chars; domains are lowercased hostnames and never the local domain.
- Keep `ruff check .` and `ruff format --check .` clean; run `uv run python manage.py makemigrations --check --dry-run` before final commits.

## Review Focus

- **Suppression correctness:** only `hide_notifications` mutes suppress notifications; expired rows never suppress; thread/domain/filter suppression targets the right object.
- **Ownership boundaries:** muting yourself or an owned comic actor is rejected; remove-follower requires managing the actor and the follow must target it.
- **Input validation:** naive/past `expires_at`, empty/too-long phrases, malformed or local domains, non-boolean toggles, and malformed JSON are 400s before any write.
- **Matching scope:** keyword matching is case-insensitive against comment text, page/series/comic title, and actor name/handle, with no match when there is no text.
- **Local-only:** muting, filtering, domain-blocking, and follower removal create no `Activity`, no deliveries, and no content changes.

---

### Task 1: Preference models and moderation services

**Files:**
- Modify: `moderation/models.py`
- Create: `moderation/migrations/0001_initial.py` (generated)
- Create: `moderation/services.py`
- Create: `tests/test_moderation.py`

**Interfaces:**
- Consumes: `Actor`, `Comic`, `ComicRole`, `Page`, `Follow`, `settings.INSTANCE_DOMAIN`.
- Produces:
  - Models `Mute`, `ThreadMute`, `KeywordFilter`, `DomainBlock`.
  - Errors `InvalidExpiryError`, `InvalidPhraseError`, `InvalidDomainError`, `InvalidPayloadError`, `SelfMuteError`, `NotActorOwnerError`, `FollowNotFoundError`.
  - `active_mute`, `is_muted`, `is_thread_muted`, `is_domain_blocked`, `matching_filter`, `is_filtered`, `active_filters`, `notification_allowed`, `mute_actor`, `unmute_actor`, `mute_thread`, `unmute_thread`, `add_filter`, `remove_filter`, `block_domain`, `unblock_domain`, `can_manage_actor`, `remove_follower`.

- [ ] **Step 1: Write the failing service tests**

Create `tests/test_moderation.py`:

```python
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from actors.models import Actor, ActorType
from comics.services import create_comic, create_page, create_series
from moderation import services
from moderation.models import DomainBlock, KeywordFilter, Mute, ThreadMute
from social.models import Follow, FollowStatus


def _user(email=None):
    return get_user_model().objects.create_user(
        email=email or f"u-{uuid4().hex[:8]}@example.com", password="x"
    )


def _remote(handle="bob", domain="remote.test"):
    return Actor.objects.create(
        ap_id=f"https://{domain}/actors/{handle}",
        type=ActorType.PERSON,
        handle=handle,
        domain=domain,
        name="Bob",
    )


def _scene():
    owner = _user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series, title="The Landing")
    return owner, comic, page


def _future(**kwargs):
    return timezone.now() + timedelta(**kwargs)


@pytest.mark.django_db
def test_mute_is_idempotent_and_updates_the_toggle():
    user = _user()
    actor = _remote()
    first = services.mute_actor(user, actor)
    assert first.hide_notifications is False
    again = services.mute_actor(user, actor, hide_notifications=True)
    assert again.pk == first.pk
    assert Mute.objects.count() == 1
    assert services.is_muted(user, actor) is True


@pytest.mark.django_db
def test_mute_rejects_self_and_owned_comic_actors():
    owner, comic, _ = _scene()
    personal = Actor.objects.create(
        ap_id=f"http://testserver/actors/{uuid4().hex}",
        handle=uuid4().hex,
        domain="",
        user=owner,
    )
    with pytest.raises(services.SelfMuteError):
        services.mute_actor(owner, personal)
    with pytest.raises(services.SelfMuteError):
        services.mute_actor(owner, comic.actor)


@pytest.mark.django_db
def test_expired_mute_is_inactive():
    user = _user()
    actor = _remote()
    services.mute_actor(user, actor, expires_at=_future(days=1))
    Mute.objects.filter(user=user, actor=actor).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert services.is_muted(user, actor) is False


@pytest.mark.django_db
def test_expiry_validation_rejects_naive_and_past():
    user = _user()
    actor = _remote()
    with pytest.raises(services.InvalidExpiryError):
        services.mute_actor(user, actor, expires_at=timezone.now())
    with pytest.raises(services.InvalidExpiryError):
        services.mute_actor(user, actor, expires_at=timezone.now() - timedelta(days=1))


@pytest.mark.django_db
def test_thread_mute_round_trip():
    user = _user()
    _, _, page = _scene()
    services.mute_thread(user, page)
    assert services.is_thread_muted(user, page) is True
    services.unmute_thread(user, page)
    assert services.is_thread_muted(user, page) is False


@pytest.mark.django_db
def test_filter_matches_comment_title_and_actor_case_insensitively():
    user = _user()
    _, _, page = _scene()
    actor = _remote()
    services.add_filter(user, "LUNAR")
    assert services.is_filtered(user, page=page) is True
    assert services.is_filtered(user, actor=actor) is False
    services.add_filter(user, "bob")
    assert services.is_filtered(user, actor=actor) is True


@pytest.mark.django_db
def test_expired_filter_does_not_match():
    user = _user()
    _, _, page = _scene()
    services.add_filter(user, "lunar", expires_at=_future(days=1))
    KeywordFilter.objects.filter(user=user).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert services.is_filtered(user, page=page) is False


@pytest.mark.django_db
def test_filter_validation():
    user = _user()
    with pytest.raises(services.InvalidPhraseError):
        services.add_filter(user, "   ")
    with pytest.raises(services.InvalidPhraseError):
        services.add_filter(user, "x" * 256)


@pytest.mark.django_db
def test_domain_block_normalizes_and_rejects_local():
    user = _user()
    block = services.block_domain(user, " Remote.Test ")
    assert block.domain == "remote.test"
    assert services.is_domain_blocked(user, "remote.test") is True
    assert DomainBlock.objects.count() == 1
    services.unblock_domain(user, "remote.test")
    assert services.is_domain_blocked(user, "remote.test") is False
    with pytest.raises(services.InvalidDomainError):
        services.block_domain(user, "not a domain")
    with pytest.raises(services.InvalidDomainError):
        services.block_domain(user, "testserver")


@pytest.mark.django_db
def test_remove_follower_requires_managing_the_actor():
    owner, comic, _ = _scene()
    follower = _remote()
    follow = Follow.objects.create(
        follower=follower, target=comic.actor, status=FollowStatus.ACCEPTED
    )
    with pytest.raises(services.NotActorOwnerError):
        services.remove_follower(_user(), comic.actor, follow)
    assert services.can_manage_actor(owner, comic.actor) is True
    services.remove_follower(owner, comic.actor, follow)
    assert not Follow.objects.filter(pk=follow.pk).exists()


@pytest.mark.django_db
def test_remove_follower_rejects_mismatched_target():
    owner, comic, _ = _scene()
    other = _remote(handle="other")
    follow = Follow.objects.create(follower=_remote(), target=other)
    with pytest.raises(services.FollowNotFoundError):
        services.remove_follower(owner, comic.actor, follow)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_moderation.py -q`
Expected: `ImportError: cannot import name 'services'`.

- [ ] **Step 3: Implement the models**

Replace `moderation/models.py` content with:

```python
from django.conf import settings
from django.db import models

from core.models import UUIDModel


class Mute(UUIDModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mutes"
    )
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="muted_by"
    )
    hide_notifications = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "actor"], name="unique_mute")
        ]


class ThreadMute(UUIDModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="thread_mutes"
    )
    page = models.ForeignKey(
        "comics.Page", on_delete=models.CASCADE, related_name="thread_mutes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "page"], name="unique_thread_mute")
        ]


class KeywordFilter(UUIDModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="keyword_filters"
    )
    phrase = models.CharField(max_length=255)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "phrase"], name="unique_user_phrase"
            )
        ]


class DomainBlock(UUIDModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="domain_blocks"
    )
    domain = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "domain"], name="unique_user_domain")
        ]
```

Run: `uv run python manage.py makemigrations moderation`
Expected: creates `moderation/migrations/0001_initial.py`.

- [ ] **Step 4: Implement the services**

Create `moderation/services.py`:

```python
import re

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from comics.models import Comic, ComicRole
from moderation.models import DomainBlock, KeywordFilter, Mute, ThreadMute

DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)


class InvalidExpiryError(Exception):
    pass


class InvalidPhraseError(Exception):
    pass


class InvalidDomainError(Exception):
    pass


class InvalidPayloadError(Exception):
    pass


class SelfMuteError(Exception):
    pass


class NotActorOwnerError(Exception):
    pass


class FollowNotFoundError(Exception):
    pass


def _active(queryset):
    now = timezone.now()
    return queryset.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def _validate_expiry(expires_at):
    if expires_at is None:
        return
    if timezone.is_naive(expires_at) or expires_at <= timezone.now():
        raise InvalidExpiryError


def can_manage_actor(user, actor):
    if user is None or actor is None:
        return False
    if actor.user_id == user.id:
        return True
    return Comic.objects.filter(
        actor=actor, roles__user=user, roles__role=ComicRole.Role.OWNER
    ).exists()


def active_mute(user, actor):
    if user is None or actor is None:
        return None
    return _active(Mute.objects.filter(user=user, actor=actor)).first()


def is_muted(user, actor):
    return active_mute(user, actor) is not None


def mute_actor(user, actor, *, hide_notifications=False, expires_at=None):
    if can_manage_actor(user, actor):
        raise SelfMuteError
    _validate_expiry(expires_at)
    mute, _ = Mute.objects.update_or_create(
        user=user,
        actor=actor,
        defaults={
            "hide_notifications": hide_notifications,
            "expires_at": expires_at,
        },
    )
    return mute


def unmute_actor(user, actor):
    return Mute.objects.filter(user=user, actor=actor).delete()[0]


def mute_thread(user, page):
    mute, _ = ThreadMute.objects.get_or_create(user=user, page=page)
    return mute


def unmute_thread(user, page):
    return ThreadMute.objects.filter(user=user, page=page).delete()[0]


def is_thread_muted(user, page):
    if user is None or page is None:
        return False
    return ThreadMute.objects.filter(user=user, page=page).exists()


def add_filter(user, phrase, *, expires_at=None):
    phrase = (phrase or "").strip()
    if not phrase or len(phrase) > 255:
        raise InvalidPhraseError
    _validate_expiry(expires_at)
    keyword, _ = KeywordFilter.objects.update_or_create(
        user=user, phrase=phrase, defaults={"expires_at": expires_at}
    )
    return keyword


def remove_filter(user, filter_id):
    return KeywordFilter.objects.filter(user=user, pk=filter_id).delete()[0]


def active_filters(user):
    return _active(KeywordFilter.objects.filter(user=user)).order_by("created_at")


def _filter_texts(actor=None, page=None, comment=None):
    texts = []
    if comment is not None:
        texts.append(comment.content)
    if page is not None:
        texts.append(page.title)
        texts.append(page.series.title)
        texts.append(page.series.comic.title)
    if actor is not None:
        texts.append(actor.name)
        texts.append(actor.handle)
    return [text.casefold() for text in texts if text]


def matching_filter(user, *, actor=None, page=None, comment=None):
    if user is None:
        return None
    texts = _filter_texts(actor=actor, page=page, comment=comment)
    if not texts:
        return None
    for keyword in active_filters(user):
        phrase = keyword.phrase.casefold()
        if phrase and any(phrase in text for text in texts):
            return keyword
    return None


def is_filtered(user, *, actor=None, page=None, comment=None):
    return matching_filter(user, actor=actor, page=page, comment=comment) is not None


def block_domain(user, domain):
    normalized = (domain or "").strip().lower()
    if not normalized or normalized == settings.INSTANCE_DOMAIN.lower():
        raise InvalidDomainError
    if not DOMAIN_RE.match(normalized):
        raise InvalidDomainError
    block, _ = DomainBlock.objects.get_or_create(user=user, domain=normalized)
    return block


def unblock_domain(user, domain):
    normalized = (domain or "").strip().lower()
    return DomainBlock.objects.filter(user=user, domain=normalized).delete()[0]


def is_domain_blocked(user, domain):
    if user is None or not domain:
        return False
    return DomainBlock.objects.filter(
        user=user, domain=domain.strip().lower()
    ).exists()


def notification_allowed(user, *, actor=None, page=None, comment=None):
    if user is None:
        return True
    mute = active_mute(user, actor)
    if mute is not None and mute.hide_notifications:
        return False
    if is_thread_muted(user, page):
        return False
    if actor is not None and actor.domain and is_domain_blocked(user, actor.domain):
        return False
    if matching_filter(user, actor=actor, page=page, comment=comment) is not None:
        return False
    return True


def remove_follower(user, actor, follow):
    if not can_manage_actor(user, actor):
        raise NotActorOwnerError
    if follow.target_id != actor.id:
        raise FollowNotFoundError
    follow.delete()
    return True
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_moderation.py -q`
Expected: `11 passed`

- [ ] **Step 6: Lint, migration check, and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
uv run python manage.py makemigrations --check --dry-run
git add moderation/models.py moderation/migrations/0001_initial.py moderation/services.py tests/test_moderation.py
git commit -m "feat: add self-service moderation preferences"
```

---

### Task 2: JSON endpoints

**Files:**
- Create: `moderation/views.py`
- Create: `moderation/urls.py`
- Modify: `config/urls.py`
- Modify: `tests/test_moderation.py`

**Interfaces:**
- Consumes: the Task 1 services and errors; `Actor`, `Page`, `Follow`, `KeywordFilter`.
- Produces: `views.mutes`, `views.thread_mutes`, `views.filters`, `views.filter_detail`, `views.domain_blocks`, `views.remove_follower`; URL names `moderation-mutes`, `moderation-thread-mutes`, `moderation-filters`, `moderation-filter-detail`, `moderation-domain-blocks`, `moderation-remove-follower`.

- [ ] **Step 1: Add failing endpoint tests**

Append to `tests/test_moderation.py`:

```python
import json

from django.utils.dateparse import parse_datetime


def _payload(response):
    return response.json()


@pytest.mark.django_db
def test_mute_endpoint_round_trip(client):
    user = _user()
    actor = _remote()
    client.force_login(user)
    expiry = _future(days=7).isoformat()

    response = client.post(
        f"/moderation/mutes/{actor.id}",
        data=json.dumps({"hide_notifications": True, "expires_at": expiry}),
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["muted"] is True
    assert body["hide_notifications"] is True
    assert parse_datetime(body["expires_at"]) is not None

    response = client.delete(f"/moderation/mutes/{actor.id}")
    assert response.status_code == 200
    assert response.json() == {"muted": False}
    assert services.is_muted(user, actor) is False


@pytest.mark.django_db
def test_mute_endpoint_validates_payload(client):
    user = _user()
    actor = _remote()
    client.force_login(user)
    response = client.post(
        f"/moderation/mutes/{actor.id}",
        data=json.dumps({"hide_notifications": "yes"}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_payload"}
    response = client.post(
        f"/moderation/mutes/{actor.id}",
        data=json.dumps({"expires_at": "not-a-date"}),
        content_type="application/json",
    )
    assert response.json() == {"error": "invalid_expiry"}
    response = client.post(
        f"/moderation/mutes/{actor.id}",
        data=json.dumps({"expires_at": timezone.now().isoformat()}),
        content_type="application/json",
    )
    assert response.json() == {"error": "invalid_expiry"}
    assert client.post(f"/moderation/mutes/{uuid4()}").status_code == 404


@pytest.mark.django_db
def test_mute_endpoint_rejects_self(client):
    owner, comic, _ = _scene()
    client.force_login(owner)
    response = client.post(f"/moderation/mutes/{comic.actor.id}")
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_target"}


@pytest.mark.django_db
def test_thread_mute_endpoint(client):
    user = _user()
    _, _, page = _scene()
    client.force_login(user)
    assert client.post(f"/moderation/thread-mutes/{page.id}").status_code == 200
    assert services.is_thread_muted(user, page) is True
    response = client.delete(f"/moderation/thread-mutes/{page.id}")
    assert response.json() == {"muted": False}
    assert services.is_thread_muted(user, page) is False


@pytest.mark.django_db
def test_filters_endpoint_create_list_delete(client):
    user = _user()
    client.force_login(user)
    response = client.post(
        "/moderation/filters",
        data=json.dumps({"phrase": "spoilers"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    filter_id = response.json()["id"]
    listing = client.get("/moderation/filters")
    assert [item["phrase"] for item in listing.json()["filters"]] == ["spoilers"]
    assert (
        client.delete(f"/moderation/filters/{filter_id}").json() == {"removed": True}
    )
    assert client.get("/moderation/filters").json() == {"filters": []}


@pytest.mark.django_db
def test_filters_endpoint_validation_and_ownership(client):
    user = _user()
    other = _user()
    client.force_login(user)
    bad = client.post(
        "/moderation/filters",
        data=json.dumps({"phrase": "  "}),
        content_type="application/json",
    )
    assert bad.status_code == 400
    assert bad.json() == {"error": "invalid_phrase"}
    keyword = services.add_filter(other, "secret")
    assert client.delete(f"/moderation/filters/{keyword.id}").status_code == 404


@pytest.mark.django_db
def test_domain_block_endpoint(client):
    user = _user()
    client.force_login(user)
    assert (
        client.post("/moderation/domain-blocks/Remote.Test").status_code == 200
    )
    assert services.is_domain_blocked(user, "remote.test") is True
    assert (
        client.post("/moderation/domain-blocks/bad_domain").status_code == 400
    )
    response = client.delete("/moderation/domain-blocks/remote.test")
    assert response.json() == {"blocked": False}
    assert services.is_domain_blocked(user, "remote.test") is False


@pytest.mark.django_db
def test_remove_follower_endpoint(client):
    owner, comic, _ = _scene()
    follow = Follow.objects.create(
        follower=_remote(), target=comic.actor, status=FollowStatus.ACCEPTED
    )
    client.force_login(owner)
    response = client.delete(
        f"/moderation/actors/{comic.actor.id}/followers/{follow.id}"
    )
    assert response.status_code == 200
    assert response.json() == {"removed": True}
    assert not Follow.objects.filter(pk=follow.pk).exists()


@pytest.mark.django_db
def test_remove_follower_endpoint_authorization(client):
    _, comic, _ = _scene()
    follow = Follow.objects.create(
        follower=_remote(), target=comic.actor, status=FollowStatus.ACCEPTED
    )
    client.force_login(_user())
    assert (
        client.delete(
            f"/moderation/actors/{comic.actor.id}/followers/{follow.id}"
        ).status_code
        == 403
    )
    other = _remote(handle="other")
    mismatch = Follow.objects.create(follower=_remote(handle="third"), target=other)
    owner_user = _user()
    own_comic = create_comic(owner_user, f"comic{uuid4().hex[:6]}", "Other")
    client.force_login(owner_user)
    assert (
        client.delete(
            f"/moderation/actors/{own_comic.actor.id}/followers/{mismatch.id}"
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_moderation_endpoints_require_login(client):
    actor = _remote()
    assert client.post(f"/moderation/mutes/{actor.id}").status_code == 302
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_moderation.py -q -k endpoint`
Expected: `404 != 200` (routes missing).

- [ ] **Step 3: Implement the views**

Create `moderation/views.py`:

```python
import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from actors.models import Actor
from comics.models import Page
from moderation import services
from moderation.models import KeywordFilter
from social.models import Follow


def _payload(request):
    if not request.body.strip():
        return {}
    try:
        data = json.loads(request.body)
    except ValueError as exc:
        raise services.InvalidPayloadError from exc
    if not isinstance(data, dict):
        raise services.InvalidPayloadError
    return data


def _expires_at(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise services.InvalidExpiryError
    parsed = parse_datetime(value)
    if parsed is None:
        raise services.InvalidExpiryError
    return parsed


def _error(code, status):
    return JsonResponse({"error": code}, status=status)


def _serialize_filter(keyword):
    return {
        "id": str(keyword.id),
        "phrase": keyword.phrase,
        "expires_at": keyword.expires_at.isoformat()
        if keyword.expires_at is not None
        else None,
    }


@login_required
@require_http_methods(["POST", "DELETE"])
def mutes(request, actor_id):
    actor = get_object_or_404(Actor, pk=actor_id)
    if request.method == "DELETE":
        services.unmute_actor(request.user, actor)
        return JsonResponse({"muted": False})
    try:
        data = _payload(request)
        hide = data.get("hide_notifications", False)
        if not isinstance(hide, bool):
            raise services.InvalidPayloadError
        mute = services.mute_actor(
            request.user,
            actor,
            hide_notifications=hide,
            expires_at=_expires_at(data.get("expires_at")),
        )
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
    except services.InvalidExpiryError:
        return _error("invalid_expiry", 400)
    except services.SelfMuteError:
        return _error("invalid_target", 400)
    return JsonResponse(
        {
            "muted": True,
            "hide_notifications": mute.hide_notifications,
            "expires_at": mute.expires_at.isoformat()
            if mute.expires_at is not None
            else None,
        }
    )


@login_required
@require_http_methods(["POST", "DELETE"])
def thread_mutes(request, page_id):
    page = get_object_or_404(Page, pk=page_id)
    if request.method == "DELETE":
        services.unmute_thread(request.user, page)
        return JsonResponse({"muted": False})
    services.mute_thread(request.user, page)
    return JsonResponse({"muted": True})


@login_required
@require_http_methods(["GET", "POST"])
def filters(request):
    if request.method == "GET":
        return JsonResponse(
            {
                "filters": [
                    _serialize_filter(keyword)
                    for keyword in services.active_filters(request.user)
                ]
            }
        )
    try:
        data = _payload(request)
        keyword = services.add_filter(
            request.user,
            data.get("phrase"),
            expires_at=_expires_at(data.get("expires_at")),
        )
    except services.InvalidPayloadError:
        return _error("invalid_payload", 400)
    except services.InvalidExpiryError:
        return _error("invalid_expiry", 400)
    except services.InvalidPhraseError:
        return _error("invalid_phrase", 400)
    return JsonResponse(_serialize_filter(keyword))


@login_required
@require_http_methods(["DELETE"])
def filter_detail(request, filter_id):
    keyword = KeywordFilter.objects.filter(pk=filter_id, user=request.user).first()
    if keyword is None:
        raise Http404
    services.remove_filter(request.user, keyword.pk)
    return JsonResponse({"removed": True})


@login_required
@require_http_methods(["POST", "DELETE"])
def domain_blocks(request, domain):
    if request.method == "DELETE":
        services.unblock_domain(request.user, domain)
        return JsonResponse({"blocked": False})
    try:
        services.block_domain(request.user, domain)
    except services.InvalidDomainError:
        return _error("invalid_domain", 400)
    return JsonResponse({"blocked": True})


@login_required
@require_http_methods(["DELETE"])
def remove_follower(request, actor_id, follow_id):
    actor = get_object_or_404(Actor, pk=actor_id)
    follow = get_object_or_404(Follow, pk=follow_id)
    try:
        services.remove_follower(request.user, actor, follow)
    except services.NotActorOwnerError:
        return _error("not_owner", 403)
    except services.FollowNotFoundError as exc:
        raise Http404 from exc
    return JsonResponse({"removed": True})
```

- [ ] **Step 4: Wire the URLs**

Create `moderation/urls.py`:

```python
from django.urls import path

from moderation import views

urlpatterns = [
    path("moderation/mutes/<uuid:actor_id>", views.mutes, name="moderation-mutes"),
    path(
        "moderation/thread-mutes/<uuid:page_id>",
        views.thread_mutes,
        name="moderation-thread-mutes",
    ),
    path("moderation/filters", views.filters, name="moderation-filters"),
    path(
        "moderation/filters/<uuid:filter_id>",
        views.filter_detail,
        name="moderation-filter-detail",
    ),
    path(
        "moderation/domain-blocks/<str:domain>",
        views.domain_blocks,
        name="moderation-domain-blocks",
    ),
    path(
        "moderation/actors/<uuid:actor_id>/followers/<uuid:follow_id>",
        views.remove_follower,
        name="moderation-remove-follower",
    ),
]
```

Add `path("", include("moderation.urls")),` to `config/urls.py` after the
`billing.urls` include.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_moderation.py -q`
Expected: `21 passed`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check . --fix
uv run ruff format .
git add moderation/views.py moderation/urls.py config/urls.py tests/test_moderation.py
git commit -m "feat: expose self-service moderation endpoints"
```

---

### Task 3: Notification gate

**Files:**
- Modify: `social/notifications.py`
- Modify: `tests/test_moderation.py`

**Interfaces:**
- Consumes: `services.notification_allowed`.
- Produces: `notify` returns `None` for suppressed notifications.

- [ ] **Step 1: Add failing gate tests**

Append to `tests/test_moderation.py` (and add these imports at the top):

```python
from moderation.services import mute_actor
from social.models import NotificationType
from social.notifications import notify


@pytest.mark.django_db
def test_notification_gate_honors_mute_toggle():
    user = _user()
    actor = _remote()
    _, _, page = _scene()
    mute_actor(user, actor)
    assert (
        notify(user, kind=NotificationType.REPLY, actor=actor, page=page) is not None
    )
    mute_actor(user, actor, hide_notifications=True)
    assert notify(user, kind=NotificationType.REPLY, actor=actor, page=page) is None


@pytest.mark.django_db
def test_notification_gate_honors_thread_mute():
    user = _user()
    actor = _remote()
    _, _, page = _scene()
    services.mute_thread(user, page)
    assert notify(user, kind=NotificationType.REPLY, actor=actor, page=page) is None


@pytest.mark.django_db
def test_notification_gate_honors_domain_block():
    user = _user()
    actor = _remote(domain="blocked.test")
    _, _, page = _scene()
    services.block_domain(user, "blocked.test")
    assert notify(user, kind=NotificationType.REPLY, actor=actor, page=page) is None


@pytest.mark.django_db
def test_notification_gate_honors_keyword_filter():
    user = _user()
    actor = _remote()
    _, _, page = _scene()
    services.add_filter(user, "landing")
    assert notify(user, kind=NotificationType.REPLY, actor=actor, page=page) is None


@pytest.mark.django_db
def test_notification_gate_ignores_expired_preferences():
    user = _user()
    actor = _remote()
    _, _, page = _scene()
    mute_actor(user, actor, hide_notifications=True, expires_at=_future(days=1))
    services.add_filter(user, "landing", expires_at=_future(days=1))
    Mute.objects.filter(user=user).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    KeywordFilter.objects.filter(user=user).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert (
        notify(user, kind=NotificationType.REPLY, actor=actor, page=page) is not None
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_moderation.py -q -k notification_gate`
Expected: suppression assertions fail because `notify` ignores preferences.

- [ ] **Step 3: Add the gate**

In `social/notifications.py`, add the import and the call:

```python
from moderation import services as moderation_services
```

```python
def notify(recipient, *, kind, actor=None, page=None, comment=None):
    if recipient is None:
        return None
    actor_user = _actor_user(actor)
    if actor_user is not None and actor_user.pk == recipient.pk:
        return None
    if not moderation_services.notification_allowed(
        recipient, actor=actor, page=page, comment=comment
    ):
        return None
    return Notification.objects.create(
        recipient=recipient, type=kind, actor=actor, page=page, comment=comment
    )
```

- [ ] **Step 4: Run the full moderation and social suites**

Run: `uv run pytest tests/test_moderation.py tests/test_social.py tests/test_comments.py -q`
Expected: all pass.

- [ ] **Step 5: Run the full suite, lint, and migration check**

Run: `uv run pytest -q`
Expected: previous total + new tests, no failures.

Run: `uv run ruff check .` and `uv run ruff format --check .`
Expected: clean.

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: `No changes detected`

- [ ] **Step 6: Commit**

```bash
git add social/notifications.py tests/test_moderation.py
git commit -m "feat: suppress notifications for muted or filtered content"
```
