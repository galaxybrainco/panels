import base64
import hashlib
from datetime import UTC, datetime

import pytest
import requests

from actors.services import create_local_actor
from federation.http_signatures import (
    content_digest,
    legacy_digest,
    sign_cavage,
    verify_cavage,
)
from federation.keys import load_actor_keys, rsa_public_key_from_actor

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def _prepared(url, body=b"", date="Mon, 28 Sep 2026 12:00:00 GMT"):
    request = requests.Request("POST", url, data=body)
    prepared = request.prepare()
    prepared.headers["Host"] = "panels.test"
    prepared.headers["Date"] = date
    prepared.headers["Content-Type"] = "application/activity+json"
    if body:
        prepared.headers["Digest"] = legacy_digest(body)
    return prepared


def test_content_digest_rfc9530_format():
    body = b'{"hello": "world"}'
    digest = content_digest(body)
    expected = base64.standard_b64encode(hashlib.sha256(body).digest()).decode()
    assert digest == f"sha-256=:{expected}:"


def test_legacy_digest_format():
    assert legacy_digest(b"") == (
        "SHA-256=47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="
    )


@pytest.mark.django_db
def test_cavage_sign_verify_roundtrip():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox", b'{"type":"Create"}')
    message.headers["Signature"] = sign_cavage(
        message, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    assert (
        verify_cavage(
            message,
            lambda key_id: rsa_public_key_from_actor(actor),
            now=NOW,
        )
        is True
    )


@pytest.mark.django_db
def test_cavage_verify_rejects_tampered_body_digest():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox", b'{"type":"Create"}')
    message.headers["Signature"] = sign_cavage(
        message, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    message.headers["Digest"] = legacy_digest(b'{"type":"Delete"}')
    assert (
        verify_cavage(message, lambda key_id: rsa_public_key_from_actor(actor), now=NOW)
        is False
    )


@pytest.mark.django_db
def test_cavage_verify_rejects_signature_not_covering_required_headers():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox", b'{"type":"Create"}')
    message.headers["Signature"] = sign_cavage(
        message,
        keys.rsa_private_key,
        f"{actor.ap_id}#main-key",
        headers=["(request-target)"],
    )
    assert (
        verify_cavage(message, lambda key_id: rsa_public_key_from_actor(actor), now=NOW)
        is False
    )


@pytest.mark.django_db
def test_cavage_verify_rejects_stale_date():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared(
        "https://panels.test/actors/alice/inbox",
        b'{"type":"Create"}',
        date="Mon, 21 Sep 2026 12:00:00 GMT",
    )
    message.headers["Signature"] = sign_cavage(
        message, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    assert (
        verify_cavage(message, lambda key_id: rsa_public_key_from_actor(actor), now=NOW)
        is False
    )


@pytest.mark.django_db
def test_cavage_verify_rejects_unknown_key():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox", b"{}")
    message.headers["Signature"] = sign_cavage(
        message, keys.rsa_private_key, f"{actor.ap_id}#main-key"
    )
    assert verify_cavage(message, lambda key_id: None, now=NOW) is False


@pytest.mark.django_db
def test_cavage_verify_returns_false_for_malformed_header():
    actor = create_local_actor("alice")
    message = _prepared("https://panels.test/actors/alice/inbox", b"{}")
    message.headers["Signature"] = "garbage"
    assert (
        verify_cavage(message, lambda key_id: rsa_public_key_from_actor(actor), now=NOW)
        is False
    )


@pytest.mark.django_db
def test_cavage_requires_signed_headers_present():
    actor = create_local_actor("alice")
    keys = load_actor_keys(actor)
    message = _prepared("https://panels.test/actors/alice/inbox")
    del message.headers["Date"]
    with pytest.raises(ValueError):
        sign_cavage(message, keys.rsa_private_key, f"{actor.ap_id}#main-key")
