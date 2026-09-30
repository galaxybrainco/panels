from datetime import timedelta
from urllib.parse import urlparse

from django.core.cache import cache
from django.tasks import task
from django.utils import timezone

from federation import client, policy
from federation.models import Delivery, DeliveryStatus

BACKOFF_SCHEDULE = [60, 300, 1800, 7200, 43200, 86400]
THROTTLE_LIMIT = 30
THROTTLE_WINDOW = 60


def backoff_seconds(attempt: int) -> int:
    index = min(attempt, len(BACKOFF_SCHEDULE) - 1)
    return BACKOFF_SCHEDULE[index]


def _throttle_allows(inbox_url: str) -> bool:
    domain = urlparse(inbox_url).netloc
    key = f"federation:throttle:{domain}"
    if cache.add(key, 1, timeout=THROTTLE_WINDOW):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=THROTTLE_WINDOW)
        return True
    return count <= THROTTLE_LIMIT


def _dead_letter(delivery, error):
    delivery.status = DeliveryStatus.DEAD
    delivery.last_error = error
    delivery.save(update_fields=["status", "attempts", "last_error", "updated_at"])


def _reschedule(delivery, error):
    delivery.attempts += 1
    delivery.last_error = error
    if delivery.attempts >= delivery.max_attempts:
        _dead_letter(delivery, error)
        return
    delivery.status = DeliveryStatus.FAILED
    delay = backoff_seconds(delivery.attempts - 1)
    delivery.next_attempt_at = timezone.now() + timedelta(seconds=delay)
    delivery.save(
        update_fields=[
            "attempts",
            "status",
            "next_attempt_at",
            "last_error",
            "updated_at",
        ]
    )
    deliver_activity.using(run_after=delivery.next_attempt_at).enqueue(str(delivery.id))


def _reschedule_throttled(delivery):
    delivery.status = DeliveryStatus.FAILED
    delivery.last_error = "rate limited"
    delivery.next_attempt_at = timezone.now() + timedelta(seconds=THROTTLE_WINDOW)
    delivery.save(
        update_fields=["status", "next_attempt_at", "last_error", "updated_at"]
    )
    deliver_activity.using(run_after=delivery.next_attempt_at).enqueue(str(delivery.id))


def _deliver(delivery_id):
    try:
        delivery = Delivery.objects.get(id=delivery_id)
    except Delivery.DoesNotExist:
        return
    if delivery.status in (DeliveryStatus.DELIVERED, DeliveryStatus.DEAD):
        return
    if not policy.delivery_allowed(delivery.activity, delivery.inbox_url):
        _dead_letter(delivery, "blocked by instance policy")
        return
    if not _throttle_allows(delivery.inbox_url):
        _reschedule_throttled(delivery)
        return
    try:
        response = client.post_activity(
            delivery.inbox_url, delivery.activity, delivery.actor
        )
    except Exception as exc:
        _reschedule(delivery, str(exc))
        return
    if 200 <= response.status_code < 300:
        delivery.status = DeliveryStatus.DELIVERED
        delivery.last_error = ""
        delivery.save(update_fields=["status", "last_error", "updated_at"])
    elif response.status_code == 429 or response.status_code >= 500:
        _reschedule(delivery, f"HTTP {response.status_code}")
    else:
        _dead_letter(delivery, f"HTTP {response.status_code}")


@task
def deliver_activity(delivery_id):
    _deliver(delivery_id)
