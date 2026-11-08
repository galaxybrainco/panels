import hashlib
from urllib.parse import urlparse

from django.conf import settings

from social.models import Follow, FollowStatus


def _digest(ids):
    value = 0
    for ap_id in ids:
        value ^= int.from_bytes(hashlib.sha256(ap_id.encode("utf-8")).digest(), "big")
    return f"{value:064x}"


def header_params(actor, inbox_url):
    domain = urlparse(inbox_url).netloc
    ids = list(
        Follow.objects.filter(
            target=actor,
            status=FollowStatus.ACCEPTED,
            follower__domain=domain,
        ).values_list("follower__ap_id", flat=True)
    )
    if not ids:
        return {}
    return {
        "collectionId": actor.followers,
        "url": f"{settings.INSTANCE_URL}/actors/{actor.handle}/followers_synchronization",
        "digest": _digest(ids),
    }


def header_value(params):
    return (
        f'collectionId="{params["collectionId"]}", '
        f'url="{params["url"]}", digest="{params["digest"]}"'
    )


def parse_header(value):
    parsed = {}
    for item in (value or "").split(","):
        key, _, raw = item.partition("=")
        parsed[key.strip()] = raw.strip().strip('"')
    return parsed


def local_partial_digest(actor):
    ids = list(
        Follow.objects.filter(
            target=actor,
            status=FollowStatus.ACCEPTED,
            follower__domain="",
        ).values_list("follower__ap_id", flat=True)
    )
    return _digest(ids)


def reconcile(actor, listed):
    follows = list(
        Follow.objects.filter(target=actor, follower__domain="").select_related(
            "follower"
        )
    )
    for follow in follows:
        if follow.follower.ap_id not in listed:
            follow.delete()
        elif follow.status != FollowStatus.ACCEPTED:
            follow.status = FollowStatus.ACCEPTED
            follow.save(update_fields=["status", "updated_at"])


def receive(actor, header):
    params = parse_header(header)
    if params.get("collectionId") != actor.followers:
        return False
    url = params.get("url", "")
    if not url or urlparse(url).netloc != urlparse(actor.ap_id).netloc:
        return False
    digest = params.get("digest", "")
    if not digest or digest == local_partial_digest(actor):
        return False
    from federation import client

    data = client.fetch_json(url)
    if not isinstance(data, dict) or not isinstance(data.get("orderedItems"), list):
        return False
    reconcile(actor, set(data["orderedItems"]))
    return True
