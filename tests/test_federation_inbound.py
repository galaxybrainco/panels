import pytest
from django.test import RequestFactory

from actors.services import create_local_actor
from federation.inbound import request_message, verify_body_digest, verify_inbound
from federation.keys import rsa_public_key_from_actor
from tests.federation_support import http_headers, signed_headers

INBOX = "http://testserver/inbox"


def _django_request(headers, body, path="/inbox"):
    factory = RequestFactory()
    return factory.post(
        path,
        data=body,
        content_type="application/activity+json",
        **http_headers(headers),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("scheme", ["cavage", "rfc9421"])
def test_verify_inbound_accepts_both_schemes(scheme):
    actor = create_local_actor("alice")
    body = b'{"type":"Create"}'
    request = _django_request(signed_headers(actor, INBOX, body, scheme=scheme), body)
    verified = verify_inbound(request, lambda key_id: rsa_public_key_from_actor(actor))
    assert verified is not None
    assert verified.key_id == f"{actor.ap_id}#main-key"
    assert verified.scheme == scheme


@pytest.mark.django_db
def test_verify_inbound_rejects_tampered_body():
    actor = create_local_actor("alice")
    body = b'{"type":"Create"}'
    request = _django_request(signed_headers(actor, INBOX, body), b'{"type":"Delete"}')
    assert (
        verify_inbound(request, lambda key_id: rsa_public_key_from_actor(actor)) is None
    )


@pytest.mark.django_db
def test_verify_inbound_rejects_unsigned_request():
    actor = create_local_actor("alice")
    request = _django_request({}, b"{}")
    assert (
        verify_inbound(request, lambda key_id: rsa_public_key_from_actor(actor)) is None
    )


def test_request_message_reconstructs_url_and_body():
    body = b'{"a":1}'
    headers = {"Host": "testserver", "Date": "Mon, 28 Sep 2026 12:00:00 GMT"}
    message = request_message(_django_request(headers, body))
    assert message.method == "POST"
    assert message.url == "http://testserver/inbox"
    assert message.body == body


def test_verify_body_digest():
    from federation.http_signatures import content_digest

    factory = RequestFactory()
    body = b'{"type":"Create"}'
    ok = factory.post(
        "/inbox",
        data=body,
        content_type="application/activity+json",
        HTTP_CONTENT_DIGEST=content_digest(body),
    )
    bad = factory.post(
        "/inbox",
        data=body,
        content_type="application/activity+json",
        HTTP_CONTENT_DIGEST=content_digest(b"other"),
    )
    assert verify_body_digest(request_message(ok)) is True
    assert verify_body_digest(request_message(bad)) is False
