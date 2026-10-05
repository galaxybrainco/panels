from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from comics.models import Page, PageStatus
from comics.publishing import schedule_page
from comics.services import create_comic, create_page, create_series
from tests.media_support import make_media


def _user(email="owner@example.com"):
    return get_user_model().objects.create_user(email=email, password="x")


def _due_page(owner, series, *, with_media=True):
    page = create_page(owner, series)
    if with_media:
        make_media(page, position=1, alt_text="A panel")
    page.status = PageStatus.SCHEDULED
    page.scheduled_for = timezone.now() - timedelta(minutes=5)
    page.scheduled_by = owner
    page.save()
    return page


@pytest.mark.django_db
def test_sweep_publishes_only_due_pages():
    owner = _user()
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    due = _due_page(owner, series)
    future = create_page(owner, series)
    make_media(future, position=1, alt_text="Another panel")
    schedule_page(owner, future, timezone.now() + timedelta(hours=1))

    call_command("publish_due_pages")

    due.refresh_from_db()
    future.refresh_from_db()
    assert due.status == PageStatus.PUBLISHED
    assert due.published_by == owner
    assert future.status == PageStatus.SCHEDULED


@pytest.mark.django_db
def test_sweep_skips_pages_that_fail_gates():
    owner = _user()
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    broken = _due_page(owner, series, with_media=False)
    good = _due_page(owner, series)

    call_command("publish_due_pages")

    broken.refresh_from_db()
    good.refresh_from_db()
    assert broken.status == PageStatus.SCHEDULED
    assert good.status == PageStatus.PUBLISHED


@pytest.mark.django_db
def test_sweep_is_idempotent():
    owner = _user()
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    _due_page(owner, series)

    call_command("publish_due_pages")
    first_stamp = Page.objects.get().published_at
    call_command("publish_due_pages")

    page = Page.objects.get()
    assert page.status == PageStatus.PUBLISHED
    assert page.published_at == first_stamp
