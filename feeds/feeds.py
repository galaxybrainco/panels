from django.contrib.syndication.views import Feed
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.feedgenerator import Atom1Feed, Rss201rev2Feed
from django.utils.html import format_html, strip_tags

from feeds.visibility import public_pages, visible_comics
from media.derivative_urls import DerivativeKind, derivative_url


class ComicFeed(Feed):
    def get_object(self, request, slug):
        return get_object_or_404(visible_comics(), slug=slug)

    def title(self, comic):
        return comic.title

    def link(self, comic):
        return reverse("comic-home", args=[comic.slug])

    def description(self, comic):
        return comic.description or f"New pages from {comic.title}"

    def items(self, comic):
        return list(public_pages(comic)[:50])

    def item_title(self, page):
        return page.title or f"Page {page.position}"

    def item_description(self, page):
        media = next(iter(page.media.all()), None)
        if media is not None:
            return format_html(
                '<img src="{}" alt="{}">',
                derivative_url(media, DerivativeKind.DISPLAY),
                media.alt_text,
            )
        return strip_tags(page.transcript or page.author_commentary or "")[:500]

    def item_link(self, page):
        return reverse(
            "reader-page",
            args=[page.series.comic.slug, page.series.slug, page.position],
        )

    def item_pubdate(self, page):
        return page.published_at

    def item_updateddate(self, page):
        return page.updated_at

    def item_categories(self, page):
        return [tag.name for tag in page.series.comic.tags.all()] + [page.series.title]


class ComicRssFeed(ComicFeed):
    feed_type = Rss201rev2Feed


class ComicAtomFeed(ComicFeed):
    feed_type = Atom1Feed
