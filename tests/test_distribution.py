import pytest
from django.urls import reverse

from comics.models import Page
from comics.services import create_page
from federation.activitypub import Audience
from media.services import add_media
from moderation import accounts
from tests.media_support import upload
from tests.test_reader import _publish, _reader, _scene, _user


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path


def _gated_scene():
    owner, comic, first, second, p1, p2, _ = _scene()
    _publish(p1, p2)
    followers = create_page(
        owner, first, title="Followers Page", audience=Audience.FOLLOWERS_ONLY
    )
    members = create_page(owner, first, title="Members Page", audience=Audience.MEMBERS)
    _publish(followers, members)
    return owner, comic, first, p1, p2, followers, members


@pytest.mark.django_db
def test_rss_and_atom_include_public_and_unlisted_only(client):
    _, comic, _, p1, p2, followers, members = _gated_scene()
    Page.objects.filter(pk=p2.pk).update(audience=Audience.UNLISTED)
    for name in ("comic-rss", "comic-atom"):
        response = client.get(reverse(name, args=[comic.slug]))
        assert response.status_code == 200
        content = response.content
        assert b"One-A" in content
        assert b"One-B" in content
        assert b"Followers Page" not in content
        assert b"Members Page" not in content
    rss = client.get(reverse("comic-rss", args=[comic.slug]))
    assert rss["Content-Type"].startswith("application/rss+xml")
    atom = client.get(reverse("comic-atom", args=[comic.slug]))
    assert atom["Content-Type"].startswith("application/atom+xml")


@pytest.mark.django_db
def test_feeds_discovery_links_and_suspended_comics(client):
    _, comic, _, p1, _, _, _ = _gated_scene()
    response = client.get(reverse("comic-home", args=[comic.slug]))
    assert b"application/rss+xml" in response.content
    assert b"application/atom+xml" in response.content
    assert b"application/json+oembed" in response.content
    reader = client.get(_reader(p1))
    assert b"application/rss+xml" in reader.content
    staff = _user(staff=True)
    accounts.apply_account_action(comic.actor, staff, "suspend", reason="x")
    assert client.get(reverse("comic-rss", args=[comic.slug])).status_code == 404
    assert client.get(reverse("comic-atom", args=[comic.slug])).status_code == 404


@pytest.mark.django_db
def test_oembed_photo_link_and_errors(client):
    owner, comic, _, p1, p2, _, _ = _gated_scene()
    media = add_media(owner, p1, upload(), alt_text="Panel")
    page_url = f"http://testserver{_reader(p1)}"
    response = client.get(reverse("oembed"), {"url": page_url, "format": "json"})
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "photo"
    assert data["width"] == media.width
    assert data["height"] == media.height
    assert media.original.url in data["url"]

    comic_url = f"http://testserver{reverse('comic-home', args=[comic.slug])}"
    comic_data = client.get(reverse("oembed"), {"url": comic_url}).json()
    assert comic_data["type"] == "link"
    assert comic_data["title"] == comic.title

    Page.objects.filter(pk=p2.pk).update(audience=Audience.MEMBERS)
    assert (
        client.get(
            reverse("oembed"), {"url": f"http://testserver{_reader(p2)}"}
        ).status_code
        == 404
    )
    assert (
        client.get(reverse("oembed"), {"url": page_url, "format": "xml"}).status_code
        == 400
    )
    assert client.get(reverse("oembed"), {}).status_code == 400
    assert (
        client.get(
            reverse("oembed"), {"url": "https://elsewhere.test/comics/x/"}
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_sitemap_includes_public_content_only(client):
    _, comic, first, p1, p2, followers, _ = _gated_scene()
    Page.objects.filter(pk=p2.pk).update(audience=Audience.UNLISTED)
    _, suspended, _, suspended_page, _, _, _ = _gated_scene()
    staff = _user(staff=True)
    accounts.apply_account_action(suspended.actor, staff, "suspend", reason="x")

    response = client.get(reverse("sitemap"))
    assert response.status_code == 200
    content = response.content
    assert reverse("comic-home", args=[comic.slug]).encode() in content
    assert reverse("series-archive", args=[comic.slug, first.slug]).encode() in content
    assert _reader(p1).encode() in content
    assert _reader(p2).encode() in content
    assert _reader(followers).encode() not in content
    assert reverse("comic-home", args=[suspended.slug]).encode() not in content
    assert _reader(suspended_page).encode() not in content


@pytest.mark.django_db
def test_opengraph_tags(client):
    owner, comic, _, p1, _, _, _ = _gated_scene()
    add_media(owner, p1, upload(), alt_text="Panel")
    home = client.get(reverse("comic-home", args=[comic.slug]))
    assert b'property="og:type" content="website"' in home.content
    assert b'property="og:title"' in home.content
    assert b'property="og:image"' in home.content
    reader = client.get(_reader(p1))
    assert b'property="og:type" content="article"' in reader.content
    assert b'property="og:image"' in reader.content
    assert b'property="article:published_time"' in reader.content
