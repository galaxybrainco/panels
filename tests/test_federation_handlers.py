import pytest

from actors.services import create_local_actor
from federation.handlers import HANDLERS, dispatch, register
from federation.models import Activity, ActivityStatus


@pytest.fixture(autouse=True)
def _clean_registry():
    original = dict(HANDLERS)
    yield
    HANDLERS.clear()
    HANDLERS.update(original)


@pytest.mark.django_db
def test_dispatch_runs_registered_handler_and_marks_processed():
    actor = create_local_actor("alice")
    seen = []
    register("Create")(lambda activity: seen.append(activity.ap_id))
    activity = Activity.objects.create(
        ap_id="https://other.test/activities/1",
        type="Create",
        actor=actor,
        payload={"type": "Create"},
    )
    dispatch(activity)
    assert seen == ["https://other.test/activities/1"]
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.PROCESSED


@pytest.mark.django_db
def test_dispatch_without_handler_marks_processed():
    activity = Activity.objects.create(
        ap_id="https://other.test/activities/2", type="Like", payload={"type": "Like"}
    )
    dispatch(activity)
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.PROCESSED
