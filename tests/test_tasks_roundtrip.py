import pytest
from django.core.management import call_command
from django.tasks import TaskResultStatus


@pytest.mark.django_db(transaction=True)
def test_task_roundtrip_through_db_worker():
    from config.tasks import ping

    result = ping.enqueue()
    assert result.status == TaskResultStatus.READY

    call_command(
        "db_worker",
        batch=True,
        max_tasks=1,
        startup_delay=False,
        reload=False,
        verbosity=0,
    )

    result.refresh()
    assert result.status == TaskResultStatus.SUCCESSFUL
    assert result.return_value == "pong"
