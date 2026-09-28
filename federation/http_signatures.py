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
