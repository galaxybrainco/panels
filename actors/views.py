from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404

from actors.activitypub import ACTIVITYPUB_CONTENT_TYPE, actor_to_activitypub
from actors.models import Actor

JRD_CONTENT_TYPE = "application/jrd+json"


def actor_detail(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    response = JsonResponse(
        actor_to_activitypub(actor), content_type=ACTIVITYPUB_CONTENT_TYPE
    )
    response["Access-Control-Allow-Origin"] = "*"
    return response


def _parse_acct(resource: str, domain: str):
    prefix = "acct:"
    if not resource.startswith(prefix):
        return None
    handle, sep, acct_domain = resource[len(prefix) :].partition("@")
    if not sep or not handle or acct_domain.lower() != domain.lower():
        return None
    return handle


def webfinger(request):
    handle = _parse_acct(request.GET.get("resource", ""), settings.INSTANCE_DOMAIN)
    if handle is None:
        raise Http404
    actor = Actor.objects.filter(handle=handle, domain="").first()
    if actor is None:
        raise Http404
    document = {
        "subject": f"acct:{actor.handle}@{settings.INSTANCE_DOMAIN}",
        "aliases": [actor.ap_id],
        "links": [
            {"rel": "self", "type": ACTIVITYPUB_CONTENT_TYPE, "href": actor.ap_id},
        ],
    }
    response = JsonResponse(document, content_type=JRD_CONTENT_TYPE)
    response["Access-Control-Allow-Origin"] = "*"
    return response
