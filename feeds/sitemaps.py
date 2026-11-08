from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from feeds.visibility import public_page_qs, public_pages, public_series, visible_comics


class ComicSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.8

    def items(self):
        return (
            visible_comics()
            .filter(series__pages__in=public_page_qs())
            .distinct()
            .order_by("title", "id")
        )

    def location(self, comic):
        return reverse("comic-home", args=[comic.slug])

    def lastmod(self, comic):
        page = public_pages(comic).first()
        return page.published_at if page is not None else None


class SeriesSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.6

    def items(self):
        return public_series()

    def location(self, series):
        return reverse("series-archive", args=[series.comic.slug, series.slug])

    def lastmod(self, series):
        page = public_pages(series.comic).filter(series=series).first()
        return page.published_at if page is not None else None


class PageSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.9

    def items(self):
        return list(public_page_qs())

    def location(self, page):
        return reverse(
            "reader-page",
            args=[page.series.comic.slug, page.series.slug, page.position],
        )

    def lastmod(self, page):
        return page.published_at
