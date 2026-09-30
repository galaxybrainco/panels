import pytest
import requests
import responses
from django.core.cache import cache

from actors.services import create_local_actor
from federation.models import Delivery, DeliveryStatus
from federation.tasks import backoff_seconds, deliver_activity

INBOX = "https://other.test/inbox"


def _delivery(actor, **kwargs):
    return Delivery.objects.create(
        inbox_url=INBOX, activity={"type": "Create"}, actor=actor, **kwargs
    )


def test_backoff_seconds_grows_and_caps():
    assert backoff_seconds(0) < backoff_seconds(1) < backoff_seconds(2)
    assert backoff_seconds(99) == backoff_seconds(50)


@pytest.mark.django_db
@responses.activate
def test_delivery_succeeds_and_marks_delivered():
    actor = create_local_actor("alice")
    delivery = _delivery(actor)
    responses.add(responses.POST, INBOX, status=202)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DELIVERED
    assert delivery.attempts == 0


@pytest.mark.django_db
@responses.activate
def test_delivery_5xx_schedules_retry():
    actor = create_local_actor("alice")
    delivery = _delivery(actor)
    responses.add(responses.POST, INBOX, status=500)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.attempts == 1
    assert delivery.next_attempt_at is not None


@pytest.mark.django_db
@responses.activate
def test_delivery_dead_letters_after_max_attempts():
    actor = create_local_actor("alice")
    delivery = _delivery(actor, attempts=5, max_attempts=6)
    responses.add(responses.POST, INBOX, status=500)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DEAD
    assert delivery.attempts == 6


@pytest.mark.django_db
@responses.activate
def test_delivery_blocked_instance_is_dead_lettered_without_request():
    from actors.models import Instance

    actor = create_local_actor("alice")
    Instance.objects.create(domain="other.test", blocked=True)
    delivery = _delivery(actor)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DEAD
    assert len(responses.calls) == 0


@pytest.mark.django_db
@responses.activate
def test_delivery_4xx_is_dead_lettered():
    actor = create_local_actor("alice")
    delivery = _delivery(actor)
    responses.add(responses.POST, INBOX, status=422)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DEAD


@pytest.mark.django_db
@responses.activate
def test_delivery_throttled_reschedules_without_request():
    actor = create_local_actor("alice")
    delivery = _delivery(actor)
    cache.clear()
    cache.set("federation:throttle:other.test", 10_000, timeout=60)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.attempts == 0
    assert delivery.next_attempt_at is not None
    assert len(responses.calls) == 0
    cache.clear()


@pytest.mark.django_db
@responses.activate
def test_delivery_network_error_schedules_retry():
    actor = create_local_actor("alice")
    delivery = _delivery(actor)
    responses.add(
        responses.POST, INBOX, body=requests.exceptions.ConnectionError("boom")
    )
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.attempts == 1


@pytest.mark.django_db
@responses.activate
def test_delivery_429_schedules_retry():
    actor = create_local_actor("alice")
    delivery = _delivery(actor)
    responses.add(responses.POST, INBOX, status=429)
    deliver_activity.func(delivery.id)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.attempts == 1


@pytest.mark.django_db
@responses.activate
def test_delivery_is_idempotent_for_finished_rows():
    actor = create_local_actor("alice")
    for status in (DeliveryStatus.DELIVERED, DeliveryStatus.DEAD):
        delivery = _delivery(actor, status=status)
        deliver_activity.func(delivery.id)
        delivery.refresh_from_db()
        assert delivery.status == status
    assert len(responses.calls) == 0
