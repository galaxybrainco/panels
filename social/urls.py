from django.urls import path

from social import feed_views, views

urlpatterns = [
    path("comments/<uuid:comment_id>", views.comment_detail, name="comment-detail"),
    path("feed", feed_views.feed_page, name="feed"),
    path("notifications", feed_views.notifications_page, name="notifications"),
    path(
        "notifications/read-all",
        feed_views.notifications_read_all,
        name="notifications-read-all",
    ),
    path(
        "notifications/<uuid:notification_id>",
        feed_views.notification_open,
        name="notification-open",
    ),
]
