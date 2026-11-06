from pathlib import Path
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from actors.services import create_local_actor
from comics import reading
from comics.models import Page, PageStatus
from comics.services import create_comic, create_page, create_series
from federation.activitypub import Audience
from media.services import add_media
from memberships.models import Provider, Tier
from memberships.services import grant_supporter_status, link_supporter
from moderation import accounts
from tests.media_support import upload


def _user(email=None, staff=False):
    return get_user_model().objects.create_user(
        email=email or f"u-{uuid4().hex[:8]}@example.com",
        password="x",
        is_staff=staff,
    )


def _scene(audience=Audience.PUBLIC):
    owner = _user()
    comic = create_comic(owner, f"comic{uuid4().hex[:6]}", "Lunar Baboon")
    first = create_series(owner, comic, "One", position=1)
    second = create_series(owner, comic, "Two", position=2)
    p1 = create_page(owner, first, title="One-A", audience=audience)
    p2 = create_page(owner, first, title="One-B", audience=audience)
    p3 = create_page(owner, second, title="Two-A", audience=audience)
    return owner, comic, first, second, p1, p2, p3


def _publish(*pages):
    for page in pages:
        Page.objects.filter(pk=page.pk).update(
            status=PageStatus.PUBLISHED,
            published_at=timezone.now(),
            ap_id=f"https://testserver/pages/{page.id}",
        )


def _reader(page):
    comic = page.series.comic
    return reverse("reader-page", args=[comic.slug, page.series.slug, page.position])


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path


@pytest.mark.django_db
def test_reading_order_and_neighbors():
    _, comic, _, _, p1, p2, p3 = _scene()
    _publish(p1, p2, p3)
    assert list(reading.ordered_pages(comic)) == [p1, p2, p3]
    nav = reading.neighbors(p2)
    assert nav["first"] == p1
    assert nav["previous"] == p1
    assert nav["next"] == p3
    assert nav["latest"] == p3
    assert reading.latest_page(comic) == p3


@pytest.mark.django_db
def test_comic_home_lists_series(client):
    _, comic, _, _, p1, _, _ = _scene()
    _publish(p1)
    response = client.get(reverse("comic-home", args=[comic.slug]))
    assert response.status_code == 200
    assert b"Lunar Baboon" in response.content
    assert b"One" in response.content
    assert b"Two" in response.content
    assert b"Read latest" in response.content


@pytest.mark.django_db
def test_archive_lists_published_pages_and_filters_month(client):
    _, comic, first, _, p1, p2, p3 = _scene()
    _publish(p1, p2)
    response = client.get(reverse("series-archive", args=[comic.slug, first.slug]))
    assert response.status_code == 200
    assert b"One-A" in response.content
    assert b"One-B" in response.content
    assert b"Two-A" not in response.content
    old = client.get(
        reverse("series-archive", args=[comic.slug, first.slug]) + "?month=2000-01"
    )
    assert b"No published pages" in old.content
    assert (
        client.get(
            reverse("series-archive", args=[comic.slug, first.slug]) + "?month=nope"
        ).status_code
        == 400
    )


@pytest.mark.django_db
def test_reader_full_page_renders_accessibility_and_prefetch(client):
    owner, comic, _, _, p1, p2, _ = _scene()
    _publish(p1, p2)
    add_media(owner, p1, upload(), alt_text="Panel one")
    add_media(owner, p2, upload(), alt_text="Panel two")
    Page.objects.filter(pk=p1.pk).update(
        transcript="Spoken text", author_commentary="Notes"
    )
    response = client.get(_reader(p1))
    assert response.status_code == 200
    assert b"<html" in response.content
    assert b'id="reader"' in response.content
    assert b"Panel one" in response.content
    assert b"Spoken text" in response.content
    assert b"Notes" in response.content
    assert b'data-next-image="http' in response.content


@pytest.mark.django_db
def test_reader_htmx_returns_partial(client):
    _, _, _, _, p1, p2, _ = _scene()
    _publish(p1, p2)
    response = client.get(_reader(p1), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert b"<html" not in response.content
    assert b'id="reader"' in response.content


@pytest.mark.django_db
def test_reader_navigation_crosses_series_and_latest_redirects(client):
    _, comic, _, _, p1, p2, p3 = _scene()
    _publish(p1, p2, p3)
    response = client.get(_reader(p2))
    assert _reader(p1).encode() in response.content
    assert _reader(p3).encode() in response.content
    latest = client.get(reverse("comic-latest", args=[comic.slug]))
    assert latest.status_code == 302
    assert latest["Location"] == _reader(p3)


@pytest.mark.django_db
def test_drafts_are_not_readable_or_archived(client):
    _, comic, first, _, p1, _, p3 = _scene()
    _publish(p1)
    assert client.get(_reader(p3)).status_code == 404
    response = client.get(reverse("series-archive", args=[comic.slug, first.slug]))
    assert b"One-A" in response.content


@pytest.mark.django_db
def test_member_pages_redirect_then_gate_then_allow_supporters(client):
    owner, comic, _, _, p1, _, _ = _scene(audience=Audience.MEMBERS)
    _publish(p1)
    anonymous = client.get(_reader(p1))
    assert anonymous.status_code == 302
    assert (
        "account_login" in anonymous["Location"]
        or "/accounts/login/" in anonymous["Location"]
    )

    outsider = _user()
    client.force_login(outsider)
    assert client.get(_reader(p1)).status_code == 403

    fan = _user()
    actor = create_local_actor(f"fan{uuid4().hex[:6]}", user=fan)
    tier = Tier.objects.create(comic=comic, name="Fan", rank=1)
    link = link_supporter(actor, comic, Provider.KOFI, fan.email)
    grant_supporter_status(link, tier)
    client.force_login(fan)
    assert client.get(_reader(p1)).status_code == 200


@pytest.mark.django_db
def test_suspended_comic_surface_is_hidden(client):
    _, comic, _, _, p1, _, _ = _scene()
    _publish(p1)
    staff = _user(staff=True)
    accounts.apply_account_action(comic.actor, staff, "suspend", reason="x")
    assert client.get(reverse("comic-home", args=[comic.slug])).status_code == 404
    assert client.get(_reader(p1)).status_code == 404


def test_htmx_asset_is_vendored():
    asset = Path("static/vendor/htmx.min.js")
    assert asset.exists()
    assert asset.stat().st_size > 1000
