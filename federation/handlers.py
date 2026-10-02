from collections.abc import Callable

from federation.models import Activity, ActivityStatus

HANDLERS: dict[str, Callable[[Activity], None]] = {}


def register(activity_type: str):
    def decorator(func):
        HANDLERS[activity_type] = func
        return func

    return decorator


def dispatch(activity: Activity) -> Activity:
    handler = HANDLERS.get(activity.type)
    if handler is not None:
        handler(activity)
    activity.status = ActivityStatus.PROCESSED
    activity.save(update_fields=["status", "updated_at"])
    return activity
