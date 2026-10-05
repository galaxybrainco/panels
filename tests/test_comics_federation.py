import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from comics.federation import (
    emit_page_activity,
    federation_plan,
    page_to_note,
)
from comics.models import FederationMode
from comics.services import create_comic, create_page, create_series
from federation.activitypub import PUBLIC, Audience
from federation.models import Activity, ActivityDirection, ActivityStatus
from tests.media_support import make_media


def _scene(**page_fields):
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="x"
    )
    comic = create_comic(owner, "lunarbaboon", "Lunar Baboon")
    series = create_series(owner, comic, "Main Story")
    page = create_page(owner, series, **page_fields)
    make_media(page, position=1, alt_text="A panel")
    return owner, comic, series, page


def _published(page):
    page.ap_id = f"http://testserver/pages/{page.id}"
    page.published_at = timezone.now()
    page.save()
    return page


@pytest.mark.django_db
def test_plan_public_federated():
    _, comic, _, page = _scene()
    plan = federation_plan(page)
    assert plan.emit is True
    assert PUBLIC in plan.to
    assert comic.actor.followers in plan.cc


@pytest.mark.django_db
def test_plan_unlisted():
    _, comic, _, page = _scene(audience=Audience.UNLISTED)
    plan = federation_plan(page)
    assert plan.emit is True
    assert comic.actor.followers in plan.to
    assert PUBLIC in plan.cc


@pytest.mark.django_db
def test_plan_followers_only():
    _, comic, _, page = _scene(audience=Audience.FOLLOWERS_ONLY)
    plan = federation_plan(page)
    assert plan.emit is True
    assert plan.to == [comic.actor.followers]
    assert plan.cc == []


@pytest.mark.django_db
@pytest.mark.parametrize("audience", [Audience.MEMBERS, Audience.TIER])
def test_plan_members_and_tier_do_not_emit(audience):
    _, _, _, page = _scene(audience=audience)
    assert federation_plan(page).emit is False


@pytest.mark.django_db
def test_plan_local_only_does_not_emit():
    _, _, _, page = _scene(federation=FederationMode.LOCAL_ONLY)
    assert federation_plan(page).emit is False


@pytest.mark.django_db
def test_page_to_note_shape():
    _, comic, _, page = _scene(title="Chapter One")
    page = _published(page)
    note = page_to_note(page)
    assert note["type"] == "Note"
    assert note["id"] == page.ap_id
    assert note["url"] == page.ap_id
    assert note["attributedTo"] == comic.actor.ap_id
    assert note["published"] == page.published_at.isoformat()
    assert "Chapter One" in note["content"]
    assert "Read on Panels" in note["content"]
    assert len(note["attachment"]) == 1
    assert note["attachment"][0]["name"] == "A panel"


@pytest.mark.django_db
def test_note_sensitive_and_content_warning():
    _, _, _, page = _scene(sensitive=True, content_warning="Flashing")
    page = _published(page)
    note = page_to_note(page)
    assert note["sensitive"] is True
    assert note["summary"] == "Flashing"


@pytest.mark.django_db
@override_settings(
    MEDIA_DERIVATIVE_BACKEND="media.derivative_urls.BunnyOptimizerBackend",
    MEDIA_CDN_BASE_URL="https://cdn.test",
)
def test_note_attachment_uses_federation_derivative():
    _, _, _, page = _scene()
    page = _published(page)
    attachment = page_to_note(page)["attachment"][0]
    assert attachment["type"] == "Document"
    assert attachment["mediaType"] == "image/png"
    assert attachment["url"].startswith("https://cdn.test/")
    assert "format=jpeg" in attachment["url"]


@pytest.mark.django_db
def test_emit_page_activity_records_outbound_activity():
    _, comic, _, page = _scene()
    page = _published(page)
    activity = emit_page_activity(page, "Create")
    assert activity.direction == ActivityDirection.OUTBOUND
    assert activity.status == ActivityStatus.PROCESSED
    assert activity.type == "Create"
    assert activity.actor == comic.actor
    assert activity.payload["actor"] == comic.actor.ap_id
    assert activity.payload["object"]["type"] == "Note"


@pytest.mark.django_db
def test_emit_page_activity_is_noop_when_not_eligible():
    _, _, _, page = _scene(audience=Audience.MEMBERS)
    assert emit_page_activity(page, "Create") is None
    assert Activity.objects.count() == 0


@pytest.mark.django_db
def test_emit_delete_uses_object_id():
    _, _, _, page = _scene()
    page = _published(page)
    activity = emit_page_activity(page, "Delete")
    assert activity.payload["object"] == page.ap_id
