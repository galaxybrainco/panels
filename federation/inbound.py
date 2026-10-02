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
    if "Host" not in message.headers:
        message.headers["Host"] = request.get_host()
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
