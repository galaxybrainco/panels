from django.contrib.sitemaps.views import sitemap
from django.urls import path

from feeds.feeds import ComicAtomFeed, ComicRssFeed
from feeds.oembed import oembed
from feeds.sitemaps import ComicSitemap, PageSitemap, SeriesSitemap

SITEMAPS = {
    "comics": ComicSitemap,
    "series": SeriesSitemap,
    "pages": PageSitemap,
}

urlpatterns = [
    path("comics/<slug:slug>/rss/", ComicRssFeed(), name="comic-rss"),
    path("comics/<slug:slug>/atom/", ComicAtomFeed(), name="comic-atom"),
    path("oembed", oembed, name="oembed"),
    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap"),
]
