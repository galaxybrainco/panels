import logging

import requests
from django.conf import settings
from django.tasks import task
from django.urls import reverse

from comics.models import Comic

logger = logging.getLogger(__name__)

TIMEOUT = 10


def feed_urls(comic):
    return [
        settings.INSTANCE_URL + reverse("comic-rss", args=[comic.slug]),
        settings.INSTANCE_URL + reverse("comic-atom", args=[comic.slug]),
    ]


def enqueue_comic(comic):
    if not settings.WEBSUB_HUB_URL:
        return None
    return notify_websub.enqueue(str(comic.id))


@task
def notify_websub(comic_id):
    comic = Comic.objects.filter(pk=comic_id).first()
    if comic is None:
        return
    hub = settings.WEBSUB_HUB_URL
    if not hub:
        return
    data = [("hub.mode", "publish")]
    data.extend(("hub.url", url) for url in feed_urls(comic))
    try:
        response = requests.post(hub, data=data, timeout=TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("WebSub publish failed for %s: %s", comic.slug, exc)
        return
    if response.status_code >= 400:
        logger.warning(
            "WebSub hub rejected %s with HTTP %s", comic.slug, response.status_code
        )
