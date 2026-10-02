from django.shortcuts import get_object_or_404

from actors.models import Actor
from federation.inbound import process_inbox


def shared_inbox(request):
    return process_inbox(request)


def actor_inbox(request, handle):
    get_object_or_404(Actor, handle=handle, domain="")
    return process_inbox(request)
