from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction

from federation.activitypub import build_activity
from federation.delivery import fan_out
from social.models import Follow, FollowStatus


def follower_inboxes(actor):
    follows = (
        Follow.objects.filter(
            target=actor,
            status=FollowStatus.ACCEPTED,
            follower__domain__gt="",
        )
        .select_related("follower")
        .order_by("created_at")
    )
    inboxes = []
    for follow in follows:
        url = follow.follower.shared_inbox or follow.follower.inbox
        if url:
            inboxes.append(url)
    return inboxes


def _follow_object(actor, target, activity_id):
    return {
        "id": activity_id,
        "type": "Follow",
        "actor": actor.ap_id,
        "object": target.ap_id,
    }


def _target_inbox(target):
    return target.shared_inbox or target.inbox


@transaction.atomic
def follow(local_actor, target):
    if local_actor == target:
        raise ValidationError("You cannot follow yourself.")
    status = FollowStatus.ACCEPTED if target.is_local else FollowStatus.PENDING
    activity_id = "" if target.is_local else f"{local_actor.ap_id}#follows/{uuid4()}"
    follow_obj, created = Follow.objects.get_or_create(
        follower=local_actor,
        target=target,
        defaults={"status": status, "activity_id": activity_id},
    )
    if created and not target.is_local:
        payload = build_activity(
            "Follow", local_actor, target.ap_id, activity_id=activity_id
        )
        fan_out(payload, [_target_inbox(target)], local_actor)
    return follow_obj


@transaction.atomic
def unfollow(local_actor, target):
    follow_obj = Follow.objects.filter(follower=local_actor, target=target).first()
    if follow_obj is None:
        return None
    activity_id = follow_obj.activity_id or f"{local_actor.ap_id}#follows/{uuid4()}"
    follow_obj.delete()
    if not target.is_local:
        undo = build_activity(
            "Undo",
            local_actor,
            _follow_object(local_actor, target, activity_id),
            activity_id=f"{local_actor.ap_id}#unfollows/{uuid4()}",
        )
        fan_out(undo, [_target_inbox(target)], local_actor)
    return None


@transaction.atomic
def accept_follow(follow_obj):
    follow_obj.status = FollowStatus.ACCEPTED
    follow_obj.save(update_fields=["status", "updated_at"])
    if not follow_obj.follower.is_local:
        activity_id = follow_obj.activity_id or (
            f"{follow_obj.follower.ap_id}#follows/{uuid4()}"
        )
        accept = build_activity(
            "Accept",
            follow_obj.target,
            _follow_object(follow_obj.follower, follow_obj.target, activity_id),
            activity_id=f"{follow_obj.target.ap_id}#accepts/{uuid4()}",
        )
        fan_out(accept, [_target_inbox(follow_obj.follower)], follow_obj.target)
    return follow_obj
