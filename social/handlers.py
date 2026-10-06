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
        defaults={"status": FollowStatus.PENDING, "activity_id": activity.ap_id},
    )
    if not created and not follow_obj.activity_id:
        follow_obj.activity_id = activity.ap_id
        follow_obj.save(update_fields=["activity_id", "updated_at"])
    if created and not target.manually_approves_followers:
        services.accept_follow(follow_obj)


@handlers.register("Undo")
def handle_undo(activity):
    obj = activity.payload.get("object")
    if isinstance(obj, dict):
        if obj.get("type") != "Follow":
            return
        Follow.objects.filter(
            follower=activity.actor, target__ap_id=obj.get("object")
        ).delete()
    elif isinstance(obj, str):
        Follow.objects.filter(follower=activity.actor, activity_id=obj).delete()


@handlers.register("Accept")
def handle_accept(activity):
    obj = activity.payload.get("object")
    target = activity.actor
    if isinstance(obj, dict) and obj.get("type") == "Follow":
        if obj.get("object") != target.ap_id:
            return
        follows = Follow.objects.filter(
            target=target, follower__domain="", status=FollowStatus.PENDING
        )
        if obj.get("actor"):
            follows = follows.filter(follower__ap_id=obj["actor"])
    elif isinstance(obj, str):
        follows = Follow.objects.filter(
            activity_id=obj, target=target, status=FollowStatus.PENDING
        )
    else:
        return
    for follow_obj in follows:
        follow_obj.status = FollowStatus.ACCEPTED
        follow_obj.save(update_fields=["status", "updated_at"])
