from urllib.parse import urlsplit

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import Resolver404, resolve, reverse

from comics.models import Page, PageStatus
from feeds.visibility import PUBLIC_AUDIENCES, public_pages, visible_comics
from media.derivative_urls import DerivativeKind, derivative_url


def _page_response(request, page):
    if page.status != PageStatus.PUBLISHED or page.audience not in PUBLIC_AUDIENCES:
        raise Http404
    comic = page.series.comic
    reader_url = request.build_absolute_uri(
        reverse(
            "reader-page",
            args=[comic.slug, page.series.slug, page.position],
        )
    )
    media = next(iter(page.media.all()), None)
    if media is None:
        return {
            "version": "1.0",
            "type": "link",
            "title": page.title or f"Page {page.position}",
            "url": reader_url,
            "provider_name": settings.INSTANCE_NAME,
            "provider_url": settings.INSTANCE_URL,
            "author_name": comic.title,
        }
    return {
        "version": "1.0",
        "type": "photo",
        "title": page.title or f"Page {page.position}",
        "url": derivative_url(media, DerivativeKind.DISPLAY),
        "width": media.width,
        "height": media.height,
        "thumbnail_url": derivative_url(media, DerivativeKind.THUMBNAIL),
        "provider_name": settings.INSTANCE_NAME,
        "provider_url": settings.INSTANCE_URL,
        "author_name": comic.title,
    }


def _comic_response(request, comic):
    page = public_pages(comic).first()
    media = next(iter(page.media.all()), None) if page is not None else None
    data = {
        "version": "1.0",
        "type": "link",
        "title": comic.title,
        "url": request.build_absolute_uri(reverse("comic-home", args=[comic.slug])),
        "provider_name": settings.INSTANCE_NAME,
        "provider_url": settings.INSTANCE_URL,
        "author_name": comic.title,
    }
    if media is not None:
        data["thumbnail_url"] = derivative_url(media, DerivativeKind.THUMBNAIL)
    return data


def oembed(request):
    if request.GET.get("format", "json") != "json":
        return JsonResponse({"error": "unsupported format"}, status=400)
    url = request.GET.get("url")
    if not url:
        return JsonResponse({"error": "url required"}, status=400)
    parsed = urlsplit(url)
    if parsed.netloc and parsed.netloc != settings.INSTANCE_DOMAIN:
        raise Http404
    try:
        match = resolve(parsed.path)
    except Resolver404 as exc:
        raise Http404 from exc
    if match.url_name == "reader-page":
        comic = get_object_or_404(visible_comics(), slug=match.kwargs["slug"])
        page = get_object_or_404(
            Page.objects.select_related("series__comic").prefetch_related("media"),
            series__comic=comic,
            series__slug=match.kwargs["series_slug"],
            position=match.kwargs["position"],
        )
        return JsonResponse(_page_response(request, page))
    if match.url_name == "comic-home":
        comic = get_object_or_404(visible_comics(), slug=match.kwargs["slug"])
        return JsonResponse(_comic_response(request, comic))
    raise Http404
