from uuid import uuid4

from django.conf import settings

from actors.models import Actor
from federation import client
from federation.activitypub import build_activity
from federation.delivery import fan_out
from social.models import Follow
from social.services import follower_inboxes


class NotPermittedError(Exception):
    pass


class InvalidTargetError(Exception):
    pass


def _target_id(value):
    if isinstance(value, dict):
        return value.get("id")
    return value


def _references_actor(target, actor):
    if target.startswith(settings.INSTANCE_URL):
        row = Actor.objects.filter(ap_id=target).first()
        return row is not None and actor.ap_id in (row.also_known_as or [])
    document = client.fetch_json(target)
    return (
        isinstance(document, dict)
        and document.get("id") == target
        and actor.ap_id in (document.get("alsoKnownAs") or [])
    )


def move_actor(actor, user, target):
    if actor.user_id != user.id:
        raise NotPermittedError
    target = _target_id(target)
    if not isinstance(target, str) or not target.startswith(("http://", "https://")):
        raise InvalidTargetError
    if target == actor.ap_id:
        raise InvalidTargetError
    if not _references_actor(target, actor):
        raise InvalidTargetError
    aliases = list(actor.also_known_as or [])
    if target not in aliases:
        aliases.append(target)
    actor.also_known_as = aliases
    actor.moved_to = target
    actor.save(update_fields=["also_known_as", "moved_to", "updated_at"])
    payload = build_activity(
        "Move", actor, actor.ap_id, activity_id=f"{actor.ap_id}#moves/{uuid4()}"
    )
    payload["target"] = target
    inboxes = follower_inboxes(actor)
    if inboxes:
        fan_out(payload, inboxes, actor)
    return actor


def migrate_follows(old, new):
    for follow in list(Follow.objects.filter(target=old)):
        if Follow.objects.filter(follower=follow.follower, target=new).exists():
            follow.delete()
        else:
            follow.target = new
            follow.save(update_fields=["target", "updated_at"])
