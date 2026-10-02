from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from actors.models import Actor
from federation import collections
from federation.inbound import process_inbox


@csrf_exempt
def shared_inbox(request):
    return process_inbox(request)


@csrf_exempt
def actor_inbox(request, handle):
    get_object_or_404(Actor, handle=handle, domain="")
    return process_inbox(request)


def actor_outbox(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    page_param = request.GET.get("page")
    page = int(page_param) if page_param and page_param.isdigit() else None
    return JsonResponse(collections.actor_outbox(actor, page=page))


def actor_followers(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    return JsonResponse(collections.empty_collection(actor.followers))


def actor_following(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    return JsonResponse(collections.empty_collection(actor.following))


def actor_featured(request, handle):
    actor = get_object_or_404(Actor, handle=handle, domain="")
    return JsonResponse(collections.empty_collection(actor.featured))
