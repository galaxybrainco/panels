from actors import portability
from actors.models import Actor
from federation import handlers, remotes


@handlers.register("Move")
def handle_move(activity):
    target = activity.payload.get("target")
    if isinstance(target, dict):
        target = target.get("id")
    if not isinstance(target, str) or not target:
        return
    old = activity.actor
    new = Actor.objects.filter(ap_id=target).first()
    if new is None:
        new = remotes.fetch_remote_actor(target)
    if new is None:
        return
    if old.ap_id not in (new.also_known_as or []):
        return
    old.moved_to = target
    old.save(update_fields=["moved_to", "updated_at"])
    portability.migrate_follows(old, new)
