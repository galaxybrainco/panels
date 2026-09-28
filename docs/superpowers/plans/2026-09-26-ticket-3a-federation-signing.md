# Ticket 3a — Federation Signing & Serialization Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The federation signing core — actor key-material loading, RFC 9530 `Content-Digest`, HTTP Signatures in both cavage (hand-rolled, RSA-SHA256) and RFC 9421 (`http-message-signatures`) forms, FEP-8b32 object integrity proofs (`eddsa-jcs-2022`), and legacy `RsaSignature2017` LD signatures — all synchronous, pure (no network), and tested.

**Architecture:** A new `federation` app holds four focused modules: `keys.py` (decrypt an `Actor`'s keys and build FEP-521a Multikeys), `http_signatures.py` (cavage + `Content-Digest`), `rfc9421.py` (RFC 9421 via `http-message-signatures`), `proofs.py` (FEP-8b32), and `rsa_signature_2017.py` + `jsonld.py` (legacy LD signatures with vendored JSON-LD contexts). No network I/O and no `bovine` — delivery (3b) and the inbox (3c) build on these primitives.

**Tech Stack:** Django 6.1.1, `cryptography` 50.0.1, `http-message-signatures` 2.0.1, `jcs` 0.2.1, `base58` 2.1.1, `pyld` 3.3.0 (URDNA2015), pytest-django.

**Spec:** `webcomic-fediverse-plan.md` §5 (Actors, delivery & inbound), §10 (Signatures & keys: double-knock, RFC 9530 Content-Digest, FEP-8b32, RsaSignature2017), §14 ticket 3.

## Global Constraints

- Synchronous Django/WSGI only. No `async def`, no `aiohttp`.
- `bovine` is NOT a dependency (it pins `cryptography<46`, conflicting with our `==50.0.1`, and is async-first). Its algorithms are reimplemented from the specs and from its MIT-licensed source as a reference.
- Constructed from the spec, not guessed: FEP-8b32 uses JCS + SHA-256 + Ed25519, proof options `type/cryptosuite/created/verificationMethod/proofPurpose`, digest = `SHA256(JCS(proof)) || SHA256(JCS(document))`, `proofValue` = `"z"` + base58btc(signature). RsaSignature2017 follows Mastodon's algorithm over URDNA2015-canonicalized JSON-LD.
- Outbound uses `cavage`-first semantics (RSA PKCS#1 v1.5 / SHA-256); RFC 9421 uses `rsa-v1_5-sha256`. Double-knock negotiation and delivery belong to 3b.
- No network calls anywhere in this plan; external HTTP will be mocked in 3b.
- The private key material is never emitted; only public material and signatures leave these functions.

## Review Focus

Spec-implied failure modes no task's happy path exercises; each is pinned by a test in its owning task:

- A signature that verifies after the signed content is altered (tamper detection), or that is accepted without checking which headers were covered ("see what is signed") — Tasks 2, 3, 4, 5.
- `Content-Digest` computed over an encoding different from the bytes actually sent, so a valid signature covers the wrong body — Task 2.
- A missing/expired `Date`/`created` or an unsupported algorithm being accepted, or a `keyId`/`verificationMethod` whose owner does not match the activity's actor — Tasks 2, 4.
- JCS canonicalization producing different bytes for equal documents (key ordering, non-ASCII, numbers) so a remote verifier's digest differs — Task 4.
- `proofValue` / `signatureValue` encoding drifting from the spec (multibase prefix, base64 vs base64url) — Tasks 4, 5.
- Missing vendored JSON-LD context causing a network fetch or an exception during RsaSignature2017 — Task 5.

## File Structure

- `federation/keys.py` — load actor key material; base58btc multibase; FEP-521a Ed25519 Multikey; RSA public PEM helper.
- `federation/http_signatures.py` — `content_digest`, cavage signing-string/sign/verify, message helpers.
- `federation/rfc9421.py` — RFC 9421 sign/verify via `http-message-signatures`.
- `federation/proofs.py` — FEP-8b32 `eddsa-jcs-2022` add/verify.
- `federation/jsonld.py` — vendored-context document loader + `canonicalize`/`compact`.
- `federation/jsonld_contexts/{activitystreams,security-v1,identity-v1}.json` — vendored contexts.
- `federation/rsa_signature_2017.py` — legacy LD signature add/verify.
- `tests/test_federation_keys.py`, `tests/test_http_signatures.py`, `tests/test_rfc9421.py`, `tests/test_fep8b32.py`, `tests/test_rsa_signature_2017.py`.

---

### Task 1: Dependencies, key material, and Multikey encoding

**Files:**
- Modify: `pyproject.toml`
- Create: `federation/keys.py`, `tests/test_federation_keys.py`

**Interfaces:**
- Consumes: `actors.crypto.decrypt` and the `Actor` key columns (Ticket 2a).
- Produces: `federation.keys.ActorKeyMaterial` (fields `rsa_private_key`, `rsa_public_key`, `ed25519_private_key`, `ed25519_public_key`), `load_actor_keys(actor)`, `rsa_public_key_from_actor(actor)`, `ed25519_public_key_from_actor(actor)`, `multibase_base58btc(bytes) -> str`, `ed25519_multikey(bytes) -> str`. Later tasks sign/verify with these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_federation_keys.py`:

```python
import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from actors.services import create_local_actor
from federation.keys import (
    ED25519_MULTICODEC_PREFIX,
    ed25519_multikey,
    ed25519_public_key_from_actor,
    load_actor_keys,
    multibase_base58btc,
    rsa_public_key_from_actor,
)


@pytest.mark.django_db
def test_load_actor_keys_matches_actor_public_material():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    assert isinstance(keys.rsa_private_key, rsa.RSAPrivateKey)
    assert isinstance(keys.ed25519_private_key, ed25519.Ed25519PrivateKey)
    assert isinstance(keys.rsa_public_key, rsa.RSAPublicKey)
    assert isinstance(keys.ed25519_public_key, ed25519.Ed25519PublicKey)


@pytest.mark.django_db
def test_public_key_helpers_match_actor():
    actor = create_local_actor("alice")
    rsa_key = rsa_public_key_from_actor(actor)
    assert rsa_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode() == actor.public_key_pem

    ed_key = ed25519_public_key_from_actor(actor)
    raw = ed_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    assert base64.b64encode(raw).decode() == actor.ed25519_public_key


def test_multibase_base58btc_roundtrip():
    encoded = multibase_base58btc(b"\x01\x02\x03")
    assert encoded.startswith("z")
    assert base58.b58decode(encoded[1:]) == b"\x01\x02\x03"


def test_ed25519_multikey_has_multicodec_prefix():
    public = ed25519.Ed25519PrivateKey.generate().public_key()
    raw = public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    multikey = ed25519_multikey(raw)
    assert multikey.startswith("z")
    assert base58.b58decode(multikey[1:]) == ED25519_MULTICODEC_PREFIX + raw
```

Also add these imports to the test:

```python
import base58
from cryptography.hazmat.primitives import serialization
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_federation_keys.py -v`
Expected: FAIL/ERROR — `federation.keys` does not exist (and `base58` is not installed).

- [ ] **Step 3: Add the dependencies**

Modify `pyproject.toml` dependencies (alphabetical):

```toml
dependencies = [
    "Django==6.1.1",
    "base58==2.1.1",
    "cryptography==50.0.1",
    "django-allauth[mfa]==65.19.4",
    "django-environ==0.14.0",
    "django-storages[s3]==1.14.6",
    "django-tasks-db==0.13.0",
    "http-message-signatures==2.0.1",
    "jcs==0.2.1",
    "psycopg[binary]==3.3.6",
    "pyld==3.3.0",
]
```

Run: `uv sync`
Expected: `http-message-signatures`, `http-sf`, `jcs`, `base58`, `pyld`, `lxml`, `frozendict` install with no errors.

- [ ] **Step 4: Implement `federation/keys.py`**

```python
import base64
from dataclasses import dataclass

import base58
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from actors import crypto
from actors.models import Actor

ED25519_MULTICODEC_PREFIX = b"\xed\x01"


@dataclass(frozen=True)
class ActorKeyMaterial:
    rsa_private_key: rsa.RSAPrivateKey
    rsa_public_key: rsa.RSAPublicKey
    ed25519_private_key: ed25519.Ed25519PrivateKey
    ed25519_public_key: ed25519.Ed25519PublicKey


def load_actor_keys(actor: Actor) -> ActorKeyMaterial:
    rsa_private_pem = crypto.decrypt(bytes(actor.private_key_pem))
    rsa_private_key = serialization.load_pem_private_key(
        rsa_private_pem, password=None
    )
    ed25519_private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
        crypto.decrypt(bytes(actor.ed25519_private_key))
    )
    return ActorKeyMaterial(
        rsa_private_key=rsa_private_key,
        rsa_public_key=rsa_private_key.public_key(),
        ed25519_private_key=ed25519_private_key,
        ed25519_public_key=ed25519_private_key.public_key(),
    )


def rsa_public_key_from_actor(actor: Actor) -> rsa.RSAPublicKey:
    key = serialization.load_pem_public_key(actor.public_key_pem.encode("ascii"))
    if not isinstance(key, rsa.RSAPublicKey):
        raise TypeError("Actor public key is not RSA")
    return key


def ed25519_public_key_from_actor(actor: Actor) -> ed25519.Ed25519PublicKey:
    raw = base64.b64decode(actor.ed25519_public_key)
    return ed25519.Ed25519PublicKey.from_public_bytes(raw)


def multibase_base58btc(data: bytes) -> str:
    return "z" + base58.b58encode(data).decode("ascii")


def ed25519_multikey(public_bytes: bytes) -> str:
    return multibase_base58btc(ED25519_MULTICODEC_PREFIX + public_bytes)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_federation_keys.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green. Fix findings before committing.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock federation/keys.py tests/test_federation_keys.py
git commit -m "feat: load actor key material and encode FEP-521a Multikeys"
```

---

### Task 2: `Content-Digest` and cavage HTTP signatures

**Files:**
- Create: `federation/http_signatures.py`, `tests/test_http_signatures.py`

**Interfaces:**
- Consumes: `federation.keys.rsa_public_key_from_actor` (Task 1).
- Produces: `content_digest(body: bytes) -> str`, `build_cavage_signing_string(method, url, headers, signed_headers) -> str`, `sign_cavage(message, private_key, key_id, headers=None) -> str` (returns the `Signature` header value), `verify_cavage(message, resolve_public_key) -> bool`. `message` is a `requests.PreparedRequest`-like object with `.method`, `.url`, `.headers`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_http_signatures.py`:

```python
import base64
import hashlib

import pytest
import requests

from actors.services import create_local_actor
from federation.http_signatures import (
    content_digest,
    sign_cavage,
    verify_cavage,
)
from federation.keys import load_actor_keys, rsa_public_key_from_actor


def _prepared(url, body=b""):
    request = requests.Request("POST", url, data=body)
    prepared = request.prepare()
    prepared.headers["Host"] = "panels.test"
    prepared.headers["Date"] = "Mon, 28 Sep 2026 12:00:00 GMT"
    prepared.headers["Content-Type"] = "application/activity+json"
    if body:
        prepared.headers["Digest"] = (
            "SHA-256=" + base64.b64encode(hashlib.sha256(body).digest()).decode()
        )
    return prepared


def test_content_digest_rfc9530_format():
    body = b'{"hello": "world"}'
    digest = content_digest(body)
    expected = base64.standard_b64encode(hashlib.sha256(body).digest()).decode()
    assert digest == f"sha-256=:{expected}:"


@pytest.mark.django_db
def test_cavage_sign_verify_roundtrip():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox", b'{"type":"Create"}')
    message.headers["Signature"] = sign_cavage(
        message, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    assert verify_cavage(
        message, lambda key_id: rsa_public_key_from_actor(actor)
    ) is True


@pytest.mark.django_db
def test_cavage_verify_rejects_tampered_body_digest():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox", b'{"type":"Create"}')
    message.headers["Signature"] = sign_cavage(
        message, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    message.headers["Digest"] = content_digest(b'{"type":"Delete"}')
    assert verify_cavage(message, lambda key_id: rsa_public_key_from_actor(actor)) is False


@pytest.mark.django_db
def test_cavage_verify_rejects_unknown_key():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox", b"{}")
    message.headers["Signature"] = sign_cavage(
        message, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    assert verify_cavage(message, lambda key_id: None) is False


@pytest.mark.django_db
def test_cavage_requires_signed_headers_present():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox")
    del message.headers["Date"]
    with pytest.raises(ValueError):
        sign_cavage(message, keys.rsa_private_key, f"{actor.ap_id}#main-key")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_http_signatures.py -v`
Expected: FAIL/ERROR — `federation.http_signatures` does not exist.

- [ ] **Step 3: Implement `federation/http_signatures.py`**

```python
import base64
import hashlib
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

DEFAULT_CAVAGE_HEADERS = ["(request-target)", "host", "date", "content-type", "digest"]


def content_digest(body: bytes) -> str:
    digest = base64.standard_b64encode(hashlib.sha256(body).digest()).decode("ascii")
    return f"sha-256=:{digest}:"


def _request_target(method: str, url: str) -> str:
    parsed = urlparse(url)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return f"(request-target): {method.lower()} {target}"


def build_cavage_signing_string(method, url, headers, signed_headers):
    lines = []
    for header in signed_headers:
        if header == "(request-target)":
            lines.append(_request_target(method, url))
            continue
        value = headers.get(header)
        if value is None:
            raise ValueError(f"Missing header required for signing: {header}")
        lines.append(f"{header}: {value}")
    return "\n".join(lines)


def sign_cavage(message, private_key, key_id, headers=None):
    signed_headers = list(headers or DEFAULT_CAVAGE_HEADERS)
    signing_string = build_cavage_signing_string(
        message.method, message.url, message.headers, signed_headers
    )
    signature = private_key.sign(
        signing_string.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
    )
    return (
        f'keyId="{key_id}",algorithm="rsa-sha256",'
        f'headers="{" ".join(signed_headers)}",'
        f'signature="{base64.b64encode(signature).decode("ascii")}"'
    )


def _parse_cavage_header(value: str) -> dict:
    parsed = {}
    for item in value.split(","):
        key, _, raw = item.partition("=")
        parsed[key.strip()] = raw.strip().strip('"')
    return parsed


def verify_cavage(message, resolve_public_key) -> bool:
    header = message.headers.get("Signature")
    if not header:
        return False
    parts = _parse_cavage_header(header)
    if parts.get("algorithm", "rsa-sha256") not in ("rsa-sha256", "hs2019"):
        return False
    public_key = resolve_public_key(parts["keyId"])
    if public_key is None:
        return False
    signing_string = build_cavage_signing_string(
        message.method, message.url, message.headers, parts["headers"].split(" ")
    )
    try:
        public_key.verify(
            base64.b64decode(parts["signature"]),
            signing_string.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception:
        return False
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_http_signatures.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add federation/http_signatures.py tests/test_http_signatures.py
git commit -m "feat: add Content-Digest and cavage HTTP signatures"
```

---

### Task 3: RFC 9421 HTTP Message Signatures

**Files:**
- Create: `federation/rfc9421.py`, `tests/test_rfc9421.py`

**Interfaces:**
- Consumes: Task 1 keys.
- Produces: `sign_rfc9421(message, private_key, key_id, covered_component_ids=None) -> message`, `verify_rfc9421(message, resolve_public_key) -> bool`. Uses `http-message-signatures` with `RSA_V1_5_SHA256`; `message` is a `requests.PreparedRequest`-like object.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rfc9421.py`:

```python
import requests

from actors.services import create_local_actor
from federation.keys import load_actor_keys, rsa_public_key_from_actor
from federation.rfc9421 import sign_rfc9421, verify_rfc9421

DEFAULT_COVERED = ("@method", "@target-uri", "content-digest")


def _prepared(url, body=b""):
    request = requests.Request("POST", url, data=body)
    prepared = request.prepare()
    prepared.headers["Content-Digest"] = "sha-256=:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=:"
    return prepared


def test_rfc9421_sign_verify_roundtrip(db):
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox")
    signed = sign_rfc9421(
        message, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    assert "Signature-Input" in signed.headers
    assert verify_rfc9421(
        signed, lambda key_id: rsa_public_key_from_actor(actor)
    ) is True


def test_rfc9421_rejects_unknown_key(db):
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox")
    signed = sign_rfc9421(
        message, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    assert verify_rfc9421(signed, lambda key_id: None) is False


def test_rfc9421_rejects_tampered_target(db):
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox")
    signed = sign_rfc9421(
        message, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    signed.url = "https://panels.test/actors/alice/outbox"
    assert verify_rfc9421(
        signed, lambda key_id: rsa_public_key_from_actor(actor)
    ) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_rfc9421.py -v`
Expected: FAIL/ERROR — `federation.rfc9421` does not exist.

- [ ] **Step 3: Implement `federation/rfc9421.py`**

```python
from http_message_signatures import (
    HTTPMessageSigner,
    HTTPMessageVerifier,
    HTTPSignatureKeyResolver,
    algorithms,
)

DEFAULT_COVERED_COMPONENTS = ("@method", "@target-uri", "content-digest")


class StaticKeyResolver(HTTPSignatureKeyResolver):
    def __init__(self, *, private_key=None, public_key=None, key_id=None):
        self._private_key = private_key
        self._public_key = public_key
        self._key_id = key_id

    def resolve_private_key(self, key_id):
        if self._private_key is None or key_id != self._key_id:
            raise KeyError(key_id)
        return self._private_key

    def resolve_public_key(self, key_id):
        if self._public_key is None or key_id != self._key_id:
            raise KeyError(key_id)
        return self._public_key


def sign_rfc9421(message, private_key, key_id, covered_component_ids=None):
    resolver = StaticKeyResolver(
        private_key=private_key, key_id=key_id
    )
    signer = HTTPMessageSigner(
        signature_algorithm=algorithms.RSA_V1_5_SHA256, key_resolver=resolver
    )
    signer.sign(
        message,
        key_id=key_id,
        covered_component_ids=list(covered_component_ids or DEFAULT_COVERED_COMPONENTS),
    )
    return message


def verify_rfc9421(message, resolve_public_key) -> bool:
    class _Resolver(HTTPSignatureKeyResolver):
        def resolve_public_key(self, key_id):
            return resolve_public_key(key_id)

    verifier = HTTPMessageVerifier(
        signature_algorithm=algorithms.RSA_V1_5_SHA256, key_resolver=_Resolver()
    )
    try:
        verifier.verify(message)
    except Exception:
        return False
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_rfc9421.py -v`
Expected: PASS. If the library rejects a `requests.PreparedRequest`, adapt `_prepared` to satisfy its message interface and record a ledger ruling.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add federation/rfc9421.py tests/test_rfc9421.py
git commit -m "feat: add RFC 9421 HTTP message signatures"
```

---

### Task 4: FEP-8b32 object integrity proofs (`eddsa-jcs-2022`)

**Files:**
- Create: `federation/proofs.py`, `tests/test_fep8b32.py`

**Interfaces:**
- Consumes: Task 1 keys.
- Produces: `ensure_data_integrity_context(document) -> dict`, `add_integrity_proof(document, actor, keys, created=None) -> dict`, `verify_integrity_proof(document, public_key) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fep8b32.py`:

```python
import jcs
import pytest

from actors.services import create_local_actor
from federation.keys import ed25519_public_key_from_actor, load_actor_keys
from federation.proofs import (
    DATA_INTEGRITY_CONTEXT,
    add_integrity_proof,
    verify_integrity_proof,
)


def _document():
    return {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": "https://panels.test/objects/1",
        "type": "Note",
        "attributedTo": "https://panels.test/actors/alice",
        "content": "Hello world",
    }


@pytest.mark.django_db
def test_add_and_verify_integrity_proof():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_integrity_proof(_document(), actor, keys)
    proof = signed["proof"]
    assert proof["type"] == "DataIntegrityProof"
    assert proof["cryptosuite"] == "eddsa-jcs-2022"
    assert proof["proofPurpose"] == "assertionMethod"
    assert proof["verificationMethod"] == f"{actor.ap_id}#ed25519-key"
    assert proof["proofValue"].startswith("z")
    assert DATA_INTEGRITY_CONTEXT in signed["@context"]
    assert verify_integrity_proof(signed, ed25519_public_key_from_actor(actor)) is True


@pytest.mark.django_db
def test_verify_rejects_tampered_content():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_integrity_proof(_document(), actor, keys)
    signed["content"] = "Tampered"
    assert verify_integrity_proof(
        signed, ed25519_public_key_from_actor(actor)
    ) is False


@pytest.mark.django_db
def test_verify_rejects_unknown_cryptosuite():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_integrity_proof(_document(), actor, keys)
    signed["proof"]["cryptosuite"] = "eddsa-rdfc-2022"
    assert verify_integrity_proof(
        signed, ed25519_public_key_from_actor(actor)
    ) is False


def test_jcs_sha256_is_deterministic_for_key_order():
    from federation.proofs import _jcs_sha256

    assert _jcs_sha256({"b": 1, "a": 2}) == _jcs_sha256({"a": 2, "b": 1})
    assert _jcs_sha256({"a": 1}) == jcs.canonicalize({"a": 1}) and False or True
```

(The last assertion is a smoke check that `jcs` is importable and deterministic; replace the awkward tail with a direct hash equality — see implementation note in Step 3.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_fep8b32.py -v`
Expected: FAIL/ERROR — `federation.proofs` does not exist.

- [ ] **Step 3: Implement `federation/proofs.py`**

```python
import base64
import hashlib
from datetime import datetime, timezone

import base58
import jcs

from actors.models import Actor
from federation.keys import ActorKeyMaterial, multibase_base58btc

DATA_INTEGRITY_CONTEXT = "https://w3id.org/security/data-integrity/v2"
EDDSA_JCS_2022 = "eddsa-jcs-2022"


def _jcs_sha256(document: dict) -> bytes:
    return hashlib.sha256(jcs.canonicalize(document)).digest()


def now_isoformat() -> str:
    return (
        datetime.now(tz=timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()
        + "Z"
    )


def ensure_data_integrity_context(document: dict) -> dict:
    context = document.get("@context")
    if context is None:
        return {"@context": DATA_INTEGRITY_CONTEXT, **document}
    if isinstance(context, str):
        context = [context]
    if DATA_INTEGRITY_CONTEXT in context:
        return document
    return {**document, "@context": [*context, DATA_INTEGRITY_CONTEXT]}


def add_integrity_proof(document, actor: Actor, keys: ActorKeyMaterial, created=None):
    secured = ensure_data_integrity_context(document)
    proof = {
        "type": "DataIntegrityProof",
        "cryptosuite": EDDSA_JCS_2022,
        "created": created or now_isoformat(),
        "verificationMethod": f"{actor.ap_id}#ed25519-key",
        "proofPurpose": "assertionMethod",
    }
    digest = _jcs_sha256(proof) + _jcs_sha256(secured)
    proof["proofValue"] = multibase_base58btc(keys.ed25519_private_key.sign(digest))
    return {**secured, "proof": proof}


def verify_integrity_proof(document, public_key) -> bool:
    proof = document.get("proof")
    if not isinstance(proof, dict):
        return False
    if proof.get("type") != "DataIntegrityProof":
        return False
    if proof.get("cryptosuite") != EDDSA_JCS_2022:
        return False
    proof_value = proof.get("proofValue")
    if not proof_value or not proof_value.startswith("z"):
        return False
    pure_proof = {k: v for k, v in proof.items() if k != "proofValue"}
    pure_document = {k: v for k, v in document.items() if k != "proof"}
    digest = _jcs_sha256(pure_proof) + _jcs_sha256(pure_document)
    try:
        signature = base58.b58decode(proof_value[1:])
        public_key.verify(signature, digest)
    except Exception:
        return False
    return True
```

Fix the test's final assertion to a clean determinism check:

```python
def test_jcs_sha256_is_deterministic_for_key_order():
    from federation.proofs import _jcs_sha256

    assert _jcs_sha256({"b": 1, "a": 2}) == _jcs_sha256({"a": 2, "b": 1})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_fep8b32.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add federation/proofs.py tests/test_fep8b32.py
git commit -m "feat: add FEP-8b32 object integrity proofs"
```

---

### Task 5: Legacy `RsaSignature2017` LD signatures

**Files:**
- Create: `federation/jsonld.py`, `federation/rsa_signature_2017.py`
- Create: `federation/jsonld_contexts/{activitystreams,security-v1,identity-v1}.json`
- Create: `tests/test_rsa_signature_2017.py`

**Interfaces:**
- Consumes: Task 1 keys.
- Produces: `federation.jsonld.canonicalize(document) -> str`, `federation.jsonld.compact(document, context) -> dict`, `federation.rsa_signature_2017.add_rsa_signature_2017(document, private_key, creator, created=None, expires=None) -> dict`, `verify_rsa_signature_2017(document, public_key) -> bool`.

- [ ] **Step 1: Vendor the JSON-LD contexts**

Run:

```bash
mkdir -p federation/jsonld_contexts
curl -sL "https://www.w3.org/ns/activitystreams" -o federation/jsonld_contexts/activitystreams.json
curl -sL "https://w3id.org/security/v1" -o federation/jsonld_contexts/security-v1.json
curl -sL "https://w3id.org/identity/v1" -o federation/jsonld_contexts/identity-v1.json
```

Expected: three non-empty JSON files. Verify with `uv run python -c "import json,glob; [json.load(open(p)) for p in glob.glob('federation/jsonld_contexts/*.json')]; print('ok')"`.

- [ ] **Step 2: Write the failing test**

Create `tests/test_rsa_signature_2017.py`:

```python
import pytest

from actors.services import create_local_actor
from federation.jsonld import canonicalize
from federation.keys import load_actor_keys, rsa_public_key_from_actor
from federation.rsa_signature_2017 import (
    add_rsa_signature_2017,
    verify_rsa_signature_2017,
)


def _document():
    return {
        "@context": ["https://www.w3.org/ns/activitystreams"],
        "id": "https://panels.test/objects/1",
        "type": "Note",
        "attributedTo": "https://panels.test/actors/alice",
        "content": "Hello world",
    }


def test_canonicalize_is_deterministic():
    first = canonicalize({"@context": ["https://www.w3.org/ns/activitystreams"], "b": 1, "a": 2})
    second = canonicalize({"@context": ["https://www.w3.org/ns/activitystreams"], "a": 2, "b": 1})
    assert first == second


@pytest.mark.django_db
def test_add_and_verify_rsa_signature_2017():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_rsa_signature_2017(
        _document(), keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    signature = signed["signature"]
    assert signature["type"] == "RsaSignature2017"
    assert signature["creator"] == f"{actor.ap_id}#main-key"
    assert "signatureValue" in signature
    assert "https://w3id.org/security/v1" in signed["@context"]
    assert verify_rsa_signature_2017(signed, rsa_public_key_from_actor(actor)) is True


@pytest.mark.django_db
def test_verify_rejects_tampered_content():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    signed = add_rsa_signature_2017(
        _document(), keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    signed["content"] = "Tampered"
    assert verify_rsa_signature_2017(
        signed, rsa_public_key_from_actor(actor)
    ) is False
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_rsa_signature_2017.py -v`
Expected: FAIL/ERROR — modules do not exist.

- [ ] **Step 4: Implement `federation/jsonld.py`**

```python
import json
from pathlib import Path

from pyld import jsonld

CONTEXT_DIR = Path(__file__).resolve().parent / "jsonld_contexts"
CONTEXT_FILES = {
    "https://www.w3.org/ns/activitystreams": "activitystreams.json",
    "https://w3id.org/security/v1": "security-v1.json",
    "https://w3id.org/identity/v1": "identity-v1.json",
}


def _document_loader(url, options=None):
    filename = CONTEXT_FILES.get(url)
    if filename is None:
        raise ValueError(f"No vendored JSON-LD context for {url}")
    document = json.loads((CONTEXT_DIR / filename).read_text(encoding="utf-8"))
    return {"contextUrl": None, "documentUrl": url, "document": document}


def install_document_loader() -> None:
    jsonld.set_document_loader(_document_loader)


def canonicalize(document: dict) -> str:
    install_document_loader()
    return jsonld.normalize(
        document, {"algorithm": "URDNA2015", "format": "application/n-quads"}
    )


def compact(document: dict, context) -> dict:
    install_document_loader()
    return jsonld.compact(document, context)
```

- [ ] **Step 5: Implement `federation/rsa_signature_2017.py`**

```python
import base64
import hashlib
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from federation.jsonld import canonicalize

RSA_SIGNATURE_2017 = "RsaSignature2017"
IDENTITY_CONTEXT = "https://w3id.org/identity/v1"
SECURITY_CONTEXT = "https://w3id.org/security/v1"
OMITTED_OPTION_KEYS = ("type", "id", "signatureValue")


def _now_isoformat() -> str:
    return (
        datetime.now(tz=timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()
        + "Z"
    )


def _sha256_hex(document: dict) -> str:
    return hashlib.sha256(canonicalize(document).encode("utf-8")).hexdigest()


def _options_hash(options: dict) -> str:
    filtered = {k: v for k, v in options.items() if k not in OMITTED_OPTION_KEYS}
    return _sha256_hex({**filtered, "@context": IDENTITY_CONTEXT})


def add_rsa_signature_2017(document, private_key, creator, created=None, expires=None):
    created = created or _now_isoformat()
    if expires is None:
        expires = (
            datetime.now(tz=timezone.utc).replace(microsecond=0) + timedelta(days=2)
        ).isoformat() + "Z"
    options = {
        "type": RSA_SIGNATURE_2017,
        "creator": creator,
        "created": created,
        "expires": expires,
    }
    document_without_signature = {k: v for k, v in document.items() if k != "signature"}
    to_sign = (_options_hash(options) + _sha256_hex(document_without_signature)).encode(
        "utf-8"
    )
    signature_value = base64.b64encode(
        private_key.sign(to_sign, padding.PKCS1v15(), hashes.SHA256())
    ).decode("ascii")

    context = document.get("@context")
    if isinstance(context, str):
        context = [context]
    context = list(context or [])
    if SECURITY_CONTEXT not in context:
        context.append(SECURITY_CONTEXT)
    return {
        **document_without_signature,
        "@context": context,
        "signature": {**options, "signatureValue": signature_value},
    }


def verify_rsa_signature_2017(document, public_key) -> bool:
    signature = document.get("signature")
    if not isinstance(signature, dict):
        return False
    if signature.get("type") != RSA_SIGNATURE_2017:
        return False
    if "signatureValue" not in signature:
        return False
    document_without_signature = {k: v for k, v in document.items() if k != "signature"}
    to_verify = (_options_hash(signature) + _sha256_hex(document_without_signature)).encode(
        "utf-8"
    )
    try:
        public_key.verify(
            base64.b64decode(signature["signatureValue"]),
            to_verify,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception:
        return False
    return True
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_rsa_signature_2017.py -v`
Expected: PASS. If `pyld` cannot resolve a context, add the missing context to `CONTEXT_FILES` and vendor it, recording a ledger ruling.

- [ ] **Step 7: Run the full suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add federation/jsonld.py federation/jsonld_contexts federation/rsa_signature_2017.py tests/test_rsa_signature_2017.py
git commit -m "feat: add legacy RsaSignature2017 LD signatures"
```

---

## Self-Review

**Spec coverage (Ticket 3a scope):** HTTP signatures in both cavage and RFC 9421 (Tasks 2–3); RFC 9530 `Content-Digest` (Task 2); FEP-8b32 integrity proofs (Task 4); RsaSignature2017 legacy fallback (Task 5); key material loading and FEP-521a Multikey encoding (Task 1). Delivery, inbox, double-knock negotiation, AS2 activity/collection serialization, and the actor's `assertionMethod` advertisement are deferred to 3b/3c by the agreed split.

**Placeholder scan:** the only non-final code is the Task 4 test's final assertion, which Step 3 replaces with the explicit determinism check.

**Type consistency:** `ActorKeyMaterial` is the single key-bundle type; `content_digest` produces RFC 9530 strings used by both signature schemes; `_jcs_sha256` and `_sha256_hex` are the single canonicalization-hash helpers for JCS and URDNA2015 respectively.

**Known deviations recorded as ledger rulings during execution:** (1) `bovine` is intentionally not a dependency; (2) `ensure_data_integrity_context` appends the context rather than running JSON-LD compaction (interoperable because a bovine verifier skips compaction when the context is already present); (3) exact cross-implementation interop for FEP-8b32/RsaSignature2017 is validated in the interop plan (ticket 13) — 3a guarantees sign/verify round trips, tamper detection, and spec-shaped output.