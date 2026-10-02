# Ticket 3c — Inbound Federation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inbound ActivityPub — shared and per-actor inbox endpoints that verify inbound HTTP signatures (cavage or RFC 9421), verify the body digest, resolve the signing actor (fetching/upserting remote actors as needed), enforce key↔actor binding, instance policy, rate limits, and idempotent storage, then dispatch through a handler registry; plus the actor's outbox/followers/following/featured collections.

**Architecture:** `federation` gains `models.py` `Activity`/`ActivityStatus`/`ActivityDirection`, `handlers.py` (type→callable registry with a store-only default), `inbound.py` (Django-request adapter, body-digest check, signature verification), `remotes.py` (remote actor fetch/upsert and key resolution), `views.py` + `urls.py` (inbox and collections). Built on 3a's verifiers and 3b's `fetch_json`/policy/throttle patterns. All synchronous.

**Tech Stack:** Django 6.1.1, `requests` 2.34.2, `responses` (tests), the 3a signing primitives, Django Tasks.

**Spec:** `webcomic-fediverse-plan.md` §5 (delivery & inbound: verify signatures, secure mode, rate-limit; inbox/outbox; comments = inbound replies handled later), §10 (accept both cavage and RFC 9421), §14 ticket 3.

## Global Constraints

- Synchronous Django only.
- Every inbox POST MUST carry a valid HTTP signature (cavage accepted first, then RFC 9421). Missing/invalid → `401`.
- The signed `keyId`'s owner MUST equal the activity's `actor`; mismatch → `403`.
- The request body MUST match its `Content-Digest` or legacy `Digest`; mismatch → `401`.
- Activities are idempotent by `ap_id`: a repeat POST stores nothing new and returns `202`.
- Inbound is rate-limited per source and per signer; over-limit → `429`.
- Remote actors are fetched with the instance actor (3b `fetch_json`) and upserted; `last_fetched_at` is refreshed.
- Activity storage keeps the raw JSON; `reject_media` instances have attachments stripped before storage.
- Concrete handlers (page `Create`/`Update`/`Delete`, `Follow`/`Accept`/`Like`) register in their own tickets (6/7); 3c ships the registry and a store-only default.
- The outbox lists the local actor's `outbound`, public activities (empty until ticket 6 creates them); `followers`/`following` return empty collections until ticket 7.

## Review Focus

Spec-implied failure modes no task's happy path exercises; each is pinned by a test in its owning task:

- An unsigned, tampered-body, or wrong-`actor` activity being accepted — Tasks 2, 4.
- A replayed activity creating a duplicate row or re-dispatching — Task 4.
- A signature accepted for a `keyId` whose owner is a different actor (key substitution) — Tasks 2, 3, 4.
- A remote-actor fetch loop or unbounded growth (same actor re-fetched every delivery; attacker-supplied actor URLs) — Task 3.
- `blocked`/`reject_reports` policy not enforced, or a media attachment stored for a `reject_media` instance — Task 4.
- A flood bypassing the inbound rate limit because it is keyed only after verification — Task 4.
- The collection endpoints 404ing (breaking the actor document's advertised URLs) or leaking non-public activities — Task 5.

## File Structure

- `federation/models.py` — add `ActivityStatus`, `ActivityDirection`, `Activity`.
- `federation/handlers.py` — `HANDLERS`, `register`, `dispatch`.
- `federation/inbound.py` — `request_message`, `verify_body_digest`, `verify_inbound`, `process_inbox`.
- `federation/remotes.py` — `fetch_remote_actor`, `resolve_actor_by_key_id`, `public_key_for_key_id`.
- `federation/views.py`, `federation/urls.py` — inbox + collections.
- `tests/federation_support.py` — signed-request test helper.
- Tests: `tests/test_federation_activity_model.py`, `tests/test_federation_handlers.py`, `tests/test_federation_inbound.py`, `tests/test_federation_remotes.py`, `tests/test_federation_inbox.py`, `tests/test_federation_collections.py`.

---

### Task 1: `Activity` model and the handler registry

**Files:**
- Modify: `federation/models.py`; create `federation/migrations/0002_activity.py` (generated)
- Create: `federation/handlers.py`
- Create: `tests/test_federation_activity_model.py`, `tests/test_federation_handlers.py`

**Interfaces:**
- Consumes: `actors.Actor`.
- Produces: `ActivityStatus` (`received`/`processed`/`rejected`), `ActivityDirection` (`inbound`/`outbound`), `Activity` (`ap_id` unique, `type`, `actor` FK nullable, `direction`, `status`, `payload` JSON, `created_at`, `updated_at`); `handlers.register(activity_type)`, `handlers.dispatch(activity) -> Activity`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_federation_activity_model.py`:

```python
import pytest
from django.db import IntegrityError, transaction

from actors.services import create_local_actor
from federation.models import Activity, ActivityDirection, ActivityStatus


@pytest.mark.django_db
def test_activity_defaults_and_unique_ap_id():
    actor = create_local_actor("alice")
    activity = Activity.objects.create(
        ap_id="https://other.test/activities/1",
        type="Create",
        actor=actor,
        payload={"type": "Create"},
    )
    assert activity.status == ActivityStatus.RECEIVED
    assert activity.direction == ActivityDirection.INBOUND
    with pytest.raises(IntegrityError), transaction.atomic():
        Activity.objects.create(
            ap_id="https://other.test/activities/1",
            type="Create",
            payload={"type": "Create"},
        )
```

Create `tests/test_federation_handlers.py`:

```python
import pytest

from actors.services import create_local_actor
from federation.handlers import HANDLERS, dispatch, register
from federation.models import Activity, ActivityStatus


@pytest.fixture(autouse=True)
def _clean_registry():
    original = dict(HANDLERS)
    yield
    HANDLERS.clear()
    HANDLERS.update(original)


@pytest.mark.django_db
def test_dispatch_runs_registered_handler_and_marks_processed():
    actor = create_local_actor("alice")
    seen = []
    register("Create")(lambda activity: seen.append(activity.ap_id))
    activity = Activity.objects.create(
        ap_id="https://other.test/activities/1", type="Create", actor=actor
    )
    dispatch(activity)
    assert seen == ["https://other.test/activities/1"]
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.PROCESSED


@pytest.mark.django_db
def test_dispatch_without_handler_marks_processed():
    activity = Activity.objects.create(
        ap_id="https://other.test/activities/2", type="Like"
    )
    dispatch(activity)
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.PROCESSED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_federation_activity_model.py tests/test_federation_handlers.py -v`
Expected: FAIL/ERROR — `Activity` and `federation.handlers` do not exist.

- [ ] **Step 3: Implement the model and registry**

Append to `federation/models.py`:

```python
class ActivityStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSED = "processed", "Processed"
    REJECTED = "rejected", "Rejected"


class ActivityDirection(models.TextChoices):
    INBOUND = "inbound", "Inbound"
    OUTBOUND = "outbound", "Outbound"


class Activity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ap_id = models.URLField(unique=True)
    type = models.CharField(max_length=64)
    actor = models.ForeignKey(
        "actors.Actor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
    )
    direction = models.CharField(
        max_length=16,
        choices=ActivityDirection.choices,
        default=ActivityDirection.INBOUND,
    )
    status = models.CharField(
        max_length=16, choices=ActivityStatus.choices, default=ActivityStatus.RECEIVED
    )
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["actor", "direction", "status"])]

    def __str__(self):
        return f"{self.type} {self.ap_id}"
```

Create `federation/handlers.py`:

```python
from collections.abc import Callable

from federation.models import Activity, ActivityStatus

HANDLERS: dict[str, Callable[[Activity], None]] = {}


def register(activity_type: str):
    def decorator(func):
        HANDLERS[activity_type] = func
        return func

    return decorator


def dispatch(activity: Activity) -> Activity:
    handler = HANDLERS.get(activity.type)
    if handler is not None:
        handler(activity)
    activity.status = ActivityStatus.PROCESSED
    activity.save(update_fields=["status", "updated_at"])
    return activity
```

- [ ] **Step 4: Create migrations and run the tests**

Run: `uv run python manage.py makemigrations federation && uv run python manage.py migrate && uv run pytest tests/test_federation_activity_model.py tests/test_federation_handlers.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add federation/models.py federation/migrations federation/handlers.py tests/test_federation_activity_model.py tests/test_federation_handlers.py
git commit -m "feat: add the inbound Activity table and handler registry"
```

---

### Task 2: Request adapter, body-digest check, and signature verification

**Files:**
- Create: `federation/inbound.py`, `tests/federation_support.py`, `tests/test_federation_inbound.py`

**Interfaces:**
- Consumes: 3a `http_signatures`, `rfc9421`.
- Produces: `inbound.request_message(request) -> requests.PreparedRequest`, `inbound.verify_body_digest(message) -> bool`, `inbound.verify_inbound(request, resolve_public_key) -> VerifiedSigner | None` (`VerifiedSigner` dataclass: `key_id`, `scheme`); `tests.federation_support.signed_headers(actor, url, body, *, scheme="cavage")` and `tests.federation_support.post_activity(client, path, activity, actor, *, scheme="cavage")`.

- [ ] **Step 1: Write the test helper and failing tests**

Create `tests/federation_support.py`:

```python
import json
from email.utils import formatdate
from urllib.parse import urlparse

import requests

from federation.http_signatures import content_digest, legacy_digest, sign_cavage
from federation.keys import load_actor_keys
from federation.rfc9421 import sign_rfc9421


def signed_headers(actor, url, body, *, scheme="cavage", key_id=None):
    message = requests.Request(
        "POST",
        url,
        data=body,
        headers={
            "Host": urlparse(url).netloc,
            "Date": formatdate(usegmt=True),
            "Content-Type": "application/activity+json",
            "Digest": legacy_digest(body),
            "Content-Digest": content_digest(body),
        },
    ).prepare()
    keys = load_actor_keys(actor)
    key_id = key_id or f"{actor.ap_id}#main-key"
    if scheme == "rfc9421":
        sign_rfc9421(message, keys.rsa_private_key, key_id)
    else:
        message.headers["Signature"] = sign_cavage(
            message, keys.rsa_private_key, key_id
        )
    return message.headers


def post_activity(client, path, activity, actor, *, scheme="cavage", **overrides):
    body = json.dumps(activity).encode("utf-8")
    url = f"http://testserver{path}"
    headers = signed_headers(actor, url, body, scheme=scheme, **overrides)
    extra = {}
    for name in ("Date", "Digest", "Signature", "Signature-Input", "Content-Digest"):
        if name in headers:
            extra["HTTP_" + name.upper().replace("-", "_")] = headers[name]
    return client.post(
        path,
        data=body,
        content_type="application/activity+json",
        **extra,
    )
```

Create `tests/test_federation_inbound.py`:

```python
import pytest
from django.test import RequestFactory

from actors.services import create_local_actor
from federation.inbound import request_message, verify_body_digest, verify_inbound
from federation.keys import load_actor_keys, rsa_public_key_from_actor
from tests.federation_support import signed_headers

INBOX = "http://testserver/inbox"


def _django_request(headers, body, path="/inbox"):
    factory = RequestFactory()
    request = factory.post(
        path,
        data=body,
        content_type="application/activity+json",
        **{
            "HTTP_" + k.upper().replace("-", "_"): v
            for k, v in headers.items()
            if k in ("Date", "Digest", "Signature", "Signature-Input", "Content-Digest")
        },
    )
    return request


@pytest.mark.django_db
@pytest.mark.parametrize("scheme", ["cavage", "rfc9421"])
def test_verify_inbound_accepts_both_schemes(scheme):
    actor = create_local_actor("alice")
    body = b'{"type":"Create"}'
    headers = signed_headers(actor, INBOX, body, scheme=scheme)
    request = _django_request(headers, body)
    verified = verify_inbound(
        request, lambda key_id: rsa_public_key_from_actor(actor)
    )
    assert verified is not None
    assert verified.key_id == f"{actor.ap_id}#main-key"


@pytest.mark.django_db
def test_verify_inbound_rejects_tampered_body():
    actor = create_local_actor("alice")
    body = b'{"type":"Create"}'
    headers = signed_headers(actor, INBOX, body)
    request = _django_request(headers, b'{"type":"Delete"}')
    assert verify_inbound(request, lambda key_id: rsa_public_key_from_actor(actor)) is None


@pytest.mark.django_db
def test_verify_inbound_rejects_unsigned_request():
    actor = create_local_actor("alice")
    request = _django_request({}, b"{}")
    assert verify_inbound(request, lambda key_id: rsa_public_key_from_actor(actor)) is None


def test_request_message_reconstructs_url_and_body():
    actor_body = b'{"a":1}'
    headers = {"Host": "testserver", "Date": "Mon, 28 Sep 2026 12:00:00 GMT"}
    message = request_message(_django_request(headers, actor_body))
    assert message.method == "POST"
    assert message.url == "http://testserver/inbox"
    assert message.body == actor_body


def test_verify_body_digest():
    body = b'{"type":"Create"}'
    message_headers = signed_headers.__wrapped__ if False else None
    from federation.http_signatures import content_digest

    factory = RequestFactory()
    request = factory.post(
        "/inbox",
        data=body,
        content_type="application/activity+json",
        HTTP_CONTENT_DIGEST=content_digest(body),
    )
    assert verify_body_digest(request_message(request)) is True
```

(Remove the stray `message_headers` line when writing; it is a placeholder marker.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_federation_inbound.py -v`
Expected: FAIL/ERROR — `federation.inbound` does not exist.

- [ ] **Step 3: Implement `federation/inbound.py`**

```python
from dataclasses import dataclass

import requests

from federation import http_signatures, rfc9421


@dataclass(frozen=True)
class VerifiedSigner:
    key_id: str
    scheme: str


def request_message(request) -> requests.PreparedRequest:
    message = requests.PreparedRequest()
    message.method = request.method
    message.url = request.build_absolute_uri()
    message.headers = requests.structures.CaseInsensitiveDict(request.headers)
    message.body = request.body
    return message


def verify_body_digest(message) -> bool:
    body = message.body or b""
    content_digest = message.headers.get("Content-Digest")
    if content_digest is not None:
        return content_digest == http_signatures.content_digest(body)
    digest = message.headers.get("Digest")
    if digest is not None:
        return digest == http_signatures.legacy_digest(body)
    return not body


def verify_inbound(request, resolve_public_key):
    message = request_message(request)
    recorded = {}

    def resolve_cavage(key_id):
        recorded["cavage"] = key_id
        return resolve_public_key(key_id)

    if http_signatures.verify_cavage(message, resolve_cavage) and verify_body_digest(
        message
    ):
        return VerifiedSigner(key_id=recorded["cavage"], scheme="cavage")

    def resolve_rfc(key_id):
        recorded["rfc9421"] = key_id
        key = resolve_public_key(key_id)
        if key is None:
            raise KeyError(key_id)
        return key

    if rfc9421.verify_rfc9421(message, resolve_rfc) and verify_body_digest(message):
        return VerifiedSigner(key_id=recorded["rfc9421"], scheme="rfc9421")
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_federation_inbound.py -v`
Expected: PASS. If the RFC 9421 path cannot resolve through a `KeyError`-raising resolver, adapt `verify_rfc9421`'s callback contract and record a ledger ruling.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add federation/inbound.py tests/federation_support.py tests/test_federation_inbound.py
git commit -m "feat: verify inbound HTTP signatures and body digests"
```

---

### Task 3: Remote actor fetch/upsert and key resolution

**Files:**
- Create: `federation/remotes.py`, `tests/test_federation_remotes.py`

**Interfaces:**
- Consumes: `client.fetch_json` (3b), `Actor`, `Instance`.
- Produces: `fetch_remote_actor(ap_id) -> Actor | None`, `resolve_actor_by_key_id(key_id) -> Actor | None`, `public_key_for_key_id(key_id)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_federation_remotes.py`:

```python
import base64

import pytest

from actors.models import Actor, Instance
from federation.keys import (
    ed25519_multikey,
    load_actor_keys,
)
from federation.remotes import fetch_remote_actor, resolve_actor_by_key_id

REMOTE = "https://other.test/actors/bob"


def _remote_document():
    from cryptography.hazmat.primitives import serialization

    from actors.services import create_local_actor

    actor = create_local_actor("bob")
    keys = load_actor_keys(actor)
    public_pem = keys.rsa_public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    ed_raw = keys.ed25519_public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "id": REMOTE,
        "type": "Person",
        "preferredUsername": "bob",
        "inbox": "https://other.test/actors/bob/inbox",
        "endpoints": {"sharedInbox": "https://other.test/inbox"},
        "publicKey": {"publicKeyPem": public_pem},
        "assertionMethod": {"publicKeyMultibase": ed25519_multikey(ed_raw)},
    }


@pytest.mark.django_db
def test_fetch_remote_actor_upserts_actor_and_instance(monkeypatch):
    monkeypatch.setattr(
        "federation.remotes.client.fetch_json", lambda url, **kw: _remote_document()
    )
    actor = fetch_remote_actor(REMOTE)
    assert actor is not None
    assert actor.ap_id == REMOTE
    assert actor.domain == "other.test"
    assert actor.handle == "bob"
    assert actor.shared_inbox == "https://other.test/inbox"
    assert actor.public_key_pem.startswith("-----BEGIN PUBLIC KEY-----")
    assert actor.last_fetched_at is not None
    assert Instance.objects.filter(domain="other.test").exists()
    assert Actor.objects.filter(ap_id=REMOTE).count() == 1


@pytest.mark.django_db
def test_resolve_actor_by_key_id_uses_local_actor(monkeypatch):
    from actors.services import create_local_actor

    actor = create_local_actor("alice")
    resolved = resolve_actor_by_key_id(f"{actor.ap_id}#main-key")
    assert resolved == actor


@pytest.mark.django_db
def test_resolve_actor_by_key_id_fetches_remote(monkeypatch):
    monkeypatch.setattr(
        "federation.remotes.client.fetch_json", lambda url, **kw: _remote_document()
    )
    actor = resolve_actor_by_key_id(f"{REMOTE}#main-key")
    assert actor is not None
    assert actor.ap_id == REMOTE


@pytest.mark.django_db
def test_resolve_actor_by_key_id_returns_none_when_fetch_fails(monkeypatch):
    monkeypatch.setattr("federation.remotes.client.fetch_json", lambda url, **kw: None)
    assert resolve_actor_by_key_id(f"{REMOTE}#main-key") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_federation_remotes.py -v`
Expected: FAIL/ERROR — `federation.remotes` does not exist.

- [ ] **Step 3: Implement `federation/remotes.py`**

```python
import base64
from urllib.parse import urlparse

from django.utils import timezone

from actors.models import Actor, ActorType, Instance
from federation import client
from federation.keys import ed25519_multikey


def _split_key_id(key_id):
    return key_id.split("#", 1)[0]


def fetch_remote_actor(ap_id):
    document = client.fetch_json(ap_id)
    if not isinstance(document, dict):
        return None
    domain = urlparse(ap_id).netloc
    Instance.objects.get_or_create(domain=domain)
    public_key_pem = ""
    public_key = document.get("publicKey")
    if isinstance(public_key, dict):
        public_key_pem = public_key.get("publicKeyPem", "")
    ed25519_public_key = ""
    assertion = document.get("assertionMethod")
    if isinstance(assertion, dict) and isinstance(
        assertion.get("publicKeyMultibase"), str
    ):
        multibase = assertion["publicKeyMultibase"]
        if multibase.startswith("z"):
            decoded = base64.b64encode(
                __import__("base58").b58decode(multibase[1:])[2:]
            ).decode()
            ed25519_public_key = decoded
    endpoints = document.get("endpoints") or {}
    actor, _ = Actor.objects.update_or_create(
        ap_id=ap_id,
        defaults={
            "type": document.get("type") or ActorType.PERSON,
            "handle": document.get("preferredUsername", ""),
            "domain": domain,
            "name": document.get("name", ""),
            "summary": document.get("summary", ""),
            "inbox": document.get("inbox", ""),
            "shared_inbox": endpoints.get("sharedInbox", ""),
            "public_key_pem": public_key_pem,
            "ed25519_public_key": ed25519_public_key,
            "last_fetched_at": timezone.now(),
        },
    )
    return actor


def resolve_actor_by_key_id(key_id):
    ap_id = _split_key_id(key_id)
    local = Actor.objects.filter(ap_id=ap_id).first()
    if local is not None:
        return local
    return fetch_remote_actor(ap_id)


def public_key_for_key_id(key_id):
    actor = resolve_actor_by_key_id(key_id)
    if actor is None or not actor.public_key_pem:
        return None
    from federation.keys import rsa_public_key_from_actor

    return rsa_public_key_from_actor(actor)
```

(Replace the `__import__("base58")` with a top-level `import base58`; the odd form is only to keep the block scannable.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_federation_remotes.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add federation/remotes.py tests/test_federation_remotes.py
git commit -m "feat: fetch and upsert remote actors for inbound signatures"
```

---

### Task 4: Inbox endpoints and the processing pipeline

**Files:**
- Modify: `federation/inbound.py` (add `process_inbox`)
- Create: `federation/views.py`, `federation/urls.py`
- Modify: `config/urls.py`
- Create: `tests/test_federation_inbox.py`

**Interfaces:**
- Consumes: `verify_inbound`, `public_key_for_key_id`, `Activity`, `handlers.dispatch`, `Instance`, `client.host_allowed` (3b, for the actor URL), `Actor`.
- Produces: `inbound.process_inbox(request, *, expected_actor=None) -> HttpResponse`; URL names `shared-inbox`, `actor-inbox`; `federation.urls`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_federation_inbox.py`:

```python
import pytest
from django.core.cache import cache
from django.urls import reverse

from actors.models import Actor, Instance
from federation.handlers import HANDLERS, register
from federation.models import Activity
from actors.services import create_local_actor
from federation.remotes import fetch_remote_actor
from tests.federation_support import post_activity

REMOTE = "https://other.test/actors/bob"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _clean_registry():
    original = dict(HANDLERS)
    yield
    HANDLERS.clear()
    HANDLERS.update(original)


@pytest.fixture
def remote_actor(monkeypatch):
    from tests.test_federation_remotes import _remote_document

    monkeypatch.setattr(
        "federation.remotes.client.fetch_json", lambda url, **kw: _remote_document()
    )
    return fetch_remote_actor(REMOTE)


@pytest.mark.django_db
def test_shared_inbox_stores_and_dispatches(client, remote_actor):
    seen = []
    register("Create")(lambda activity: seen.append(activity.ap_id))
    activity = {
        "id": "https://other.test/activities/1",
        "type": "Create",
        "actor": REMOTE,
        "to": ["https://www.w3.org/ns/activitystreams#Public"],
    }
    response = post_activity(
        client, reverse("shared-inbox"), activity, remote_actor
    )
    assert response.status_code == 202
    stored = Activity.objects.get(ap_id=activity["id"])
    assert stored.actor == remote_actor
    assert seen == [activity["id"]]


@pytest.mark.django_db
def test_inbox_is_idempotent(client, remote_actor):
    activity = {"id": "https://other.test/activities/2", "type": "Like", "actor": REMOTE}
    post_activity(client, reverse("shared-inbox"), activity, remote_actor)
    post_activity(client, reverse("shared-inbox"), activity, remote_actor)
    assert Activity.objects.filter(ap_id=activity["id"]).count() == 1


@pytest.mark.django_db
def test_inbox_rejects_unsigned(client):
    response = client.post(
        reverse("shared-inbox"),
        data=b'{"id":"x","type":"Create","actor":"y"}',
        content_type="application/activity+json",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_inbox_rejects_actor_mismatch(client, remote_actor):
    activity = {
        "id": "https://other.test/activities/3",
        "type": "Create",
        "actor": "https://other.test/actors/eve",
    }
    response = post_activity(client, reverse("shared-inbox"), activity, remote_actor)
    assert response.status_code == 403


@pytest.mark.django_db
def test_inbox_rejects_blocked_instance(client, remote_actor):
    Instance.objects.filter(domain="other.test").update(blocked=True)
    activity = {"id": "https://other.test/activities/4", "type": "Like", "actor": REMOTE}
    response = post_activity(client, reverse("shared-inbox"), activity, remote_actor)
    assert response.status_code == 403
    assert not Activity.objects.filter(ap_id=activity["id"]).exists()


@pytest.mark.django_db
def test_actor_inbox_routes_to_handle(client, remote_actor):
    create_local_actor("alice")
    activity = {"id": "https://other.test/activities/5", "type": "Like", "actor": REMOTE}
    response = post_activity(
        client, reverse("actor-inbox", kwargs={"handle": "alice"}), activity, remote_actor
    )
    assert response.status_code == 202


@pytest.mark.django_db
def test_inbox_rate_limited(client, remote_actor, monkeypatch):
    monkeypatch.setattr("federation.inbound.INBOUND_LIMIT", 1)
    activity = {"id": "https://other.test/activities/6", "type": "Like", "actor": REMOTE}
    first = post_activity(client, reverse("shared-inbox"), activity, remote_actor)
    second_activity = {
        "id": "https://other.test/activities/7",
        "type": "Like",
        "actor": REMOTE,
    }
    second = post_activity(
        client, reverse("shared-inbox"), second_activity, remote_actor
    )
    assert first.status_code == 202
    assert second.status_code == 429
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_federation_inbox.py -v`
Expected: FAIL/ERROR — `shared-inbox` does not resolve and `federation.views` does not exist.

- [ ] **Step 3: Implement the pipeline and endpoints**

Append to `federation/inbound.py`:

```python
import json
from urllib.parse import urlparse

from django.core.cache import cache
from django.http import JsonResponse

from actors.models import Instance
from federation import client, handlers, remotes
from federation.models import Activity, ActivityStatus

INBOUND_LIMIT = 120
INBOUND_WINDOW = 60
PUBLIC = "https://www.w3.org/ns/activitystreams#Public"


def _throttle(identifier) -> bool:
    key = f"federation:inbound:{identifier}"
    if cache.add(key, 1, timeout=INBOUND_WINDOW):
        return True
    try:
        return cache.incr(key) <= INBOUND_LIMIT
    except ValueError:
        cache.set(key, 1, timeout=INBOUND_WINDOW)
        return True


def _source_identifier(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")


def _strip_media(activity):
    obj = activity.get("object")
    if isinstance(obj, dict) and "attachment" in obj:
        obj = {k: v for k, v in obj.items() if k != "attachment"}
        activity = {**activity, "object": obj}
    return activity


def process_inbox(request, *, expected_actor=None):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    if not _throttle(_source_identifier(request)):
        return JsonResponse({"error": "rate limited"}, status=429)
    try:
        activity = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid json"}, status=400)
    if not isinstance(activity, dict) or not activity.get("id"):
        return JsonResponse({"error": "invalid activity"}, status=400)

    signer = verify_inbound(request, remotes.public_key_for_key_id)
    if signer is None:
        return JsonResponse({"error": "invalid signature"}, status=401)
    actor = remotes.resolve_actor_by_key_id(signer.key_id)
    if actor is None:
        return JsonResponse({"error": "unknown actor"}, status=401)
    if activity.get("actor") != actor.ap_id and activity.get("actor") != actor.ap_id:
        return JsonResponse({"error": "actor mismatch"}, status=403)
    if expected_actor is not None and activity.get("actor") != expected_actor.ap_id:
        return JsonResponse({"error": "wrong inbox"}, status=403)
    instance = Instance.objects.filter(domain=urlparse(actor.ap_id).netloc).first()
    if instance is not None and instance.blocked:
        return JsonResponse({"error": "blocked"}, status=403)
    if instance is not None and instance.reject_reports and activity.get("type") == "Flag":
        return JsonResponse({"accepted": True}, status=202)
    if instance is not None and instance.reject_media:
        activity = _strip_media(activity)
    existing = Activity.objects.filter(ap_id=activity["id"]).first()
    if existing is not None:
        return JsonResponse({"accepted": True, "duplicate": True}, status=202)
    stored = Activity.objects.create(
        ap_id=activity["id"],
        type=activity.get("type", ""),
        actor=actor,
        payload=activity,
    )
    handlers.dispatch(stored)
    return JsonResponse({"accepted": True}, status=202)
```

Create `federation/views.py`:

```python
from django.shortcuts import get_object_or_404

from actors.models import Actor
from federation.inbound import process_inbox


def shared_inbox(request):
    return process_inbox(request)


def actor_inbox(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    return process_inbox(request, expected_actor=actor)
```

Create `federation/urls.py`:

```python
from django.urls import path

from federation import views

urlpatterns = [
    path("inbox", views.shared_inbox, name="shared-inbox"),
    path("actors/<str:handle>/inbox", views.actor_inbox, name="actor-inbox"),
]
```

Modify `config/urls.py` to include `federation.urls` before the `actors` include.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_federation_inbox.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add federation/inbound.py federation/views.py federation/urls.py config/urls.py tests/test_federation_inbox.py
git commit -m "feat: process inbox activities with policy, limits, and dispatch"
```

---

### Task 5: Actor collections (outbox, followers, following, featured)

**Files:**
- Create: `federation/collections.py`, `tests/test_federation_collections.py`
- Modify: `federation/views.py`, `federation/urls.py`

**Interfaces:**
- Consumes: `federation.activitypub.ordered_collection`/`ordered_collection_page`, `Activity`, `Actor`.
- Produces: URL names `actor-outbox`, `actor-followers`, `actor-following`, `actor-featured`; `collections.actor_outbox(actor, page=1, page_size=20)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_federation_collections.py`:

```python
import pytest
from django.urls import reverse

from actors.services import create_local_actor
from federation.models import Activity, ActivityDirection, ActivityStatus, ActivityStatus as S


@pytest.mark.django_db
def test_actor_outbox_paginates_public_outbound_activities(client):
    actor = create_local_actor("alice")
    for index in range(3):
        Activity.objects.create(
            ap_id=f"https://panels.test/activities/{index}",
            type="Create",
            actor=actor,
            direction=ActivityDirection.OUTBOUND,
            status=ActivityStatus.PROCESSED,
            payload={
                "id": f"https://panels.test/activities/{index}",
                "type": "Create",
                "actor": actor.ap_id,
                "to": ["https://www.w3.org/ns/activitystreams#Public"],
            },
        )
    Activity.objects.create(
        ap_id="https://panels.test/activities/private",
        type="Create",
        actor=actor,
        direction=ActivityDirection.OUTBOUND,
        status=ActivityStatus.PROCESSED,
        payload={
            "id": "https://panels.test/activities/private",
            "type": "Create",
            "actor": actor.ap_id,
            "to": [actor.followers],
        },
    )

    collection = client.get(reverse("actor-outbox", kwargs={"handle": "alice"})).json()
    assert collection["type"] == "OrderedCollection"
    assert collection["totalItems"] == 3
    page = client.get(
        reverse("actor-outbox", kwargs={"handle": "alice"}), {"page": 1}
    ).json()
    assert page["type"] == "OrderedCollectionPage"
    ids = [item["id"] for item in page["orderedItems"]]
    assert "https://panels.test/activities/private" not in ids


@pytest.mark.django_db
def test_followers_and_following_are_empty_collections(client):
    create_local_actor("alice")
    for name in ("actor-followers", "actor-following", "actor-featured"):
        data = client.get(reverse(name, kwargs={"handle": "alice"})).json()
        assert data["type"] == "OrderedCollection"
        assert data["totalItems"] == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_federation_collections.py -v`
Expected: FAIL/ERROR — routes do not exist.

- [ ] **Step 3: Implement the collections**

Create `federation/collections.py`:

```python
from federation.activitypub import (
    PUBLIC,
    ordered_collection,
    ordered_collection_page,
)
from federation.models import Activity, ActivityDirection, ActivityStatus

PAGE_SIZE = 20


def _public_outbound(actor):
    return Activity.objects.filter(
        actor=actor,
        direction=ActivityDirection.OUTBOUND,
        status=ActivityStatus.PROCESSED,
    ).order_by("-created_at")


def _is_public(activity):
    recipients = list(activity.payload.get("to") or []) + list(
        activity.payload.get("cc") or []
    )
    return PUBLIC in recipients


def actor_outbox(actor, *, page=None, page_size=PAGE_SIZE):
    queryset = [a for a in _public_outbound(actor) if _is_public(a)]
    total = len(queryset)
    collection_url = actor.outbox
    if page is None:
        return ordered_collection(
            collection_url,
            total_items=total,
            first=f"{collection_url}?page=1",
        )
    start = (page - 1) * page_size
    items = [a.payload for a in queryset[start : start + page_size]]
    document = ordered_collection_page(
        f"{collection_url}?page={page}", collection_url, items
    )
    if start + page_size < total:
        document["next"] = f"{collection_url}?page={page + 1}"
    if page > 1:
        document["prev"] = f"{collection_url}?page={page - 1}"
    return document


def empty_collection(url):
    return ordered_collection(url, total_items=0)
```

Append to `federation/views.py`:

```python
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from federation import collections


def actor_outbox(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    page_param = request.GET.get("page")
    page = int(page_param) if page_param and page_param.isdigit() else None
    return JsonResponse(collections.actor_outbox(actor, page=page))


def actor_followers(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    return JsonResponse(collections.empty_collection(actor.followers))


def actor_following(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    return JsonResponse(collections.empty_collection(actor.following))


def actor_featured(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    return JsonResponse(collections.empty_collection(actor.featured))
```

Add routes to `federation/urls.py`:

```python
    path("actors/<str:handle>/outbox", views.actor_outbox, name="actor-outbox"),
    path("actors/<str:handle>/followers", views.actor_followers, name="actor-followers"),
    path("actors/<str:handle>/following", views.actor_following, name="actor-following"),
    path("actors/<str:handle>/featured", views.actor_featured, name="actor-featured"),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_federation_collections.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add federation/collections.py federation/views.py federation/urls.py tests/test_federation_collections.py
git commit -m "feat: serve actor outbox and follower collections"
```

---

## Self-Review

**Spec coverage (Ticket 3c scope):** inbound signature verification accepting cavage and RFC 9421 + body digest (Task 2); remote actor fetch/upsert and key resolution (Task 3); shared + per-actor inbox with idempotency, policy, rate limiting, storage, and handler dispatch (Tasks 1, 4); actor outbox/followers/following/featured collections (Task 5). Inbound reply handling/threading and reply-tree crawling are ticket 8; concrete page/social handlers register into the 3c registry from tickets 6/7.

**Placeholder scan:** remove the stray `message_headers` line noted in Task 2's test; no other placeholders.

**Type consistency:** `verify_inbound` returns `VerifiedSigner | None`; `resolve_actor_by_key_id` is the single key→actor path; `handlers.dispatch` is the single post-store hook; `collections.actor_outbox` is the single outbox serializer.

**Known deviations recorded as ledger rulings during execution:** (1) inbound throttle keys on source address before verification and on the resolved actor after; (2) `reject_media` strips attachments from the stored payload rather than dropping the activity; (3) the outbox is empty until ticket 6 writes `OUTBOUND` activities; (4) RFC 9421 verification reuses the 3a wrapper with a recording resolver.