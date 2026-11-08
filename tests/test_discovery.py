from uuid import uuid4

import pytest
from django.urls import reverse

from comics.models import Comic, ContentRating, Page
from comics.services import create_comic, create_page, create_series
from federation.activitypub import Audience
from media.services import add_media
from moderation import accounts
from moderation.models import ModerationAudit
from tests.media_support import upload
from tests.test_reader import _publish, _user


def _comic(title="Moon Quest", *, published=True, tags=None, rating=None):
    owner = _user()
    comic = create_comic(
        owner,
        f"comic{uuid4().hex[:6]}",
        title,
        tags=tags,
        **({"content_rating": rating} if rating else {}),
    )
    series = create_series(owner, comic, "Main")
    page = create_page(owner, series, title=f"{title} Page")
    if published:
        _publish(page)
    return owner, comic, page


@pytest.mark.django_db
def test_discover_lists_published_visible_comics(client):
    _, listed, _ = _comic("Listed Comic")
    _comic("Unpublished Comic", published=False)
    _, suspended, _ = _comic("Suspended Comic")
    staff = _user(staff=True)
    accounts.apply_account_action(suspended.actor, staff, "suspend", reason="x")

    response = client.get(reverse("discover"))
    assert response.status_code == 200
    assert b"Listed Comic" in response.content
    assert b"Unpublished Comic" not in response.content
    assert b"Suspended Comic" not in response.content


@pytest.mark.django_db
def test_discover_search_covers_title_description_tag_and_handle(client):
    _, comic, _ = _comic("Moon Quest", tags=["Fantasy"])
    Comic.objects.filter(pk=comic.pk).update(description="A silent space opera")
    assert b"Moon Quest" in client.get(reverse("discover"), {"q": "moon"}).content
    assert b"Moon Quest" in client.get(reverse("discover"), {"q": "silent"}).content
    assert b"Moon Quest" in client.get(reverse("discover"), {"q": "fantasy"}).content
    assert (
        b"Moon Quest"
        in client.get(reverse("discover"), {"q": comic.actor.handle}).content
    )
    assert b"Moon Quest" not in client.get(reverse("discover"), {"q": "zzz"}).content


@pytest.mark.django_db
def test_discover_tag_and_rating_filters(client):
    _, all_ages, _ = _comic("All Ages", tags=["Comedy"])
    _, teen, _ = _comic("Teen Comic", tags=["Drama"], rating=ContentRating.TEEN)
    tag = client.get(reverse("discover"), {"tag": "comedy"})
    assert b"All Ages" in tag.content
    assert b"Teen Comic" not in tag.content
    teen_filter = client.get(reverse("discover"), {"rating": "teen"})
    assert b"Teen Comic" in teen_filter.content
    assert b"All Ages" not in teen_filter.content
    both = client.get(reverse("discover"), {"rating": "all"})
    assert b"All Ages" in both.content and b"Teen Comic" in both.content
    assert client.get(reverse("discover"), {"rating": "bad"}).status_code == 400


@pytest.mark.django_db
def test_featured_comics_sort_first_and_toggle_is_staff_only(client):
    _, plain, _ = _comic("Plain Comic")
    _, featured, _ = _comic("Featured Comic")
    staff = _user(staff=True)

    client.force_login(_user())
    denied = client.post(
        reverse("moderation-comic-feature", args=[featured.id]),
        data='{"featured": true}',
        content_type="application/json",
    )
    assert denied.status_code == 403

    client.force_login(staff)
    response = client.post(
        reverse("moderation-comic-feature", args=[featured.id]),
        data='{"featured": true}',
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == {"featured": True}
    featured.refresh_from_db()
    assert featured.featured is True
    assert ModerationAudit.objects.filter(action="comic.featured").exists()

    content = client.get(reverse("discover")).content
    assert content.index(b"Featured Comic") < content.index(b"Plain Comic")
    assert b"Featured" in content

    bad = client.post(
        reverse("moderation-comic-feature", args=[plain.id]),
        data='{"featured": "yes"}',
        content_type="application/json",
    )
    assert bad.status_code == 400


@pytest.mark.django_db
def test_discovery_cover_excludes_members_only_pages(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    owner, comic, page = _comic("Gated Cover", published=False)
    Page.objects.filter(pk=page.pk).update(audience=Audience.MEMBERS)
    _publish(page)
    media = add_media(owner, page, upload(), alt_text="members only")
    response = client.get(reverse("discover"))
    assert b"Gated Cover" in response.content
    assert media.original.url.encode() not in response.content
