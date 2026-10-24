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
