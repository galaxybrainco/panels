# Ticket 3b — Outbound Federation Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Outbound ActivityPub — a plain-JSON AS2 builder layer and the actor's FEP-521a Multikey, a `requests` client that signs deliveries (cavage-first with RFC 9421 double-knock) and performs signed secure-mode fetches, a Django Tasks delivery pipeline with exponential-backoff retries and dead-lettering, per-domain outbound throttling, shared-inbox dedupe, and blocked/silenced instance gating.

**Architecture:** `federation` gains `activitypub.py` (AS2 builders + addressing), `models.py` (`Delivery`, `PeerSignaturePreference`), `client.py` (signed HTTP via the 3a primitives), `policy.py` (instance gating), `delivery.py` (fan-out/enqueue), and `tasks.py` (the delivery task). `actors/activitypub.py` publishes the actor's Ed25519 `assertionMethod` (Multikey). All synchronous; built on the merged 3a signing primitives.

**Tech Stack:** Django 6.1.1, `requests` 2.34.2, `responses` 0.26.3 (tests), Django Tasks (`django-tasks-db`), `cryptography`, `http-message-signatures`.

**Spec:** `webcomic-fediverse-plan.md` §5 (visibility/addressing, delivery & inbound, rate-limit for cost/abuse, federation policy), §10 (double-knock), §14 ticket 3.

## Global Constraints

- Synchronous Django/WSGI only. No async.
- Delivery signs with the **activity's actor** key; secure-mode fetches sign with the **instance actor**.
- Retries use `django-tasks-db` but the retry schedule is ours: re-enqueue via `Task.using(run_after=...)` (built-in `TaskContext` has only `task_result`).
- Outbound emitting is cavage-first; on `401` retry RFC 9421; remember the working scheme per remote domain in DB.
- One `Delivery` row per (activity, inbox URL); shared inbox URLs are deduped before enqueue.
- Policy gate: never deliver to a `blocked` instance; withhold **public** activities from `silenced` instances.
- The delivery payload is the caller's AS2 activity (page `Create`/`Update`/`Delete` are ticket 6); 3b delivers whatever it is given.

## Review Focus

Spec-implied failure modes no task's happy path exercises; each is pinned by a test in its owning task:

- A delivery retried forever instead of dead-lettering, or dropped without a retry when a `5xx`/network error occurs — Task 5.
- A duplicate delivery when shared inboxes are not deduped, or one `Delivery` per recipient actor instead of per inbox — Tasks 4, 5.
- Delivering to a blocked instance, or delivering a public activity to a silenced one — Task 4/5.
- Signature double-knock not remembering the working scheme, so every delivery pays a wasted `401` — Task 3.
- A secure-mode fetch sent unsigned (rejected by authorized-fetch instances) or signed with a non-instance actor — Task 3.
- The outbound rate limit never triggering, or triggering so hard it dead-letters rather than reschedules — Task 5.
- The actor document advertising an Ed25519 `assertionMethod` whose `publicKeyMultibase` does not decode to the actor's actual key — Task 1.

## File Structure

- `federation/activitypub.py` — AS2 context constants, `Audience`, `build_activity`, `addressing_for`, `is_public`, `ordered_collection`, `ordered_collection_page`.
- `federation/models.py` — `Delivery`, `DeliveryStatus`, `PeerSignaturePreference`, `SignatureScheme`.
- `federation/client.py` — `signed_request`, `post_activity`, `fetch_json`, `user_agent`.
- `federation/policy.py` — `domain_blocked`, `domain_silenced`, `delivery_allowed`.
- `federation/delivery.py` — `unique_inboxes`, `enqueue_delivery`, `fan_out`.
- `federation/tasks.py` — `deliver_activity` task, `backoff_seconds`.
- `actors/activitypub.py` — add the Ed25519 `assertionMethod` Multikey.
- Tests: `tests/test_activitypub_serialization.py`, `tests/test_federation_models.py`, `tests/test_federation_client.py`, `tests/test_federation_policy.py`, `tests/test_federation_delivery.py`.

---

### Task 1: AS2 plain-JSON builders and the actor's assertion method

**Files:**
- Create: `federation/activitypub.py`, `tests/test_activitypub_serialization.py`
- Modify: `actors/activitypub.py`, `tests/test_actor_document.py`

**Interfaces:**
- Consumes: `federation.keys.ed25519_multikey` (Ticket 3a), `Actor`.
- Produces: `ACTIVITYSTREAMS_CONTEXT`, `SECURITY_CONTEXT`, `PUBLIC`, `Audience`, `activity_context()`, `build_activity(type, actor, obj, *, activity_id=None, to=None, cc=None)`, `addressing_for(audience, followers_url)`, `is_public(activity)`, `ordered_collection(id, *, total_items=0, items=None, first=None, last=None)`, `ordered_collection_page(id, part_of, ordered_items, *, next=None, prev=None)`. Later tasks call `is_public`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_activitypub_serialization.py`:

```python
import pytest

from actors.services import create_local_actor
from federation.activitypub import (
    ACTIVITYSTREAMS_CONTEXT,
    PUBLIC,
    Audience,
    activity_context,
    addressing_for,
    build_activity,
    is_public,
    ordered_collection,
    ordered_collection_page,
)


def test_activity_context_is_mastodon_legible():
    assert activity_context() == [
        "https://www.w3.org/ns/activitystreams",
        "https://w3id.org/security/v1",
    ]


@pytest.mark.django_db
def test_build_activity_shape():
    actor = create_local_actor("alice")
    activity = build_activity(
        "Create",
        actor,
        {"type": "Note", "id": "https://panels.test/objects/1"},
        activity_id="https://panels.test/activities/1",
        to=[PUBLIC],
        cc=[actor.followers],
    )
    assert activity["type"] == "Create"
    assert activity["actor"] == actor.ap_id
    assert activity["id"] == "https://panels.test/activities/1"
    assert activity["to"] == [PUBLIC]
    assert activity["cc"] == [actor.followers]
    assert activity["@context"] == activity_context()


def test_addressing_for_audiences():
    followers = "https://panels.test/actors/alice/followers"
    assert addressing_for(Audience.PUBLIC, followers) == ([PUBLIC], [followers])
    assert addressing_for(Audience.UNLISTED, followers) == ([followers], [PUBLIC])
    assert addressing_for(Audience.FOLLOWERS_ONLY, followers) == ([followers], [])
    assert addressing_for(Audience.MEMBERS, followers) == ([], [])


def test_is_public_reads_to_and_cc():
    assert is_public({"to": [PUBLIC], "cc": []}) is True
    assert is_public({"to": ["x"], "cc": [PUBLIC]}) is True
    assert is_public({"to": ["x"], "cc": ["y"]}) is False
    assert is_public({}) is False


def test_collection_builders():
    collection = ordered_collection(
        "https://panels.test/actors/alice/outbox", total_items=2, first="p1"
    )
    assert collection["type"] == "OrderedCollection"
    assert collection["totalItems"] == 2
    assert collection["first"] == "p1"
    page = ordered_collection_page(
        "p1", "https://panels.test/actors/alice/outbox", ["a"], next="p2"
    )
    assert page["type"] == "OrderedCollectionPage"
    assert page["partOf"].endswith("/outbox")
    assert page["orderedItems"] == ["a"]
    assert page["next"] == "p2"


def test_activitystreams_context_constant():
    assert ACTIVITYSTREAMS_CONTEXT == "https://www.w3.org/ns/activitystreams"
```

Append to `tests/test_actor_document.py`:

```python
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from federation.keys import ED25519_MULTICODEC_PREFIX


@pytest.mark.django_db
def test_actor_document_advertises_ed25519_assertion_method(client):
    from actors.services import create_local_actor

    actor = create_local_actor("alice")
    data = client.get(reverse("actor-detail", kwargs={"handle": "alice"})).json()
    method = data["assertionMethod"]
    assert method["id"] == f"{actor.ap_id}#ed25519-key"
    assert method["type"] == "Multikey"
    assert method["controller"] == actor.ap_id
    assert method["publicKeyMultibase"].startswith("z")
    assert data["publicKey"]["publicKeyPem"] == actor.public_key_pem
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_activitypub_serialization.py tests/test_actor_document.py -v`
Expected: FAIL/ERROR — `federation.activitypub` does not exist, and `assertionMethod` is missing from the actor document.

- [ ] **Step 3: Implement `federation/activitypub.py`**

```python
from actors.models import Actor

ACTIVITYSTREAMS_CONTEXT = "https://www.w3.org/ns/activitystreams"
SECURITY_CONTEXT = "https://w3id.org/security/v1"
PUBLIC = "https://www.w3.org/ns/activitystreams#Public"


class Audience(__import__("django.db.models", fromlist=["TextChoices"]).TextChoices):
    PUBLIC = "public", "Public"
    UNLISTED = "unlisted", "Unlisted"
    FOLLOWERS_ONLY = "followers_only", "Followers only"
    MEMBERS = "members", "Members"
    TIER = "tier", "Tier"


def activity_context():
    return [ACTIVITYSTREAMS_CONTEXT, SECURITY_CONTEXT]


def build_activity(activity_type, actor: Actor, obj, *, activity_id=None, to=None, cc=None):
    activity = {
        "@context": activity_context(),
        "type": activity_type,
        "actor": actor.ap_id,
        "object": obj,
    }
    if activity_id is not None:
        activity["id"] = activity_id
    if to is not None:
        activity["to"] = to
    if cc is not None:
        activity["cc"] = cc
    return activity


def addressing_for(audience, followers_url):
    if audience == Audience.PUBLIC:
        return [PUBLIC], [followers_url]
    if audience == Audience.UNLISTED:
        return [followers_url], [PUBLIC]
    if audience == Audience.FOLLOWERS_ONLY:
        return [followers_url], []
    return [], []


def is_public(activity) -> bool:
    recipients = list(activity.get("to") or []) + list(activity.get("cc") or [])
    return PUBLIC in recipients


def ordered_collection(collection_id, *, total_items=0, items=None, first=None, last=None):
    document = {
        "@context": ACTIVITYSTREAMS_CONTEXT,
        "id": collection_id,
        "type": "OrderedCollection",
        "totalItems": total_items,
    }
    if items is not None:
        document["orderedItems"] = items
    if first is not None:
        document["first"] = first
    if last is not None:
        document["last"] = last
    return document


def ordered_collection_page(page_id, part_of, ordered_items, *, next=None, prev=None):
    document = {
        "@context": ACTIVITYSTREAMS_CONTEXT,
        "id": page_id,
        "type": "OrderedCollectionPage",
        "partOf": part_of,
        "orderedItems": ordered_items,
    }
    if next is not None:
        document["next"] = next
    if prev is not None:
        document["prev"] = prev
    return document
```

(Replace the `Audience` base with a normal `from django.db import models` and `class Audience(models.TextChoices):`; the inline import above is only to keep the block self-contained.)

- [ ] **Step 4: Add the assertion method to the actor document**

Modify `actors/activitypub.py` — add the imports and the `assertionMethod` field, and add the Multikey context:

```python
import base64

from federation.keys import ed25519_multikey
```

```python
        "publicKey": {
            "id": f"{actor.ap_id}#main-key",
            "owner": actor.ap_id,
            "publicKeyPem": actor.public_key_pem,
        },
        "assertionMethod": {
            "id": f"{actor.ap_id}#ed25519-key",
            "type": "Multikey",
            "controller": actor.ap_id,
            "publicKeyMultibase": ed25519_multikey(
                base64.b64decode(actor.ed25519_public_key)
            ),
        },
```

Also add `"https://w3id.org/security/multikey/v1"` to the document's `@context` list.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_activitypub_serialization.py tests/test_actor_document.py -v`
Expected: PASS. If `actors.activitypub` importing `federation.keys` triggers an import cycle, move `ed25519_multikey` calls inline into `actors/activitypub.py` using `base58` and record a ledger ruling.

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add federation/activitypub.py actors/activitypub.py tests/test_activitypub_serialization.py tests/test_actor_document.py
git commit -m "feat: add AS2 builders and publish the actor assertion method"
```

---

### Task 2: `Delivery` and `PeerSignaturePreference` models

**Files:**
- Create: `federation/models.py` (replace the empty file), `federation/migrations/0001_initial.py` (generated)
- Create: `tests/test_federation_models.py`

**Interfaces:**
- Consumes: `actors.Actor`.
- Produces: `federation.models.DeliveryStatus`, `Delivery` (fields `inbox_url`, `activity`, `actor`, `status`, `attempts`, `max_attempts`, `next_attempt_at`, `last_error`, `created_at`, `updated_at`), `SignatureScheme`, `PeerSignaturePreference` (`domain` unique, `scheme`, `updated_at`). Later tasks create/update these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_federation_models.py`:

```python
import pytest
from django.db import IntegrityError, transaction

from actors.services import create_local_actor
from federation.models import (
    Delivery,
    DeliveryStatus,
    PeerSignaturePreference,
    SignatureScheme,
)


@pytest.mark.django_db
def test_delivery_defaults():
    actor = create_local_actor("alice")
    delivery = Delivery.objects.create(
        inbox_url="https://other.test/inbox",
        activity={"type": "Create"},
        actor=actor,
    )
    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.attempts == 0
    assert delivery.max_attempts == 6
    assert delivery.next_attempt_at is None


@pytest.mark.django_db
def test_peer_signature_preference_domain_is_unique():
    PeerSignaturePreference.objects.create(
        domain="other.test", scheme=SignatureScheme.CAVAGE
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        PeerSignaturePreference.objects.create(
            domain="other.test", scheme=SignatureScheme.RFC9421
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_federation_models.py -v`
Expected: FAIL/ERROR — the models do not exist.

- [ ] **Step 3: Implement the models**

Create `federation/models.py`:

```python
import uuid

from django.db import models


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    FAILED = "failed", "Retrying"
    DELIVERED = "delivered", "Delivered"
    DEAD = "dead", "Dead-lettered"


class SignatureScheme(models.TextChoices):
    CAVAGE = "cavage", "draft-cavage-http-signatures"
    RFC9421 = "rfc9421", "RFC 9421 HTTP Message Signatures"


class Delivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inbox_url = models.URLField()
    activity = models.JSONField()
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="deliveries"
    )
    status = models.CharField(
        max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=6)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]
        verbose_name_plural = "deliveries"

    def __str__(self):
        return f"{self.status} → {self.inbox_url}"


class PeerSignaturePreference(models.Model):
    domain = models.CharField(max_length=255, unique=True)
    scheme = models.CharField(max_length=16, choices=SignatureScheme.choices)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.domain}: {self.scheme}"
```

- [ ] **Step 4: Create and apply migrations**

Run: `uv run python manage.py makemigrations federation && uv run python manage.py migrate && uv run python manage.py makemigrations --check --dry-run`
Expected: `federation/migrations/0001_initial.py` created; migrations apply; no pending changes.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_federation_models.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add federation/models.py federation/migrations tests/test_federation_models.py
git commit -m "feat: add Delivery and PeerSignaturePreference models"
```

---

### Task 3: Signed HTTP client with double-knock and secure-mode fetch

**Files:**
- Modify: `pyproject.toml` (add `responses`), `config/settings/base.py` (software version for User-Agent)
- Create: `federation/client.py`, `tests/test_federation_client.py`

**Interfaces:**
- Consumes: 3a `http_signatures`, `rfc9421`, `keys`; `PeerSignaturePreference`.
- Produces: `federation.client.user_agent()`, `post_activity(url, activity, actor, *, session=None, timeout=10) -> requests.Response`, `fetch_json(url, *, actor=None, session=None, timeout=10) -> dict | None`. Later tasks call `post_activity`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_federation_client.py`:

```python
import json

import pytest
import responses

from actors.services import create_local_actor
from federation.client import fetch_json, post_activity
from federation.models import PeerSignaturePreference, SignatureScheme

INBOX = "https://other.test/inbox"


@pytest.mark.django_db
@responses.activate
def test_post_activity_signs_and_delivers():
    actor = create_local_actor("alice")
    responses.add(responses.POST, INBOX, status=202)
    response = post_activity(INBOX, {"type": "Create"}, actor)
    assert response.status_code == 202
    request = responses.calls[0].request
    assert "Signature" in request.headers
    assert request.headers["Content-Type"] == "application/activity+json"


@pytest.mark.django_db
@responses.activate
def test_double_knock_falls_back_to_rfc9421_and_remembers():
    actor = create_local_actor("alice")
    responses.add(responses.POST, INBOX, status=401)
    responses.add(responses.POST, INBOX, status=202)

    response = post_activity(INBOX, {"type": "Create"}, actor)
    assert response.status_code == 202
    preference = PeerSignaturePreference.objects.get(domain="other.test")
    assert preference.scheme == SignatureScheme.RFC9421


@pytest.mark.django_db
@responses.activate
def test_post_activity_uses_remembered_preference_first():
    actor = create_local_actor("alice")
    PeerSignaturePreference.objects.create(
        domain="other.test", scheme=SignatureScheme.RFC9421
    )
    responses.add(responses.POST, INBOX, status=202)
    post_activity(INBOX, {"type": "Create"}, actor)
    request = responses.calls[0].request
    assert "Signature-Input" in request.headers  # RFC 9421, not cavage


@pytest.mark.django_db
@responses.activate
def test_fetch_json_signs_with_instance_actor():
    create_local_actor("instance", is_instance_actor=True)
    responses.add(
        responses.GET,
        "https://other.test/actors/bob",
        json={"id": "https://other.test/actors/bob", "type": "Person"},
        status=200,
    )
    document = fetch_json("https://other.test/actors/bob")
    assert document["type"] == "Person"
    assert "Signature" in responses.calls[0].request.headers


@pytest.mark.django_db
@responses.activate
def test_fetch_json_returns_none_on_error():
    create_local_actor("instance", is_instance_actor=True)
    responses.add(responses.GET, "https://other.test/actors/bob", status=404)
    assert fetch_json("https://other.test/actors/bob") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_federation_client.py -v`
Expected: FAIL/ERROR — `responses` is not installed and `federation.client` does not exist.

- [ ] **Step 3: Add `responses` and implement the client**

Add `responses==0.26.3` to the `[dependency-groups] dev` list in `pyproject.toml`, then `uv sync`.

Create `federation/client.py`:

```python
import json
from email.utils import formatdate
from urllib.parse import urlparse

import requests
from django.conf import settings

from actors.models import Actor
from federation import http_signatures, rfc9421
from federation.keys import load_actor_keys
from federation.models import PeerSignaturePreference, SignatureScheme

ACTIVITYPUB_CONTENT_TYPE = "application/activity+json"
DEFAULT_TIMEOUT = 10
CAVAGE_GET_HEADERS = ["(request-target)", "host", "date"]


def user_agent():
    return f"Panels/{settings.SOFTWARE_VERSION}"


def _base_headers(body):
    return {
        "User-Agent": user_agent(),
        "Accept": ACTIVITYPUB_CONTENT_TYPE,
        "Date": formatdate(usegmt=True),
        "Digest": http_signatures.legacy_digest(body),
        "Content-Digest": http_signatures.content_digest(body),
    }


def _sign(message, actor, scheme, *, has_body):
    keys = load_actor_keys(actor)
    key_id = f"{actor.ap_id}#main-key"
    if scheme == SignatureScheme.RFC9421:
        covered = ("@method", "@target-uri", "content-digest") if has_body else (
            "@method",
            "@target-uri",
        )
        rfc9421.sign_rfc9421(message, keys.rsa_private_key, key_id, covered)
    else:
        headers = None if has_body else CAVAGE_GET_HEADERS
        message.headers["Signature"] = http_signatures.sign_cavage(
            message, keys.rsa_private_key, key_id, headers
        )


def _preference(domain):
    preference = PeerSignaturePreference.objects.filter(domain=domain).first()
    return preference.scheme if preference else None


def _remember(domain, scheme):
    PeerSignaturePreference.objects.update_or_create(
        domain=domain, defaults={"scheme": scheme}
    )


def signed_request(method, url, *, body=None, actor, session=None, timeout=DEFAULT_TIMEOUT, accept=None):
    has_body = body is not None
    payload = json.dumps(body).encode("utf-8") if has_body else None
    domain = urlparse(url).netloc
    remembered = _preference(domain)
    schemes = [SignatureScheme.CAVAGE, SignatureScheme.RFC9421]
    if remembered:
        schemes = [remembered, *[s for s in schemes if s != remembered]]
    session = session or requests
    last_response = None
    for scheme in schemes:
        headers = _base_headers(payload or b"")
        if has_body:
            headers["Content-Type"] = ACTIVITYPUB_CONTENT_TYPE
        if accept:
            headers["Accept"] = accept
        request = requests.Request(method, url, data=payload, headers=headers)
        prepared = request.prepare()
        _sign(prepared, actor, scheme, has_body=has_body)
        last_response = session.send(prepared, timeout=timeout)
        if last_response.status_code != 401:
            if last_response.status_code < 400:
                _remember(domain, scheme)
            return last_response
    return last_response


def post_activity(url, activity, actor, *, session=None, timeout=DEFAULT_TIMEOUT):
    return signed_request("POST", url, body=activity, actor=actor, session=session, timeout=timeout)


def fetch_json(url, *, actor=None, session=None, timeout=DEFAULT_TIMEOUT):
    actor = actor or Actor.objects.filter(is_instance_actor=True, domain="").first()
    if actor is None:
        return None
    response = signed_request(
        "GET", url, actor=actor, session=session, timeout=timeout
    )
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None
```

Add `SOFTWARE_VERSION = "0.1.0"` (or reuse `actors.software.SOFTWARE_VERSION`) to `config/settings/base.py`. Prefer reusing `actors.software.SOFTWARE_VERSION` — import it in `client.py` instead of a setting; record the choice in the ledger if the plan's setting differs.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_federation_client.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock config/settings/base.py federation/client.py tests/test_federation_client.py
git commit -m "feat: add signed federation client with double-knock"
```

---

### Task 4: Policy gating and fan-out helpers

**Files:**
- Create: `federation/policy.py`, `federation/delivery.py`
- Create: `tests/test_federation_policy.py`, `tests/test_federation_delivery.py`

**Interfaces:**
- Consumes: `Instance`, `Actor`, `Delivery`, `federation.activitypub.is_public`.
- Produces: `domain_blocked(domain)`, `domain_silenced(domain)`, `delivery_allowed(activity, inbox_url) -> bool`; `unique_inboxes(urls) -> list[str]`, `enqueue_delivery(activity, inbox_url, actor) -> Delivery`, `fan_out(activity, inbox_urls, actor) -> list[Delivery]`. Task 5 calls `delivery_allowed`; callers use `fan_out`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_federation_policy.py`:

```python
import pytest

from actors.models import Instance
from federation.activitypub import PUBLIC
from federation.policy import delivery_allowed, domain_blocked, domain_silenced


@pytest.mark.django_db
def test_unknown_domain_is_allowed():
    assert delivery_allowed({"to": [PUBLIC]}, "https://other.test/inbox") is True


@pytest.mark.django_db
def test_blocked_domain_is_denied():
    Instance.objects.create(domain="bad.test", blocked=True)
    assert domain_blocked("bad.test") is True
    assert delivery_allowed({"to": [PUBLIC]}, "https://bad.test/inbox") is False


@pytest.mark.django_db
def test_silenced_domain_withholds_public_but_allows_directed():
    Instance.objects.create(domain="quiet.test", silenced=True)
    assert domain_silenced("quiet.test") is True
    assert delivery_allowed({"to": [PUBLIC]}, "https://quiet.test/inbox") is False
    assert delivery_allowed({"to": ["https://quiet.test/u/bob"]}, "https://quiet.test/inbox") is True
```

Create `tests/test_federation_delivery.py`:

```python
import pytest

from actors.services import create_local_actor
from federation.delivery import enqueue_delivery, fan_out, unique_inboxes
from federation.models import Delivery


def test_unique_inboxes_dedupes_preserving_order():
    assert unique_inboxes(["a", "b", "a", "", "b", "c"]) == ["a", "b", "c"]


@pytest.mark.django_db
def test_enqueue_delivery_creates_one_row_per_inbox():
    actor = create_local_actor("alice")
    deliveries = fan_out(
        {"type": "Create"}, ["https://a.test/inbox", "https://a.test/inbox"], actor
    )
    assert len(deliveries) == 1
    assert Delivery.objects.count() == 1
    assert deliveries[0].inbox_url == "https://a.test/inbox"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_federation_policy.py tests/test_federation_delivery.py -v`
Expected: FAIL/ERROR — modules do not exist.

- [ ] **Step 3: Implement policy and delivery**

Create `federation/policy.py`:

```python
from urllib.parse import urlparse

from actors.models import Instance
from federation.activitypub import is_public


def _instance(domain):
    return Instance.objects.filter(domain=domain).first()


def domain_blocked(domain) -> bool:
    instance = _instance(domain)
    return bool(instance and instance.blocked)


def domain_silenced(domain) -> bool:
    instance = _instance(domain)
    return bool(instance and instance.silenced)


def delivery_allowed(activity, inbox_url) -> bool:
    domain = urlparse(inbox_url).netloc
    if domain_blocked(domain):
        return False
    if domain_silenced(domain) and is_public(activity):
        return False
    return True
```

Create `federation/delivery.py`:

```python
from actors.models import Actor
from federation.models import Delivery


def unique_inboxes(urls):
    seen = set()
    result = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def enqueue_delivery(activity, inbox_url, actor: Actor) -> Delivery:
    from federation.tasks import deliver_activity

    delivery = Delivery.objects.create(
        inbox_url=inbox_url, activity=activity, actor=actor
    )
    deliver_activity.enqueue(delivery.id)
    return delivery


def fan_out(activity, inbox_urls, actor: Actor):
    return [enqueue_delivery(activity, url, actor) for url in unique_inboxes(inbox_urls)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_federation_policy.py tests/test_federation_delivery.py -v`
Expected: FAIL for `test_enqueue_delivery_creates_one_row_per_inbox` until Task 5 defines `federation.tasks`. If so, implement Task 5 first and re-run; record the ordering in the ledger. `test_federation_policy.py` and `test_unique_inboxes_dedupes_preserving_order` must pass now.

- [ ] **Step 5: Commit (after Task 5's task exists)**

```bash
git add federation/policy.py federation/delivery.py tests/test_federation_policy.py tests/test_federation_delivery.py
git commit -m "feat: add federation policy gating and fan-out helpers"
```

---

### Task 5: Delivery task with retries, dead-lettering, and throttling

**Files:**
- Create: `federation/tasks.py`
- Create: `tests/test_federation_tasks.py`

**Interfaces:**
- Consumes: `Delivery`, `client.post_activity`, `policy.delivery_allowed`, `django.tasks`, Django cache.
- Produces: `federation.tasks.deliver_activity` (a `django.tasks` Task), `backoff_seconds(attempt) -> int`. `delivery.enqueue_delivery` imports the task.

- [ ] **Step 1: Write the failing test**

Create `tests/test_federation_tasks.py`:

```python
from datetime import timedelta

import pytest
import responses
from django.test import override_settings
from django.utils import timezone

from actors.services import create_local_actor
from federation.models import Delivery, DeliveryStatus, PeerSignaturePreference
from federation.tasks import backoff_seconds, deliver_activity

INBOX = "https://other.test/inbox"


def _delivery(actor, **kwargs):
    return Delivery.objects.create(
        inbox_url=INBOX, activity={"type": "Create"}, actor=actor, **kwargs
    )


def test_backoff_seconds_grows_and_caps():
    assert backoff_seconds(0) < backoff_seconds(1) < backoff_seconds(2)
    assert backoff_seconds(99) == backoff_seconds(50)


@pytest.mark.django_db
@responses.activate
def test_delivery_succeeds_and_marks_delivered():
    actor = create_local_actor("alice")
    delivery = _delivery(actor)
    responses.add(responses.POST, INBOX, status=202)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DELIVERED
    assert delivery.attempts == 0


@pytest.mark.django_db
@responses.activate
def test_delivery_5xx_schedules_retry():
    actor = create_local_actor("alice")
    delivery = _delivery(actor)
    responses.add(responses.POST, INBOX, status=500)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.attempts == 1
    assert delivery.next_attempt_at is not None


@pytest.mark.django_db
@responses.activate
def test_delivery_dead_letters_after_max_attempts():
    actor = create_local_actor("alice")
    delivery = _delivery(actor, attempts=5, max_attempts=6)
    responses.add(responses.POST, INBOX, status=500)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DEAD
    assert delivery.attempts == 6


@pytest.mark.django_db
@responses.activate
def test_delivery_blocked_instance_is_dead_lettered_without_request():
    from actors.models import Instance

    actor = create_local_actor("alice")
    Instance.objects.create(domain="other.test", blocked=True)
    delivery = _delivery(actor)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DEAD
    assert len(responses.calls) == 0


@pytest.mark.django_db
@responses.activate
def test_delivery_4xx_is_dead_lettered():
    actor = create_local_actor("alice")
    delivery = _delivery(actor)
    responses.add(responses.POST, INBOX, status=422)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DEAD
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_federation_tasks.py -v`
Expected: FAIL/ERROR — `federation.tasks` does not exist.

- [ ] **Step 3: Implement the task**

Create `federation/tasks.py`:

```python
from datetime import timedelta

from django.core.cache import cache
from django.tasks import task
from django.utils import timezone

from federation import client, policy
from federation.models import Delivery, DeliveryStatus

BACKOFF_SCHEDULE = [60, 300, 1800, 7200, 43200, 86400]
THROTTLE_LIMIT = 30
THROTTLE_WINDOW = 60


def backoff_seconds(attempt: int) -> int:
    index = min(attempt, len(BACKOFF_SCHEDULE) - 1)
    return BACKOFF_SCHEDULE[index]


def _throttle_allows(inbox_url: str) -> bool:
    from urllib.parse import urlparse

    domain = urlparse(inbox_url).netloc
    key = f"federation:throttle:{domain}"
    count = cache.get(key, 0)
    if count >= THROTTLE_LIMIT:
        return False
    cache.set(key, count + 1, timeout=THROTTLE_WINDOW)
    return True


def _dead_letter(delivery, error):
    delivery.status = DeliveryStatus.DEAD
    delivery.last_error = error
    delivery.save(update_fields=["status", "last_error", "updated_at"])


def _reschedule(delivery, error):
    delivery.attempts += 1
    delivery.last_error = error
    if delivery.attempts >= delivery.max_attempts:
        _dead_letter(delivery, error)
        return
    delivery.status = DeliveryStatus.FAILED
    delay = backoff_seconds(delivery.attempts - 1)
    delivery.next_attempt_at = timezone.now() + timedelta(seconds=delay)
    delivery.save(
        update_fields=["attempts", "status", "next_attempt_at", "last_error", "updated_at"]
    )
    deliver_activity.using(run_after=delivery.next_attempt_at).enqueue(delivery.id)


def _deliver(delivery_id):
    try:
        delivery = Delivery.objects.get(id=delivery_id)
    except Delivery.DoesNotExist:
        return
    if delivery.status in (DeliveryStatus.DELIVERED, DeliveryStatus.DEAD):
        return
    if not policy.delivery_allowed(delivery.activity, delivery.inbox_url):
        _dead_letter(delivery, "blocked by instance policy")
        return
    if not _throttle_allows(delivery.inbox_url):
        _reschedule(delivery, "rate limited")
        return
    try:
        response = client.post_activity(
            delivery.inbox_url, delivery.activity, delivery.actor
        )
    except Exception as exc:
        _reschedule(delivery, str(exc))
        return
    if 200 <= response.status_code < 300:
        delivery.status = DeliveryStatus.DELIVERED
        delivery.last_error = ""
        delivery.save(update_fields=["status", "last_error", "updated_at"])
    elif response.status_code == 429 or response.status_code >= 500:
        _reschedule(delivery, f"HTTP {response.status_code}")
    else:
        _dead_letter(delivery, f"HTTP {response.status_code}")


@task
def deliver_activity(delivery_id):
    _deliver(delivery_id)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_federation_tasks.py tests/test_federation_delivery.py -v`
Expected: PASS. If `deliver_activity.func` is not the callable attribute, use the function reference directly (define `_deliver` and have the task call it) and test `_deliver`.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run python manage.py makemigrations --check --dry-run`
Expected: all green and no missing migrations.

- [ ] **Step 6: Commit**

```bash
git add federation/tasks.py tests/test_federation_tasks.py
git commit -m "feat: deliver activities with retries, dead-lettering, and throttling"
```

---

## Self-Review

**Spec coverage (Ticket 3b scope):** outbound `Create`/`Update`/`Delete` delivery via Django Tasks with retry/backoff/dead-lettering (Tasks 3, 5); shared-inbox dedupe (Task 4); cavage↔RFC 9421 double-knock with a remembered per-domain preference (Task 3); secure-mode signed fetch with the instance actor (Task 3); blocked/silenced instance gating (Task 4/5); per-domain outbound throttle (Task 5); AS2 builders + actor `assertionMethod` (Task 1). Inbound, reply handling, and the outbox endpoints are 3c; concrete page serialization is ticket 6.

**Placeholder scan:** no TBD/TODO steps; every code step carries full content.

**Type consistency:** `Delivery` rows carry the caller's AS2 `activity` dict and the signing `actor`; `client.post_activity` is the single outbound call; `policy.delivery_allowed` is the single gate; `backoff_seconds` is the single retry schedule.

**Known deviations recorded as ledger rulings during execution:** (1) silenced handling is interpreted as withholding public activities while still delivering directed ones; (2) retries are scheduled by re-enqueueing rather than a library retry API; (3) `SOFTWARE_VERSION` may be reused from `actors.software` rather than a new setting.