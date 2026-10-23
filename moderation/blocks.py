from uuid import uuid4

from django.db import transaction
from django.db.models import Q

from actors.models import Actor
from federation.activitypub import build_activity
from federation.delivery import fan_out
from moderation.models import Block
from social.models import Follow


class NoActorError(Exception):
    pass


class SelfBlockError(Exception):
    pass


def personal_actor(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return Actor.objects.filter(user=user).first()


def _remove_follows(left, right):
    Follow.objects.filter(
        Q(follower=left, target=right) | Q(follower=right, target=left)
    ).delete()


def _inbox(actor):
    return actor.shared_inbox or actor.inbox


def block_actor(user, target):
    actor = personal_actor(user)
    if actor is None:
        raise NoActorError
    if actor == target:
        raise SelfBlockError
    with transaction.atomic():
        block, created = Block.objects.get_or_create(blocker=actor, blocked=target)
        _remove_follows(actor, target)
        if created and not target.is_local:
            activity_id = f"{actor.ap_id}#blocks/{uuid4()}"
            payload = build_activity(
                "Block", actor, target.ap_id, activity_id=activity_id
            )
            fan_out(payload, [_inbox(target)], actor)
            block.activity_id = activity_id
            block.save(update_fields=["activity_id", "updated_at"])
    return block


def unblock_actor(user, target):
    actor = personal_actor(user)
    if actor is None:
        raise NoActorError
    block = Block.objects.filter(blocker=actor, blocked=target).first()
    if block is None:
        return False
    with transaction.atomic():
        if not target.is_local:
            block_activity = {
                "id": block.activity_id or f"{actor.ap_id}#blocks/{uuid4()}",
                "type": "Block",
                "actor": actor.ap_id,
                "object": target.ap_id,
            }
            undo = build_activity(
                "Undo",
                actor,
                block_activity,
                activity_id=f"{actor.ap_id}#unblocks/{uuid4()}",
            )
            fan_out(undo, [_inbox(target)], actor)
        block.delete()
    return True


def _object_ap_id(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("id") or value.get("object") or ""
    return ""


def record_inbound_block(activity):
    ap_id = _object_ap_id(activity.payload.get("object"))
    blocked = Actor.objects.filter(ap_id=ap_id, domain="").first() if ap_id else None
    if blocked is None:
        return None
    with transaction.atomic():
        block, created = Block.objects.get_or_create(
            blocker=activity.actor,
            blocked=blocked,
            defaults={"activity_id": activity.ap_id},
        )
        if not created and not block.activity_id:
            block.activity_id = activity.ap_id
            block.save(update_fields=["activity_id", "updated_at"])
        _remove_follows(activity.actor, blocked)
    return block


def handle_undo_block(activity, obj):
    blocked_ap_id = _object_ap_id(obj.get("object"))
    activity_id = obj.get("id") or ""
    query = Block.objects.filter(blocker=activity.actor)
    if blocked_ap_id:
        query = query.filter(blocked__ap_id=blocked_ap_id)
    elif activity_id:
        query = query.filter(activity_id=activity_id)
    else:
        return 0
    return query.delete()[0]


def has_block_with_local(actor):
    if actor is None:
        return False
    return Block.objects.filter(
        Q(blocker=actor, blocked__domain="") | Q(blocker__domain="", blocked=actor)
    ).exists()


def should_drop_inbound(actor, activity_type):
    return activity_type != "Undo" and has_block_with_local(actor)


def is_blocked(user, actor):
    me = personal_actor(user)
    if me is None or actor is None:
        return False
    return Block.objects.filter(
        Q(blocker=me, blocked=actor) | Q(blocker=actor, blocked=me)
    ).exists()


def hidden_actor_ids(user):
    me = personal_actor(user)
    if me is None:
        return []
    ids = set(Block.objects.filter(blocker=me).values_list("blocked_id", flat=True))
    ids.update(Block.objects.filter(blocked=me).values_list("blocker_id", flat=True))
    return list(ids)
