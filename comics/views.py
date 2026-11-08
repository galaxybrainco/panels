import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from actors.activitypub import ACTIVITYPUB_CONTENT_TYPE
from comics import permissions
from comics.federation import federation_plan, page_to_note
from comics.models import Comic, Page, PageStatus


def page_detail(request, page_id):
    page = get_object_or_404(Page, pk=page_id, status=PageStatus.PUBLISHED)
    from moderation import accounts

    if not accounts.publicly_visible(page.series.comic.actor):
        raise Http404
    plan = federation_plan(page)
    if not plan.emit:
        raise Http404
    response = JsonResponse(
        page_to_note(page, plan=plan), content_type=ACTIVITYPUB_CONTENT_TYPE
    )
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
