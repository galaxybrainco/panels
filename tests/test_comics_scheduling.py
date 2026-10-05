from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from comics.models import ComicRole, Page, PageStatus
from comics.publishing import publish, publish_page, schedule_page, unschedule_page
from comics.services import create_comic, create_page, create_series
from comics.tasks import publish_scheduled_page
from tests.media_support import make_ready_media


def _user(email="owner@example.com"):
    return get_user_model().objects.create_user(email=email, password="x")


def _page(owner):
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    make_ready_media(page, position=1, alt_text="A panel")
    return comic, page


@pytest.mark.django_db
def test_schedule_page_sets_state_and_enqueues_delayed_task():
    owner = _user()
    _, page = _page(owner)
    when = timezone.now() + timedelta(hours=2)
    scheduled = schedule_page(owner, page, when)
    assert scheduled.status == PageStatus.SCHEDULED
    assert scheduled.scheduled_for == when
    assert scheduled.scheduled_by == owner
    task = DBTaskResult.objects.get()
    assert task.run_after == when


@pytest.mark.django_db
def test_schedule_rejects_naive_and_past_times():
    owner = _user()
    _, page = _page(owner)
    with pytest.raises(ValidationError) as naive:
        schedule_page(owner, page, timezone.now().replace(tzinfo=None))
    assert "scheduled_for" in naive.value.message_dict
    with pytest.raises(ValidationError) as past:
        schedule_page(owner, page, timezone.now() - timedelta(minutes=1))
    assert "scheduled_for" in past.value.message_dict


@pytest.mark.django_db
def test_schedule_requires_publish_permission_and_gates():
    owner = _user()
    contributor = _user("contributor@example.com")
    comic, page = _page(owner)
    ComicRole.objects.create(
        comic=comic, user=contributor, role=ComicRole.Role.CONTRIBUTOR
    )
    when = timezone.now() + timedelta(hours=1)
    with pytest.raises(PermissionDenied):
        schedule_page(contributor, page, when)
    blank = create_page(owner, page.series)
    with pytest.raises(ValidationError):
        schedule_page(owner, blank, when)


@pytest.mark.django_db
def test_publish_scheduled_page_publishes_due_page_with_scheduler_credit():
    owner = _user()
    _, page = _page(owner)
    schedule_page(owner, page, timezone.now() + timedelta(hours=1))
    Page.objects.filter(pk=page.pk).update(
        scheduled_for=timezone.now() - timedelta(minutes=1)
    )
    publish_scheduled_page.func(str(page.id))
    page.refresh_from_db()
    assert page.status == PageStatus.PUBLISHED
    assert page.published_by == owner
    assert page.scheduled_for is None


@pytest.mark.django_db
def test_publish_scheduled_page_ignores_non_scheduled_pages():
    owner = _user()
    _, page = _page(owner)
    publish_scheduled_page.func(str(page.id))
    page.refresh_from_db()
    assert page.status == PageStatus.DRAFT


@pytest.mark.django_db
def test_publish_scheduled_page_requeues_when_early():
    owner = _user()
    _, page = _page(owner)
    schedule_page(owner, page, timezone.now() + timedelta(hours=3))
    publish_scheduled_page.func(str(page.id))
    page.refresh_from_db()
    assert page.status == PageStatus.SCHEDULED


@pytest.mark.django_db
def test_unschedule_returns_to_draft():
    owner = _user()
    _, page = _page(owner)
    schedule_page(owner, page, timezone.now() + timedelta(hours=1))
    unscheduled = unschedule_page(owner, page)
    assert unscheduled.status == PageStatus.DRAFT
    assert unscheduled.scheduled_for is None
    assert unscheduled.scheduled_by is None


@pytest.mark.django_db
def test_publish_with_require_scheduled_is_noop_for_draft_page():
    owner = _user()
    _, page = _page(owner)
    result = publish(page, require_scheduled=True)
    result.refresh_from_db()
    assert result.status == PageStatus.DRAFT


@pytest.mark.django_db
def test_schedule_rejects_already_published_page():
    owner = _user()
    _, page = _page(owner)
    publish_page(owner, page)
    with pytest.raises(ValidationError):
        schedule_page(owner, page, timezone.now() + timedelta(hours=1))


@pytest.mark.django_db
def test_task_leaves_page_scheduled_when_gates_fail():
    owner = _user()
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series)
    page.status = PageStatus.SCHEDULED
    page.scheduled_for = timezone.now() - timedelta(minutes=1)
    page.scheduled_by = owner
    page.save()

    publish_scheduled_page.func(str(page.id))

    page.refresh_from_db()
    assert page.status == PageStatus.SCHEDULED
