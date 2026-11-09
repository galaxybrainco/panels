import hashlib
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from django.core.files import File
from django.core.management.base import CommandError
from django.utils.html import strip_tags

from comics.services import create_page
from federation.client import host_allowed
from media.models import Media
from media.services import add_media

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
MAX_IMAGE_BYTES = 25_000_000
MAX_FEED_BYTES = 5_000_000
TIMEOUT = 15
USER_AGENT = "Panels-Import/1.0"
IMG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)', re.IGNORECASE)


def _upload(data, name):
    upload = File(BytesIO(data), name=name)
    upload.content_type = CONTENT_TYPES.get(Path(name).suffix.lower(), "image/png")
    return upload


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _existing_hashes(comic):
    return set(
        Media.objects.filter(page__series__comic=comic).values_list("sha256", flat=True)
    )


def _fetch(url, *, max_bytes):
    if not host_allowed(url):
        raise CommandError(f"Refusing to fetch non-public URL: {url}")
    try:
        response = requests.get(
            url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
    except requests.RequestException as exc:
        raise CommandError(f"Fetch failed for {url}: {exc}") from exc
    if response.status_code != 200:
        raise CommandError(f"Fetch failed for {url}: HTTP {response.status_code}")
    data = response.content
    if len(data) > max_bytes:
        raise CommandError(f"Response too large: {url}")
    return data


def import_directory(owner, comic, series, directory):
    root = Path(directory)
    if not root.is_dir():
        raise CommandError("Directory does not exist")
    files = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not files:
        raise CommandError("No image files found")
    existing = _existing_hashes(comic)
    pages = media = skipped = 0
    for path in files:
        data = path.read_bytes()
        if len(data) > MAX_IMAGE_BYTES:
            skipped += 1
            continue
        digest = _hash(data)
        if digest in existing:
            skipped += 1
            continue
        page = create_page(owner, series, title=strip_tags(path.stem)[:255])
        add_media(owner, page, _upload(data, path.name), alt_text="")
        existing.add(digest)
        pages += 1
        media += 1
    return {"pages": pages, "media": media, "skipped": skipped}


def _entry_images(entry):
    urls = []
    for enclosure in entry.get("enclosures") or []:
        href = enclosure.get("href") or enclosure.get("url")
        media_type = enclosure.get("type") or ""
        if href and media_type.startswith("image/"):
            urls.append(href)
    for item in entry.get("media_content") or []:
        url = item.get("url")
        if url:
            urls.append(url)
    html = entry.get("summary") or ""
    urls.extend(IMG_RE.findall(html))
    seen = set()
    result = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def import_feed(owner, comic, series, feed_url):
    data = _fetch(feed_url, max_bytes=MAX_FEED_BYTES)
    feed = feedparser.parse(data)
    if not feed.entries:
        raise CommandError("Feed has no entries")
    existing = _existing_hashes(comic)
    pages = media = skipped = 0
    for index, entry in enumerate(feed.entries, start=1):
        title = strip_tags(str(entry.get("title") or f"Imported page {index}"))[:255]
        downloads = []
        for image_url in _entry_images(entry):
            absolute = urljoin(entry.get("link") or feed_url, image_url)
            if not host_allowed(absolute):
                skipped += 1
                continue
            try:
                raw = _fetch(absolute, max_bytes=MAX_IMAGE_BYTES)
            except CommandError:
                skipped += 1
                continue
            digest = _hash(raw)
            if digest in existing:
                skipped += 1
                continue
            downloads.append((digest, raw, absolute))
        if not downloads:
            skipped += 1
            continue
        page = create_page(owner, series, title=title)
        for position, (digest, raw, absolute) in enumerate(downloads, start=1):
            suffix = Path(urlparse(absolute).path).suffix or ".jpg"
            add_media(
                owner,
                page,
                _upload(raw, f"import-{index}-{position}{suffix}"),
                alt_text="",
            )
            existing.add(digest)
            media += 1
        pages += 1
    return {"pages": pages, "media": media, "skipped": skipped}
