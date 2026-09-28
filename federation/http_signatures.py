import base64
import hashlib
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

DEFAULT_CAVAGE_HEADERS = ["(request-target)", "host", "date", "content-type", "digest"]
REQUIRED_CAVAGE_HEADERS = ("(request-target)", "host", "date")
DEFAULT_MAX_AGE = timedelta(hours=12)
FUTURE_SKEW = timedelta(minutes=5)


def content_digest(body: bytes) -> str:
    digest = base64.standard_b64encode(hashlib.sha256(body).digest()).decode("ascii")
    return f"sha-256=:{digest}:"


def legacy_digest(body: bytes) -> str:
    digest = base64.standard_b64encode(hashlib.sha256(body).digest()).decode("ascii")
    return f"SHA-256={digest}"


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


def _date_is_fresh(value, *, max_age, now=None) -> bool:
    if not value:
        return False
    try:
        date = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return False
    if date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    now = now or datetime.now(tz=UTC)
    age = now - date
    return -FUTURE_SKEW <= age <= max_age


def verify_cavage(
    message,
    resolve_public_key,
    *,
    required_headers=None,
    max_age=DEFAULT_MAX_AGE,
    now=None,
) -> bool:
    header = message.headers.get("Signature")
    if not header:
        return False
    parts = _parse_cavage_header(header)
    if parts.get("algorithm", "rsa-sha256") not in ("rsa-sha256", "hs2019"):
        return False
    try:
        key_id = parts["keyId"]
        signed_headers = parts["headers"].split(" ")
        signature = base64.b64decode(parts["signature"])
    except KeyError, ValueError:
        return False

    required = set(required_headers or REQUIRED_CAVAGE_HEADERS)
    if message.body:
        required.add("digest")
    if not required.issubset(set(signed_headers)):
        return False

    if not _date_is_fresh(message.headers.get("date"), max_age=max_age, now=now):
        return False

    public_key = resolve_public_key(key_id)
    if public_key is None:
        return False
    try:
        signing_string = build_cavage_signing_string(
            message.method, message.url, message.headers, signed_headers
        )
        public_key.verify(
            signature,
            signing_string.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception:
        return False
    return True
