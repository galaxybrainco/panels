from urllib.parse import parse_qs

import pytest
import responses
from django.test import override_settings
from django.urls import reverse

from comics.models import PageStatus
from comics.publishing import publish_page
from feeds import websub
from media.services import add_media
from tests.media_support import upload
from tests.test_reader import _publish, _scene


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path


@pytest.mark.django_db
def test_publishing_enqueues_websub_only_when_configured(settings):
    from django_tasks_db.models import DBTaskResult

    owner, comic, _, _, page, _, _ = _scene()
    add_media(owner, page, upload(), alt_text="Panel")
    _publish(page)
    publish_page(owner, page)
    assert not DBTaskResult.objects.filter(task_path__contains="notify_websub").exists()

    settings.WEBSUB_HUB_URL = "https://hub.test/"
    page.status = PageStatus.DRAFT
    page.published_at = None
    page.save(update_fields=["status", "published_at"])
    publish_page(owner, page)
    assert DBTaskResult.objects.filter(task_path__contains="notify_websub").exists()


@responses.activate
@override_settings(WEBSUB_HUB_URL="https://hub.test/")
@pytest.mark.django_db
def test_websub_task_posts_feed_urls():
    _, comic, _, _, _, _, _ = _scene()
    responses.add(responses.POST, "https://hub.test/", status=204)
    websub.notify_websub.func(str(comic.id))
    body = parse_qs(responses.calls[0].request.body)
    assert body["hub.mode"] == ["publish"]
    urls = body["hub.url"]
    assert any(f"/comics/{comic.slug}/rss/" in url for url in urls)
    assert any(f"/comics/{comic.slug}/atom/" in url for url in urls)


@responses.activate
@override_settings(WEBSUB_HUB_URL="https://hub.test/")
@pytest.mark.django_db
def test_websub_task_does_not_raise_on_hub_error():
    _, comic, _, _, _, _, _ = _scene()
    responses.add(responses.POST, "https://hub.test/", status=500)
    websub.notify_websub.func(str(comic.id))


@override_settings(WEBSUB_HUB_URL="https://hub.test/")
@pytest.mark.django_db
def test_comic_pages_advertise_the_hub(client):
    _, comic, _, _, _, _, _ = _scene()
    response = client.get(reverse("comic-home", args=[comic.slug]))
    assert b'rel="hub"' in response.content
    assert b"https://hub.test/" in response.content
