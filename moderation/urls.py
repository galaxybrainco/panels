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
    path(
        "moderation/blocks/<uuid:actor_id>",
        views.blocks,
        name="moderation-blocks",
    ),
    path("moderation/reports", views.reports_queue, name="moderation-reports"),
    path(
        "moderation/reports/<uuid:report_id>",
        views.report_detail,
        name="moderation-report-detail",
    ),
    path(
        "moderation/reports/<uuid:report_id>/assign",
        views.assign_report,
        name="moderation-report-assign",
    ),
    path(
        "moderation/reports/<uuid:report_id>/resolve",
        views.resolve_report,
        name="moderation-report-resolve",
    ),
    path(
        "moderation/reports/<uuid:report_id>/forward",
        views.forward_report,
        name="moderation-report-forward",
    ),
    path(
        "moderation/reports/<uuid:report_id>/comment-action",
        views.comment_action,
        name="moderation-report-comment-action",
    ),
    path(
        "moderation/actors/<uuid:actor_id>/notes",
        views.actor_notes,
        name="moderation-actor-notes",
    ),
    path("moderation/audit", views.audit_log, name="moderation-audit"),
    path(
        "moderation/accounts/<uuid:actor_id>",
        views.account_detail,
        name="moderation-account-detail",
    ),
    path(
        "moderation/accounts/<uuid:actor_id>/action",
        views.account_action,
        name="moderation-account-action",
    ),
    path(
        "moderation/domains/<str:domain>",
        views.domain_detail,
        name="moderation-domain-detail",
    ),
    path(
        "moderation/domains/<str:domain>/action",
        views.domain_action,
        name="moderation-domain-action",
    ),
]
