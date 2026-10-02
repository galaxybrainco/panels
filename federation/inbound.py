import json
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from django.core.cache import cache
from django.http import JsonResponse

from actors.models import Instance
from federation import handlers, http_signatures, remotes, rfc9421
from federation.models import Activity

INBOUND_LIMIT = 120
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
        activity = {
            **activity,
            "object": {k: v for k, v in obj.items() if k != "attachment"},
        }
    return activity


def process_inbox(request):
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
    if activity.get("actor") != actor.ap_id:
        return JsonResponse({"error": "actor mismatch"}, status=403)

    instance = Instance.objects.filter(domain=urlparse(actor.ap_id).netloc).first()
    if instance is not None and instance.blocked:
        return JsonResponse({"error": "blocked"}, status=403)
    if (
        instance is not None
        and instance.reject_reports
        and activity.get("type") == "Flag"
    ):
        return JsonResponse({"accepted": True}, status=202)
    if instance is not None and instance.reject_media:
        activity = _strip_media(activity)

    if Activity.objects.filter(ap_id=activity["id"]).exists():
        return JsonResponse({"accepted": True, "duplicate": True}, status=202)
    stored = Activity.objects.create(
        ap_id=activity["id"],
        type=activity.get("type", ""),
        actor=actor,
        payload=activity,
    )
    handlers.dispatch(stored)
    return JsonResponse({"accepted": True}, status=202)
