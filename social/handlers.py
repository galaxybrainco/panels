from actors.models import Actor
from federation import handlers
from social import services
from social.models import Follow, FollowStatus


@handlers.register("Follow")
def handle_follow(activity):
    target = Actor.objects.filter(
        ap_id=activity.payload.get("object"), domain=""
    ).first()
    if target is None:
        return
    follow_obj, created = Follow.objects.get_or_create(
        follower=activity.actor,
        target=target,
        defaults={"status": FollowStatus.PENDING},
    )
    if created and not target.manually_approves_followers:
        services.accept_follow(follow_obj)


@handlers.register("Undo")
def handle_undo(activity):
    obj = activity.payload.get("object")
    if isinstance(obj, dict):
        if obj.get("type") != "Follow":
            return
        target_ap_id = obj.get("object")
    else:
        target_ap_id = obj
    target = Actor.objects.filter(ap_id=target_ap_id).first()
    if target is None:
        return
    Follow.objects.filter(follower=activity.actor, target=target).delete()


@handlers.register("Accept")
def handle_accept(activity):
    obj = activity.payload.get("object")
    follows = Follow.objects.filter(
        target=activity.actor, follower__domain="", status=FollowStatus.PENDING
    )
    if isinstance(obj, dict) and obj.get("type") == "Follow" and obj.get("actor"):
        follows = follows.filter(follower__ap_id=obj["actor"])
    for follow_obj in follows:
        follow_obj.status = FollowStatus.ACCEPTED
        follow_obj.save(update_fields=["status", "updated_at"])
