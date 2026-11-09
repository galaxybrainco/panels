import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from actors.activitypub import ACTIVITYPUB_CONTENT_TYPE
from comics import permissions, quotes
from comics import replies as reply_services
from comics.federation import federation_plan, page_to_note
from comics.models import Comic, Page, PageStatus, QuoteRequest, QuoteStatus
from comics.quotes import authorization_object


def page_detail(request, page_id):
    page = get_object_or_404(Page, pk=page_id, status=PageStatus.PUBLISHED)
    from moderation import accounts

    if not accounts.publicly_visible(page.series.comic.actor):
        raise Http404
    plan = federation_plan(page)
    if not plan.emit:
        raise Http404
    from federation import integrity

    note = integrity.attach_proof(
        page_to_note(page, plan=plan), page.series.comic.actor
    )
    response = JsonResponse(note, content_type=ACTIVITYPUB_CONTENT_TYPE)
    response["Access-Control-Allow-Origin"] = "*"
    return response


@login_required
@require_POST
def payment_links(request, slug):
    comic = get_object_or_404(Comic, slug=slug)
    if not permissions.is_owner(request.user, comic):
        return JsonResponse({"error": "not_owner"}, status=403)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    links = data.get("links") if isinstance(data, dict) else None
    if not isinstance(links, list) or len(links) > 10:
        return JsonResponse({"error": "invalid_links"}, status=400)
    validator = URLValidator(schemes=["http", "https"])
    cleaned = []
    for item in links:
        if not isinstance(item, dict):
            return JsonResponse({"error": "invalid_links"}, status=400)
        name = str(item.get("name") or "").strip()
        url = item.get("url")
        rel = str(item.get("rel") or "payment").strip() or "payment"
        if not name or len(name) > 100 or not isinstance(url, str) or len(rel) > 32:
            return JsonResponse({"error": "invalid_links"}, status=400)
        try:
            validator(url)
        except ValidationError:
            return JsonResponse({"error": "invalid_links"}, status=400)
        cleaned.append({"name": name, "url": url, "rel": rel})
    comic.payment_links = cleaned
    comic.save(update_fields=["payment_links", "updated_at"])
    return JsonResponse({"payment_links": cleaned})


def _quote_data(request):
    return {
        "id": str(request.id),
        "page": str(request.page_id),
        "series": request.page.series.slug,
        "position": request.page.position,
        "actor": request.actor.ap_id,
        "quote_id": request.quote_id,
        "status": request.status,
        "created_at": request.created_at.isoformat(),
    }


@login_required
def quote_request_list(request, slug):
    comic = get_object_or_404(Comic, slug=slug)
    if not permissions.is_owner(request.user, comic):
        return JsonResponse({"error": "not_owner"}, status=403)
    status = request.GET.get("status")
    try:
        rows = quotes.quote_requests(comic, request.user, status=status)
    except quotes.NotPermittedError:
        return JsonResponse({"error": "not_owner"}, status=403)
    return JsonResponse({"quotes": [_quote_data(row) for row in rows]})


@login_required
@require_POST
def quote_decision(request, slug, request_id):
    comic = get_object_or_404(Comic, slug=slug)
    if not permissions.is_owner(request.user, comic):
        return JsonResponse({"error": "not_owner"}, status=403)
    quote = get_object_or_404(QuoteRequest, pk=request_id, page__series__comic=comic)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    decision = data.get("decision") if isinstance(data, dict) else None
    if decision not in {"approve", "reject"}:
        return JsonResponse({"error": "invalid_decision"}, status=400)
    try:
        quotes.decide_quote(quote, request.user, decision == "approve")
    except quotes.NotPermittedError:
        return JsonResponse({"error": "not_owner"}, status=403)
    except quotes.QuoteClosedError:
        return JsonResponse({"error": "closed"}, status=409)
    return JsonResponse(_quote_data(quote))


@login_required
@require_POST
def set_quote_policy(request, slug, series_slug, position):
    comic = get_object_or_404(Comic, slug=slug)
    if not permissions.is_owner(request.user, comic):
        return JsonResponse({"error": "not_owner"}, status=403)
    page = get_object_or_404(
        Page, series__comic=comic, series__slug=series_slug, position=position
    )
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    policy = data.get("policy") if isinstance(data, dict) else None
    try:
        quotes.set_quote_policy(page, request.user, policy)
    except quotes.InvalidPolicyError:
        return JsonResponse({"error": "invalid_policy"}, status=400)
    except quotes.NotPermittedError:
        return JsonResponse({"error": "not_owner"}, status=403)
    return JsonResponse({"policy": page.quote_policy})


def quote_authorization(request, authorization_uuid):
    expected = f"{settings.INSTANCE_URL}/quote-authorizations/{authorization_uuid}"
    quote = get_object_or_404(
        QuoteRequest, authorization_id=expected, status=QuoteStatus.APPROVED
    )
    return JsonResponse(
        authorization_object(quote), content_type=ACTIVITYPUB_CONTENT_TYPE
    )


@login_required
@require_POST
def set_reply_policy(request, slug, series_slug, position):
    comic = get_object_or_404(Comic, slug=slug)
    if not permissions.is_owner(request.user, comic):
        return JsonResponse({"error": "not_owner"}, status=403)
    page = get_object_or_404(
        Page, series__comic=comic, series__slug=series_slug, position=position
    )
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid_payload"}, status=400)
    policy = data.get("policy") if isinstance(data, dict) else None
    try:
        reply_services.set_reply_policy(page, request.user, policy)
    except reply_services.InvalidPolicyError:
        return JsonResponse({"error": "invalid_policy"}, status=400)
    except reply_services.NotPermittedError:
        return JsonResponse({"error": "not_owner"}, status=403)
    return JsonResponse({"policy": page.reply_policy})
