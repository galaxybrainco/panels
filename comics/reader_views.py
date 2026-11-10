import hashlib
from datetime import datetime
from urllib.parse import quote

from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.vary import vary_on_headers

from actors.models import Actor
from comics import permissions, reading
from comics.models import Comic, Page, PageStatus, PageView, Series
from media.derivative_urls import DerivativeKind, derivative_url
from memberships.services import can_view_page, page_requires_tier
from social.models import ReadingProgress
from social.progress import record_progress, resume_point


def _comic_or_404(slug):
    comic = get_object_or_404(Comic.objects.select_related("actor"), slug=slug)
    from moderation import accounts

    if not accounts.publicly_visible(comic.actor):
        raise Http404
    return comic


def _record_view(request, page):
    if not settings.PAGE_VIEW_ANALYTICS_ENABLED:
        return
    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key
    digest = hashlib.sha256((session_key or "").encode("utf-8")).hexdigest()
    PageView.objects.create(
        page=page,
        user=request.user if request.user.is_authenticated else None,
        session_hash=digest,
    )


def _viewer_actor(request):
    if not request.user.is_authenticated:
        return None
    return Actor.objects.filter(user=request.user).first()


def _can_read(page, actor):
    required = page_requires_tier(page)
    if required is None:
        return True
    return actor is not None and can_view_page(actor, page)


def _page_thumb(page):
    media = next(iter(page.media.all()), None)
    if media is None:
        return None
    return derivative_url(media, DerivativeKind.THUMBNAIL)


def _resume_pages(user, comic):
    if not user.is_authenticated:
        return {}
    rows = ReadingProgress.objects.filter(
        user=user, series__comic=comic
    ).select_related("page", "page__series")
    return {row.series_id: row.page for row in rows if row.page is not None}


def _resume_card(page, actor):
    if (
        page is None
        or page.status != PageStatus.PUBLISHED
        or not _can_read(page, actor)
    ):
        return None
    return {
        "url": _reader_url(page),
        "label": page.title or f"Page {page.position}",
    }


def _series_url(comic, series):
    return reverse("series-archive", args=[comic.slug, series.slug])


def _reader_url(page):
    comic = page.series.comic
    return reverse("reader-page", args=[comic.slug, page.series.slug, page.position])


@vary_on_headers("HX-Request")
def comic_home(request, slug):
    comic = _comic_or_404(slug)
    actor = _viewer_actor(request)
    resume_pages = _resume_pages(request.user, comic)
    cards = []
    for series in comic.series.order_by("position", "id"):
        pages = list(
            series.pages.filter(status=PageStatus.PUBLISHED).prefetch_related("media")
        )
        accessible = [page for page in pages if _can_read(page, actor)]
        first = next((page for page in accessible if page.media.all()), None)
        resume = _resume_card(resume_pages.get(series.id), actor)
        cards.append(
            {
                "series": series,
                "url": _series_url(comic, series),
                "count": len(accessible),
                "thumb": _page_thumb(first) if first is not None else None,
                "resume_url": resume["url"] if resume else None,
                "resume_label": resume["label"] if resume else None,
            }
        )
    latest = reading.latest_page(comic)
    latest_url = None
    if latest is not None and _can_read(latest, actor):
        latest_url = _reader_url(latest)
    return render(
        request,
        "comics/comic_home.html",
        {
            "comic": comic,
            "cards": cards,
            "latest_url": latest_url,
            "show_dashboard": permissions.can_edit(request.user, comic)
            or permissions.can_moderate(request.user, comic),
        },
    )


@vary_on_headers("HX-Request")
def series_archive(request, slug, series_slug):
    comic = _comic_or_404(slug)
    series = get_object_or_404(Series, comic=comic, slug=series_slug)
    actor = _viewer_actor(request)
    pages = list(
        reading.series_pages(series)
        .select_related("series__comic")
        .prefetch_related("media")
    )
    if request.GET.get("month"):
        try:
            parsed = datetime.strptime(request.GET["month"], "%Y-%m")
        except ValueError:
            return HttpResponseBadRequest("Invalid month")
        pages = [
            page
            for page in pages
            if page.published_at is not None
            and page.published_at.year == parsed.year
            and page.published_at.month == parsed.month
        ]
    pages = [page for page in pages if _can_read(page, actor)]
    current_page = None
    if request.user.is_authenticated:
        row = resume_point(request.user, series)
        if row is not None:
            current_page = row.page
    resume = _resume_card(current_page, actor)
    page_obj = Paginator(pages, 50).get_page(request.GET.get("page"))
    items = [
        {
            "page": page,
            "thumb": _page_thumb(page),
            "url": _reader_url(page),
            "current": current_page is not None and current_page.pk == page.pk,
        }
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
            "month": request.GET.get("month", ""),
            "resume_url": resume["url"] if resume else None,
            "resume_label": resume["label"] if resume else None,
        },
    )


def latest(request, slug):
    comic = _comic_or_404(slug)
    page = reading.latest_page(comic)
    if page is None or not _can_read(page, _viewer_actor(request)):
        raise Http404
    return redirect(_reader_url(page))


def _login_redirect(request):
    url = f"{reverse('account_login')}?next={quote(request.get_full_path(), safe='/')}"
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=200)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


def _gated(request, page, comic, series):
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=200)
        response["HX-Redirect"] = request.get_full_path()
        return response
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


def _access_response(request, page, comic, series, actor):
    required = page_requires_tier(page)
    if required is None:
        return None
    if not request.user.is_authenticated:
        return _login_redirect(request)
    if not _can_read(page, actor):
        return _gated(request, page, comic, series)
    return None


@vary_on_headers("HX-Request")
def reader(request, slug, series_slug, position):
    comic = _comic_or_404(slug)
    page = get_object_or_404(
        Page.objects.select_related("series__comic").prefetch_related("media"),
        series__comic=comic,
        series__slug=series_slug,
        position=position,
        status=PageStatus.PUBLISHED,
    )
    series = page.series
    actor = _viewer_actor(request)
    guarded = _access_response(request, page, comic, series, actor)
    if guarded is not None:
        return guarded
    if request.user.is_authenticated:
        record_progress(request.user, page)
    _record_view(request, page)
    nav_pages = reading.neighbors(page)
    nav = {
        key: (_reader_url(value) if value is not None else None)
        for key, value in nav_pages.items()
    }
    next_page = nav_pages["next"]
    next_image = ""
    if next_page is not None and _can_read(next_page, actor):
        next_media = next(iter(next_page.media.all()), None)
        if next_media is not None:
            next_image = derivative_url(next_media, DerivativeKind.DISPLAY)
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
