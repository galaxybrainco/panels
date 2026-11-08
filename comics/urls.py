from django.urls import path

from comics import discovery_views, reader_views, views

urlpatterns = [
    path("pages/<uuid:page_id>", views.page_detail, name="page-detail"),
    path("discover", discovery_views.discover, name="discover"),
    path(
        "comics/<slug:slug>/payment-links",
        views.payment_links,
        name="comic-payment-links",
    ),
    path("comics/<slug:slug>/", reader_views.comic_home, name="comic-home"),
    path("comics/<slug:slug>/latest/", reader_views.latest, name="comic-latest"),
    path(
        "comics/<slug:slug>/<slug:series_slug>/",
        reader_views.series_archive,
        name="series-archive",
    ),
    path(
        "comics/<slug:slug>/<slug:series_slug>/<int:position>/",
        reader_views.reader,
        name="reader-page",
    ),
]
