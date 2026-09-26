from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from accounts.models import User
from actors.activitypub import ACTIVITYPUB_CONTENT_TYPE, actor_to_activitypub
from actors.models import Actor
from actors.software import SOFTWARE_NAME, SOFTWARE_VERSION

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


def nodeinfo_discovery(request):
    href = request.build_absolute_uri(reverse("nodeinfo"))
    return JsonResponse(
        {
            "links": [
                {
                    "rel": "http://nodeinfo.diaspora.software/ns/schema/2.1",
                    "href": href,
                }
            ]
        }
    )


def nodeinfo(request):
    return JsonResponse(
        {
            "version": "2.1",
            "software": {"name": SOFTWARE_NAME, "version": SOFTWARE_VERSION},
            "protocols": ["activitypub"],
            "services": {"inbound": [], "outbound": []},
            "openRegistrations": settings.INSTANCE_OPEN_REGISTRATIONS,
            "usage": {"users": {"total": User.objects.count()}},
            "metadata": {
                "nodeName": settings.INSTANCE_NAME,
                "nodeDescription": settings.INSTANCE_DESCRIPTION,
            },
        }
    )
