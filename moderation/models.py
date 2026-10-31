from django.conf import settings
from django.db import models

from core.models import UUIDModel


class Mute(UUIDModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mutes"
    )
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="muted_by"
    )
    hide_notifications = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "actor"], name="unique_mute")
        ]


class ThreadMute(UUIDModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="thread_mutes"
    )
    page = models.ForeignKey(
        "comics.Page", on_delete=models.CASCADE, related_name="thread_mutes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "page"], name="unique_thread_mute")
        ]


class KeywordFilter(UUIDModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="keyword_filters",
    )
    phrase = models.CharField(max_length=255)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "phrase"], name="unique_user_phrase"
            )
        ]


class DomainBlock(UUIDModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="domain_blocks"
    )
    domain = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "domain"], name="unique_user_domain"
            )
        ]


class Block(UUIDModel):
    blocker = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="blocks_made"
    )
    blocked = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="blocked_by"
    )
    activity_id = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["blocker", "blocked"], name="unique_block"),
            models.CheckConstraint(
                condition=~models.Q(blocker=models.F("blocked")),
                name="no_self_block",
            ),
        ]


class ReportCategory(models.TextChoices):
    SPAM = "spam", "Spam"
    ABUSE = "abuse", "Abuse"
    ILLEGAL = "illegal", "Illegal"
    OTHER = "other", "Other"


class ReportStatus(models.TextChoices):
    OPEN = "open", "Open"
    ASSIGNED = "assigned", "Assigned"
    RESOLVED = "resolved", "Resolved"
    REJECTED = "rejected", "Rejected"


class Report(UUIDModel):
    target_actor = models.ForeignKey(
        "actors.Actor",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports_about",
    )
    target_page = models.ForeignKey(
        "comics.Page",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports_about",
    )
    target_comment = models.ForeignKey(
        "social.Comment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports_about",
    )
    reporter_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports_made",
    )
    reporter_actor = models.ForeignKey(
        "actors.Actor",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports_sent",
    )
    reason = models.TextField()
    category = models.CharField(
        max_length=16, choices=ReportCategory.choices, default=ReportCategory.OTHER
    )
    status = models.CharField(
        max_length=16, choices=ReportStatus.choices, default=ReportStatus.OPEN
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_reports",
    )
    resolution = models.TextField(blank=True, default="")
    forwarded_at = models.DateTimeField(null=True, blank=True)
    remote_activity_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        target_actor__isnull=False,
                        target_page__isnull=True,
                        target_comment__isnull=True,
                    )
                    | models.Q(
                        target_actor__isnull=True,
                        target_page__isnull=False,
                        target_comment__isnull=True,
                    )
                    | models.Q(
                        target_actor__isnull=True,
                        target_page__isnull=True,
                        target_comment__isnull=False,
                    )
                ),
                name="report_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reporter_user__isnull=False, reporter_actor__isnull=True)
                    | models.Q(reporter_user__isnull=True, reporter_actor__isnull=False)
                ),
                name="report_exactly_one_reporter",
            ),
            models.UniqueConstraint(
                fields=["remote_activity_id"],
                condition=~models.Q(remote_activity_id=""),
                name="unique_remote_report_activity",
            ),
        ]


class ModerationNote(UUIDModel):
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="moderation_notes",
    )
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="moderation_notes"
    )
    body = models.TextField()

    class Meta:
        ordering = ["-created_at"]


class ModerationAudit(UUIDModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderation_audit",
    )
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=255, blank=True, default="")
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class AccountState(models.TextChoices):
    NORMAL = "normal", "Normal"
    LIMITED = "limited", "Limited"
    SUSPENDED = "suspended", "Suspended"
    FROZEN = "frozen", "Frozen"
    DISABLED = "disabled", "Disabled"


class AccountModeration(UUIDModel):
    actor = models.OneToOneField(
        "actors.Actor", on_delete=models.CASCADE, related_name="moderation"
    )
    state = models.CharField(
        max_length=16, choices=AccountState.choices, default=AccountState.NORMAL
    )
    force_sensitive = models.BooleanField(default=False)
    reason = models.TextField(blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="account_moderation_changes",
    )


class BlockedEmailDomain(UUIDModel):
    domain = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class BlockedIP(UUIDModel):
    address = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
