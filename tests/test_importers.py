from uuid import uuid4

import pytest
import responses
from django.core.management import call_command
from django.core.management.base import CommandError

from comics.models import Comic, Page, PageStatus
from comics.services import create_comic
from media.models import Media
from tests.media_support import upload
from tests.test_reader import _user


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path


def _png():
    return upload().read()


def _feed_xml():
    return b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>Episode 1</title><link>https://source.test/e1</link>
<enclosure url="https://images.test/1.png" type="image/png"/></item>
<item><title>Episode 2</title><link>https://source.test/e2</link>
<description><![CDATA[<p><img src="https://images.test/2.png"></p>]]></description></item>
</channel></rss>"""


@pytest.mark.django_db
def test_directory_import_creates_drafts_with_empty_alt(tmp_path):
    owner = _user()
    source = tmp_path / "source"
    source.mkdir()
    (source / "page-one.png").write_bytes(_png())
    (source / "page-two.png").write_bytes(_png() * 2)
    (source / "notes.txt").write_text("ignore")
    call_command(
        "import_comic",
        owner=owner.email,
        handle=f"import{uuid4().hex[:6]}",
        title="Imported Comic",
        directory=str(source),
    )
    comic = Comic.objects.get(title="Imported Comic")
    pages = Page.objects.filter(series__comic=comic).order_by("position")
    assert pages.count() == 2
    assert all(page.status == PageStatus.DRAFT for page in pages)
    assert [page.title for page in pages] == ["page-one", "page-two"]
    assert all(media.alt_text == "" for media in Media.objects.all())
    assert Media.objects.count() == 2


@pytest.mark.django_db
def test_directory_import_into_existing_comic(tmp_path):
    owner = _user()
    comic = create_comic(owner, f"existing{uuid4().hex[:6]}", "Existing")
    source = tmp_path / "source"
    source.mkdir()
    (source / "panel.png").write_bytes(_png())
    call_command(
        "import_comic",
        owner=owner.email,
        comic=comic.slug,
        series="Bonus",
        directory=str(source),
    )
    assert Page.objects.filter(series__comic=comic, series__slug="bonus").count() == 1


@pytest.mark.django_db
def test_directory_import_requires_images_and_owner(tmp_path):
    owner = _user()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CommandError):
        call_command(
            "import_comic",
            owner=owner.email,
            handle=f"import{uuid4().hex[:6]}",
            title="Empty",
            directory=str(empty),
        )
    with pytest.raises(CommandError):
        call_command(
            "import_comic",
            owner="missing@example.com",
            handle=f"import{uuid4().hex[:6]}",
            title="Missing",
            directory=str(empty),
        )


@responses.activate
@pytest.mark.django_db
def test_feed_import_downloads_images_and_dedupes(monkeypatch):
    monkeypatch.setattr("comics.importers.host_allowed", lambda url: True)
    owner = _user()
    image = _png()
    responses.add(
        responses.GET,
        "https://source.test/feed.xml",
        body=_feed_xml(),
        content_type="application/rss+xml",
    )
    responses.add(
        responses.GET,
        "https://images.test/1.png",
        body=image,
        content_type="image/png",
    )
    responses.add(
        responses.GET,
        "https://images.test/2.png",
        body=image + b"x",
        content_type="image/png",
    )
    options = {
        "owner": owner.email,
        "handle": f"feed{uuid4().hex[:6]}",
        "title": "Feed Comic",
        "feed": "https://source.test/feed.xml",
    }
    call_command("import_comic", **options)
    comic = Comic.objects.get(title="Feed Comic")
    assert Page.objects.filter(series__comic=comic).count() == 2
    assert Media.objects.filter(page__series__comic=comic).count() == 2
    assert [
        page.title
        for page in Page.objects.filter(series__comic=comic).order_by("position")
    ] == [
        "Episode 1",
        "Episode 2",
    ]

    responses.reset()
    responses.add(
        responses.GET,
        "https://source.test/feed.xml",
        body=_feed_xml(),
        content_type="application/rss+xml",
    )
    responses.add(
        responses.GET,
        "https://images.test/1.png",
        body=image,
        content_type="image/png",
    )
    responses.add(
        responses.GET,
        "https://images.test/2.png",
        body=image + b"x",
        content_type="image/png",
    )
    call_command("import_comic", **options, comic=comic.slug)
    assert Page.objects.filter(series__comic=comic).count() == 2


@responses.activate
@pytest.mark.django_db
def test_feed_import_rejects_http_errors(monkeypatch):
    monkeypatch.setattr("comics.importers.host_allowed", lambda url: True)
    owner = _user()
    responses.add(responses.GET, "https://source.test/feed.xml", status=404)
    with pytest.raises(CommandError):
        call_command(
            "import_comic",
            owner=owner.email,
            handle=f"feed{uuid4().hex[:6]}",
            title="Bad Feed",
            feed="https://source.test/feed.xml",
        )
