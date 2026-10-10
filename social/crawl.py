import logging
from urllib.parse import urlparse

from django.core.cache import cache
from django.utils import timezone

from actors.models import Actor
from federation import client, remotes
from social.comments import federatable_page, reply_target, store_inbound_reply
from social.models import Comment

logger = logging.getLogger(__name__)

MAX_DEPTH = 3
MAX_NOTES = 20
CRAWL_LIMIT = 30
CRAWL_WINDOW = 60


def _throttle_allows(url) -> bool:
    domain = urlparse(url).netloc
    key = f"social:crawl:{domain}"
    if cache.add(key, 1, timeout=CRAWL_WINDOW):
        return True
    try:
        return cache.incr(key) <= CRAWL_LIMIT
    except ValueError:
        cache.set(key, 1, timeout=CRAWL_WINDOW)
        return True


def fetch_object(url):
    if not url or not isinstance(url, str) or not _throttle_allows(url):
        return None
    return client.fetch_json(url)


def resolve_author(note):
    attributed = note.get("attributedTo")
    if isinstance(attributed, list):
        attributed = attributed[0] if attributed else None
    if isinstance(attributed, dict):
        attributed = attributed.get("id")
    if not attributed:
        return None
    actor = Actor.objects.filter(ap_id=attributed).first()
    if actor is not None:
        return actor
    return remotes.fetch_remote_actor(attributed)


def store_reply(note):
    actor = resolve_author(note) if isinstance(note, dict) else None
    if actor is None:
        return None
    return store_inbound_reply(note, actor)


def _reply_items(note, budget):
    root = note.get("replies")
    if root is None:
        return []
    items = []
    queue = [root]
    seen = set()
    while queue:
        node = queue.pop(0)
        if isinstance(node, str):
            if node in seen:
                continue
            seen.add(node)
            if not budget.take():
                break
            node = fetch_object(node)
        if not isinstance(node, dict):
            continue
        batch = node.get("orderedItems") or node.get("items")
        if isinstance(batch, list):
            items.extend(batch)
        for key in ("first", "next"):
            link = node.get(key)
            if isinstance(link, dict):
                link = link.get("id")
            if isinstance(link, str) and link not in seen:
                queue.append(link)
    return items


class _Budget:
    def __init__(self, limit=MAX_NOTES):
        self.remaining = limit

    def take(self):
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _crawl_descendants(comment, budget, depth, note=None):
    stored = 0
    if note is None:
        if not budget.take():
            return stored
        note = fetch_object(comment.ap_id)
    if not isinstance(note, dict):
        return stored
    for item in _reply_items(note, budget):
        if not budget.take():
            break
        fetched = None
        if isinstance(item, dict):
            child_note = item
        else:
            child_note = fetch_object(item)
            fetched = child_note
        if not isinstance(child_note, dict):
            continue
        child = store_reply(child_note)
        if child is None:
            continue
        stored += 1
        if depth + 1 < MAX_DEPTH:
            stored += _crawl_descendants(child, budget, depth + 1, note=fetched)
    return stored


def _backfill_ancestors(comment, budget):
    missing = []
    target = comment.in_reply_to
    for _ in range(MAX_DEPTH):
        if not target or Comment.objects.filter(ap_id=target).exists():
            break
        if federatable_page(target) is not None:
            break
        if not budget.take():
            break
        note = fetch_object(target)
        if not isinstance(note, dict):
            break
        missing.append(note)
        target = reply_target(note.get("inReplyTo"))
    stored = 0
    for note in reversed(missing):
        if store_reply(note) is not None:
            stored += 1
    return stored


def crawl_comment(comment):
    budget = _Budget()
    stored = 0
    try:
        stored += _backfill_ancestors(comment, budget)
        stored += _crawl_descendants(comment, budget, depth=1)
    except Exception as exc:
        logger.warning("Thread crawl failed for comment %s: %s", comment.pk, exc)
    finally:
        Comment.objects.filter(pk=comment.pk).update(crawled_at=timezone.now())
    return stored
