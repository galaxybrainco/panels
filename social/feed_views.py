from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from social import feed
from social.models import Notification
from social.notifications import mark_all_read, mark_read


@login_required
def feed_page(request):
    notifications = feed.visible_notifications(request.user)[:5]
    pages = feed.visible_feed_pages(request.user)
    page_obj = Paginator(pages, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "social/feed.html",
        {"notifications": notifications, "page_obj": page_obj},
    )


@login_required
def notifications_page(request):
    rows = feed.visible_notifications(request.user)
    unread = sum(1 for row in rows if not row.read)
    page_obj = Paginator(rows, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "social/notifications.html",
        {"page_obj": page_obj, "unread": unread},
    )


@login_required
@require_POST
def notifications_read_all(request):
    mark_all_read(request.user)
    return redirect("notifications")


@login_required
def notification_open(request, notification_id):
    notification = get_object_or_404(
        Notification, pk=notification_id, recipient=request.user
    )
    mark_read(notification)
    return redirect(feed.notification_target(notification))
