import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from memberships import kofi
from memberships.models import Provider, ProviderConnection


@csrf_exempt
def kofi_webhook(request, comic_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    connection = get_object_or_404(
        ProviderConnection, comic_id=comic_id, provider=Provider.KOFI
    )
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"error": "invalid json"}, status=400)
    else:
        payload = request.POST.dict()
    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid payload"}, status=400)
    if kofi.process_kofi_event(connection, payload) is None:
        return JsonResponse({"error": "invalid token"}, status=403)
    return JsonResponse({"ok": True})
