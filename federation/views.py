from urllib.parse import urlparse

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from actors.activitypub import ACTIVITYPUB_CONTENT_TYPE
from actors.models import Actor
from federation import collections, inbound, remotes
from federation.inbound import process_inbox


@csrf_exempt
def shared_inbox(request):
    return process_inbox(request)


@csrf_exempt
def actor_inbox(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    from moderation import accounts

    if not accounts.publicly_visible(actor):
        raise Http404
    return process_inbox(request)


def actor_outbox(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    from moderation import accounts

    if not accounts.publicly_visible(actor):
        raise Http404
    page_param = request.GET.get("page")
    page = int(page_param) if page_param and page_param.isdigit() else None
    return JsonResponse(collections.actor_outbox(actor, page=page))


def actor_followers(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    from moderation import accounts

    if not accounts.publicly_visible(actor):
        raise Http404
    page_param = request.GET.get("page")
    page = int(page_param) if page_param and page_param.isdigit() else None
    return JsonResponse(collections.actor_followers(actor, page=page))


def actor_following(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    from moderation import accounts

    if not accounts.publicly_visible(actor):
        raise Http404
    page_param = request.GET.get("page")
    page = int(page_param) if page_param and page_param.isdigit() else None
    return JsonResponse(collections.actor_following(actor, page=page))


def actor_featured(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    from moderation import accounts

    if not accounts.publicly_visible(actor):
        raise Http404
    return JsonResponse(collections.empty_collection(actor.featured))


def followers_synchronization(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    signer = inbound.verify_inbound(request, remotes.public_key_for_key_id)
    if signer is None:
        return JsonResponse({"error": "invalid signature"}, status=401)
    requester = remotes.resolve_actor_by_key_id(signer.key_id)
    if requester is None:
        return JsonResponse({"error": "unknown actor"}, status=401)
    domain = urlparse(requester.ap_id).netloc
    from social.models import Follow, FollowStatus

    ids = list(
        Follow.objects.filter(
            target=actor,
            status=FollowStatus.ACCEPTED,
            follower__domain=domain,
        ).values_list("follower__ap_id", flat=True)
    )
    return JsonResponse(
        {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": request.build_absolute_uri(),
            "type": "OrderedCollection",
            "totalItems": len(ids),
            "orderedItems": ids,
        },
        content_type=ACTIVITYPUB_CONTENT_TYPE,
    )
