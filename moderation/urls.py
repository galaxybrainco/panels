from django.urls import path

from moderation import views

urlpatterns = [
    path("moderation/mutes/<uuid:actor_id>", views.mutes, name="moderation-mutes"),
    path(
        "moderation/thread-mutes/<uuid:page_id>",
        views.thread_mutes,
        name="moderation-thread-mutes",
    ),
    path("moderation/filters", views.filters, name="moderation-filters"),
    path(
        "moderation/filters/<uuid:filter_id>",
        views.filter_detail,
        name="moderation-filter-detail",
    ),
    path(
        "moderation/domain-blocks/<str:domain>",
        views.domain_blocks,
        name="moderation-domain-blocks",
    ),
    path(
        "moderation/actors/<uuid:actor_id>/followers/<uuid:follow_id>",
        views.remove_follower,
        name="moderation-remove-follower",
    ),
]
