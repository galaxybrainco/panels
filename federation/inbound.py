import json
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from django.core.cache import cache
from django.http import JsonResponse

from actors.models import Instance
from federation import handlers, http_signatures, remotes, rfc9421
from federation.models import Activity, ActivityStatus

INBOUND_LIMIT = 120
INBOUND_ACTOR_LIMIT = 120
INBOUND_WINDOW = 60


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


class _RecordingKeyResolver:
    def __init__(self, resolve_public_key):
        self._resolve_public_key = resolve_public_key
        self.key_id = None

    def __call__(self, key_id):
        self.key_id = key_id
        key = self._resolve_public_key(key_id)
        if key is None:
            raise KeyError(key_id)
        return key


def verify_inbound(request, resolve_public_key):
    message = request_message(request)
    resolver = _RecordingKeyResolver(resolve_public_key)

    try:
        cavage_verified = http_signatures.verify_cavage(message, resolver)
    except KeyError:
        cavage_verified = False
    if cavage_verified and verify_body_digest(message):
        return VerifiedSigner(key_id=resolver.key_id, scheme="cavage")

    if rfc9421.verify_rfc9421(message, resolver) and verify_body_digest(message):
        return VerifiedSigner(key_id=resolver.key_id, scheme="rfc9421")
    return None


def _throttle(identifier, limit) -> bool:
    key = f"federation:inbound:{identifier}"
    if cache.add(key, 1, timeout=INBOUND_WINDOW):
        return True
    try:
        return cache.incr(key) <= limit
    except ValueError:
        cache.set(key, 1, timeout=INBOUND_WINDOW)
        return True


def _source_identifier(request):
    return request.META.get("REMOTE_ADDR", "")


def _strip_media(value):
    if isinstance(value, dict):
        return {
            key: _strip_media(item)
            for key, item in value.items()
            if key != "attachment"
        }
    if isinstance(value, list):
        return [_strip_media(item) for item in value]
    return value


def process_inbox(request):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    if not _throttle(f"ip:{_source_identifier(request)}", INBOUND_LIMIT):
        return JsonResponse({"error": "rate limited"}, status=429)
    try:
        document = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid json"}, status=400)
    if not isinstance(document, dict) or not document.get("id"):
        return JsonResponse({"error": "invalid activity"}, status=400)

    signer = verify_inbound(request, remotes.public_key_for_key_id)
    if signer is None:
        return JsonResponse({"error": "invalid signature"}, status=401)
    actor = remotes.resolve_actor_by_key_id(signer.key_id)
    if actor is None:
        return JsonResponse({"error": "unknown actor"}, status=401)
    if document.get("actor") != actor.ap_id:
        return JsonResponse({"error": "actor mismatch"}, status=403)
    if not _throttle(f"actor:{actor.ap_id}", INBOUND_ACTOR_LIMIT):
        return JsonResponse({"error": "rate limited"}, status=429)

    instance = Instance.objects.filter(domain=urlparse(actor.ap_id).netloc).first()
    if instance is not None and instance.blocked:
        return JsonResponse({"error": "blocked"}, status=403)
    if (
        instance is not None
        and instance.reject_reports
        and document.get("type") == "Flag"
    ):
        return JsonResponse({"accepted": True}, status=202)
    if instance is not None and instance.reject_media:
        document = _strip_media(document)

    stored, created = Activity.objects.get_or_create(
        ap_id=document["id"],
        defaults={
            "type": document.get("type", ""),
            "actor": actor,
            "payload": document,
        },
    )
    if not created and stored.status == ActivityStatus.PROCESSED:
        return JsonResponse({"accepted": True, "duplicate": True}, status=202)
    if not created:
        stored.type = document.get("type", "")
        stored.actor = actor
        stored.payload = document
        stored.status = ActivityStatus.RECEIVED
        stored.error = ""
        stored.save(
            update_fields=[
                "type",
                "actor",
                "payload",
                "status",
                "error",
                "updated_at",
            ]
        )
    try:
        handlers.dispatch(stored)
    except Exception as exc:
        stored.status = ActivityStatus.REJECTED
        stored.error = str(exc)
        stored.save(update_fields=["status", "error", "updated_at"])
    return JsonResponse({"accepted": True}, status=202)
