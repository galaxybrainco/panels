import calendar
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from billing.models import ComicPlan
from comics import permissions
from comics.models import Comic, Page, PageStatus, QuoteRequest, QuoteStatus
from media.quota import comic_storage_bytes, comic_storage_quota
from memberships.models import SupporterStatus
from moderation.models import Report, ReportStatus
from social.models import Follow, FollowStatus


def _month(request):
    raw = request.GET.get("month")
    if not raw:
        today = timezone.localdate()
        return today.year, today.month
    try:
        parsed = datetime.strptime(raw, "%Y-%m")
    except ValueError as exc:
        raise ValueError from exc
    return parsed.year, parsed.month


def _grid(year, month):
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        days = []
        for day in week:
            days.append({"date": day, "in_month": day.month == month, "items": []})
        weeks.append(days)
    return weeks


def _schedule(comic, year, month, weeks):
    pages = (
        Page.objects.filter(series__comic=comic)
        .select_related("series")
        .filter(
            Q(scheduled_for__year=year, scheduled_for__month=month)
            | Q(published_at__year=year, published_at__month=month)
        )
    )
    by_date = {}
    for page in pages:
        when = page.published_at
        if page.status == PageStatus.SCHEDULED and page.scheduled_for is not None:
            when = page.scheduled_for
        if when is None:
            continue
        by_date.setdefault(when.date(), []).append(
            {"page": page, "scheduled": page.status == PageStatus.SCHEDULED}
        )
    for week in weeks:
        for day in week:
            day["items"] = by_date.get(day["date"], [])


@login_required
def dashboard(request, slug):
    comic = get_object_or_404(Comic.objects.select_related("actor"), slug=slug)
    show_editor = permissions.can_edit(request.user, comic)
    show_moderation = permissions.can_moderate(request.user, comic)
    show_owner = permissions.is_owner(request.user, comic)
    if not (show_editor or show_moderation):
        raise PermissionDenied
    try:
        year, month = _month(request)
    except ValueError:
        return HttpResponseBadRequest("Invalid month")
    weeks = _grid(year, month)
    prev_year, prev_month = (year, month - 1) if month > 1 else (year - 1, 12)
    next_year, next_month = (year, month + 1) if month < 12 else (year + 1, 1)
    if show_editor:
        _schedule(comic, year, month, weeks)

    follows = Follow.objects.filter(target=comic.actor, status=FollowStatus.ACCEPTED)
    pages = Page.objects.filter(series__comic=comic)
    counts = {
        status: pages.filter(status=status).count()
        for status in (
            PageStatus.DRAFT,
            PageStatus.SCHEDULED,
            PageStatus.PUBLISHED,
        )
    }
    context = {
        "comic": comic,
        "show_editor": show_editor,
        "show_moderation": show_moderation,
        "show_owner": show_owner,
        "year": year,
        "month": month,
        "month_label": f"{calendar.month_name[month]} {year}",
        "prev_month": f"{prev_year:04d}-{prev_month:02d}",
        "next_month": f"{next_year:04d}-{next_month:02d}",
        "weeks": weeks,
        "upcoming": Page.objects.filter(
            series__comic=comic,
            status=PageStatus.SCHEDULED,
            scheduled_for__gte=timezone.now(),
        ).order_by("scheduled_for")[:10],
        "recent_pages": pages.select_related("series").order_by("-created_at")[:12],
        "series_count": comic.series.count(),
        "counts": counts,
        "local_followers": follows.filter(follower__domain="").count(),
        "remote_followers": follows.filter(follower__domain__gt="").count(),
        "reports": [],
        "plan": None,
        "used_bytes": comic_storage_bytes(comic),
        "quota_bytes": comic_storage_quota(comic),
        "supporters": [],
        "supporter_count": 0,
        "pending_quotes": 0,
    }
    if show_moderation:
        context["reports"] = (
            Report.objects.filter(
                Q(target_page__series__comic=comic)
                | Q(target_comment__page__series__comic=comic)
                | Q(target_actor__comic=comic),
                status__in=[ReportStatus.OPEN, ReportStatus.ASSIGNED],
            )
            .select_related(
                "target_page",
                "target_comment",
                "target_actor",
                "reporter_user",
                "reporter_actor",
            )
            .order_by("-created_at")[:10]
        )
    if show_owner:
        context["plan"] = ComicPlan.objects.filter(comic=comic).first()
        active = (
            SupporterStatus.objects.filter(link__comic=comic, active=True)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=timezone.now()))
            .select_related("link__actor", "link", "tier")
            .order_by("tier__rank", "created_at")
        )
        context["supporters"] = active[:50]
        context["supporter_count"] = active.values("link_id").distinct().count()
        context["pending_quotes"] = QuoteRequest.objects.filter(
            page__series__comic=comic, status=QuoteStatus.PENDING
        ).count()
    return render(request, "comics/dashboard.html", context)
