from comics.models import Page, PageStatus


def _ordered(queryset):
    return queryset.filter(status=PageStatus.PUBLISHED).order_by(
        "series__position", "series__id", "position", "id"
    )


def ordered_pages(comic):
    return _ordered(Page.objects.filter(series__comic=comic))


def series_pages(series):
    return _ordered(Page.objects.filter(series=series))


def latest_page(comic):
    return ordered_pages(comic).last()


def neighbors(page):
    pages = list(ordered_pages(page.series.comic))
    index = next(
        (position for position, item in enumerate(pages) if item.pk == page.pk),
        None,
    )
    if index is None:
        return {"first": None, "previous": None, "next": None, "latest": None}
    return {
        "first": pages[0],
        "previous": pages[index - 1] if index > 0 else None,
        "next": pages[index + 1] if index + 1 < len(pages) else None,
        "latest": pages[-1],
    }
