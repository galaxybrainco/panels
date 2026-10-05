from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404

from actors.activitypub import ACTIVITYPUB_CONTENT_TYPE
from comics.federation import federation_plan, page_to_note
from comics.models import Page, PageStatus


def page_detail(request, page_id):
    page = get_object_or_404(Page, pk=page_id, status=PageStatus.PUBLISHED)
    plan = federation_plan(page)
    if not plan.emit:
        raise Http404
    response = JsonResponse(
        page_to_note(page, plan=plan), content_type=ACTIVITYPUB_CONTENT_TYPE
    )
    response["Access-Control-Allow-Origin"] = "*"
    return response
