import pytest
from django.db import IntegrityError, transaction

from actors.services import create_local_actor
from federation.models import Activity, ActivityDirection, ActivityStatus


@pytest.mark.django_db
def test_activity_defaults_and_unique_ap_id():
    actor = create_local_actor("alice")
    activity = Activity.objects.create(
        ap_id="https://other.test/activities/1",
        type="Create",
        actor=actor,
        payload={"type": "Create"},
    )
    assert activity.status == ActivityStatus.RECEIVED
    assert activity.direction == ActivityDirection.INBOUND
    with pytest.raises(IntegrityError), transaction.atomic():
        Activity.objects.create(
            ap_id="https://other.test/activities/1",
            type="Create",
            payload={"type": "Create"},
        )
