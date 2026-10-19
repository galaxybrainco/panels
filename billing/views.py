import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from billing import services
from comics.models import Comic


def _require_enabled():
    if not settings.BILLING_ENABLED:
        raise Http404


def _interval(request):
    content_type = request.content_type or ""
    if not request.body.strip() or not content_type.startswith("application/json"):
        return "month"
    try:
        payload = json.loads(request.body)
    except ValueError as exc:
        raise services.InvalidPayload from exc
    if not isinstance(payload, dict):
        raise services.InvalidPayload
    return payload.get("interval") or "month"


@require_POST
@login_required
def checkout(request, comic_id):
    _require_enabled()
    comic = get_object_or_404(Comic, pk=comic_id)
    try:
        url = services.start_checkout(comic, request.user, _interval(request))
    except services.InvalidPayload:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    except services.InvalidInterval:
        return JsonResponse({"error": "invalid_interval"}, status=400)
    except services.NotOwner:
        return JsonResponse({"error": "not_owner"}, status=403)
    except services.AlreadySubscribed:
        return JsonResponse({"error": "already_subscribed"}, status=409)
    except services.StripeError:
        return JsonResponse({"error": "stripe_error"}, status=502)
    return JsonResponse({"checkout_url": url})
