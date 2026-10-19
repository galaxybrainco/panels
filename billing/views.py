import json
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from billing import services, stripe_client, webhooks
from comics.models import Comic


def billing_enabled(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not settings.BILLING_ENABLED:
            raise Http404
        return view(request, *args, **kwargs)

    return wrapped


def _interval(request):
    content_type = request.content_type or ""
    if not request.body.strip() or not content_type.startswith("application/json"):
        return "month"
    try:
        payload = json.loads(request.body)
    except ValueError as exc:
        raise services.InvalidPayloadError from exc
    if not isinstance(payload, dict):
        raise services.InvalidPayloadError
    return payload.get("interval") or "month"


@billing_enabled
@require_POST
@login_required
def checkout(request, comic_id):
    comic = get_object_or_404(Comic, pk=comic_id)
    try:
        url = services.start_checkout(comic, request.user, _interval(request))
    except services.InvalidPayloadError:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    except services.InvalidIntervalError:
        return JsonResponse({"error": "invalid_interval"}, status=400)
    except services.NotOwnerError:
        return JsonResponse({"error": "not_owner"}, status=403)
    except services.AlreadySubscribedError:
        return JsonResponse({"error": "already_subscribed"}, status=409)
    except services.StripeError:
        return JsonResponse({"error": "stripe_error"}, status=502)
    return JsonResponse({"checkout_url": url})


@billing_enabled
@require_POST
@login_required
def portal(request, comic_id):
    comic = get_object_or_404(Comic, pk=comic_id)
    try:
        url = services.open_portal(comic, request.user)
    except services.NotOwnerError:
        return JsonResponse({"error": "not_owner"}, status=403)
    except services.NoBillingAccountError:
        return JsonResponse({"error": "no_billing_account"}, status=409)
    except services.StripeError:
        return JsonResponse({"error": "stripe_error"}, status=502)
    return JsonResponse({"portal_url": url})


@billing_enabled
@csrf_exempt
@require_POST
def webhook(request):
    try:
        event = stripe_client.construct_event(
            request.body,
            request.headers.get("Stripe-Signature", ""),
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except stripe_client.InvalidSignatureError:
        return JsonResponse({"error": "invalid_signature"}, status=400)
    if event["type"] in webhooks.SUBSCRIPTION_EVENTS:
        webhooks.sync_subscription(event["data"]["object"])
    elif event["type"] in webhooks.PAYMENT_EVENTS:
        webhooks.handle_payment_failed(event["data"]["object"])
    return JsonResponse({"received": True})
