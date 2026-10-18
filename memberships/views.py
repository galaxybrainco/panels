import hmac
import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from memberships import kofi
from memberships.models import Provider, ProviderConnection


def _payload(request):
    if request.content_type == "application/json":
        try:
            return json.loads(request.body or b"{}")
        except ValueError:
            return None
    if "data" in request.POST:
        try:
            return json.loads(request.POST.get("data", ""))
        except ValueError:
            return None
    return request.POST.dict()


@csrf_exempt
def kofi_webhook(request, comic_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    connection = get_object_or_404(
        ProviderConnection, comic_id=comic_id, provider=Provider.KOFI
    )
    payload = _payload(request)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid payload"}, status=400)
    if not kofi.verify_token(connection, payload.get("verification_token")):
        return JsonResponse({"error": "invalid token"}, status=403)
    supporter = kofi.process_kofi_event(connection, payload)
    return JsonResponse({"ok": True, "ignored": supporter is None})


@csrf_exempt
def bmac_webhook(request, comic_id, secret):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    connection = get_object_or_404(
        ProviderConnection, comic_id=comic_id, provider=Provider.BMAC
    )
    stored = connection.get_webhook_token()
    if not stored or not hmac.compare_digest(stored.encode(), secret.encode()):
        return JsonResponse({"error": "invalid secret"}, status=403)
    from memberships.tasks import reconcile_bmac_comic

    reconcile_bmac_comic.enqueue(str(connection.id))
    return JsonResponse({"ok": True})
