from datetime import datetime
from urllib.parse import quote

from django.core.paginator import Paginator
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from actors.models import Actor
from comics import reading
from comics.models import Comic, Page, PageStatus, Series
from media.derivative_urls import DerivativeKind, derivative_url
from memberships.services import can_view_page, page_requires_tier


def _comic_or_404(slug):
    comic = get_object_or_404(Comic.objects.select_related("actor"), slug=slug)
    from moderation import accounts

    if not accounts.publicly_visible(comic.actor):
        raise Http404
    return comic


def _page_thumb(page):
    media = list(page.media.all())
    if not media:
        return None
    return derivative_url(media[0], DerivativeKind.THUMBNAIL)


def _series_url(comic, series):
    return reverse("series-archive", args=[comic.slug, series.slug])


def _reader_url(page):
    comic = page.series.comic
    return reverse("reader-page", args=[comic.slug, page.series.slug, page.position])


def comic_home(request, slug):
    comic = _comic_or_404(slug)
    cards = []
    for series in comic.series.order_by("position", "id"):
        pages = series.pages.filter(status=PageStatus.PUBLISHED)
        first = pages.prefetch_related("media").first()
        cards.append(
            {
                "series": series,
                "url": _series_url(comic, series),
                "count": pages.count(),
                "thumb": _page_thumb(first) if first is not None else None,
            }
        )
    latest = reading.latest_page(comic)
    return render(
        request,
        "comics/comic_home.html",
        {
            "comic": comic,
            "cards": cards,
            "latest_url": _reader_url(latest) if latest is not None else None,
        },
    )


def series_archive(request, slug, series_slug):
    comic = _comic_or_404(slug)
    series = get_object_or_404(Series, comic=comic, slug=series_slug)
    pages = reading.series_pages(series).prefetch_related("media")
    month = request.GET.get("month")
    if month:
        try:
            parsed = datetime.strptime(month, "%Y-%m")
        except ValueError:
            return HttpResponseBadRequest("Invalid month")
        pages = pages.filter(
            published_at__year=parsed.year, published_at__month=parsed.month
        )
    page_obj = Paginator(pages, 50).get_page(request.GET.get("page"))
    items = [
        {"page": page, "thumb": _page_thumb(page), "url": _reader_url(page)}
        for page in page_obj.object_list
    ]
    return render(
        request,
        "comics/series_archive.html",
        {
            "comic": comic,
            "series": series,
            "items": items,
            "page_obj": page_obj,
            "month": month or "",
        },
    )


def latest(request, slug):
    comic = _comic_or_404(slug)
    page = reading.latest_page(comic)
    if page is None:
        raise Http404
    return redirect(_reader_url(page))


def _access_response(request, page, comic, series):
    required = page_requires_tier(page)
    if required is None:
        return None
    if not request.user.is_authenticated:
        login = reverse("account_login")
        return redirect(f"{login}?next={quote(request.get_full_path(), safe='/')}")
    actor = Actor.objects.filter(user=request.user).first()
    if actor is None or not can_view_page(actor, page):
        return render(
            request,
            "comics/gated.html",
            {
                "comic": comic,
                "series": series,
                "comic_url": reverse("comic-home", args=[comic.slug]),
            },
            status=403,
        )
    return None


def reader(request, slug, series_slug, position):
    comic = _comic_or_404(slug)
    page = get_object_or_404(
        Page.objects.select_related("series").prefetch_related("media"),
        series__comic=comic,
        series__slug=series_slug,
        position=position,
        status=PageStatus.PUBLISHED,
    )
    series = page.series
    guarded = _access_response(request, page, comic, series)
    if guarded is not None:
        return guarded
    nav_pages = reading.neighbors(page)
    nav = {
        key: (_reader_url(value) if value is not None else None)
        for key, value in nav_pages.items()
    }
    next_page = nav_pages["next"]
    next_image = ""
    if next_page is not None:
        next_media = list(next_page.media.all())
        if next_media:
            next_image = derivative_url(next_media[0], DerivativeKind.DISPLAY)
    context = {
        "comic": comic,
        "series": series,
        "page": page,
        "images": [
            {
                "url": derivative_url(media, DerivativeKind.DISPLAY),
                "alt": media.alt_text,
                "width": media.width,
                "height": media.height,
            }
            for media in page.media.all()
        ],
        "nav": nav,
        "next_image": next_image,
    }
    template = (
        "comics/_reader.html"
        if request.headers.get("HX-Request") == "true"
        else "comics/reader.html"
    )
    return render(request, template, context)
