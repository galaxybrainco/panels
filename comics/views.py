from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404

from actors.activitypub import ACTIVITYPUB_CONTENT_TYPE
from comics.federation import page_to_note
from comics.models import Page, PageStatus
from federation.activitypub import Audience


def page_detail(request, page_id):
    page = get_object_or_404(Page, pk=page_id, status=PageStatus.PUBLISHED)
    if page.audience in (Audience.MEMBERS, Audience.TIER):
        raise Http404
    response = JsonResponse(page_to_note(page), content_type=ACTIVITYPUB_CONTENT_TYPE)
    response["Access-Control-Allow-Origin"] = "*"
    return response
