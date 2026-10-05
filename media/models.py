import os

from django.db import models

from core.models import UUIDModel


class MediaStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class DerivativeKind(models.TextChoices):
    THUMBNAIL = "thumbnail", "Thumbnail"
    DISPLAY = "display", "Display"
    FEDERATION = "federation", "Federation"


def _original_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    return f"media/{instance.id}/original{ext}"


def _derivative_upload_to(instance, filename):
    return f"media/{instance.media_id}/{instance.kind}.webp"


class Media(UUIDModel):
    page = models.ForeignKey(
        "comics.Page", on_delete=models.CASCADE, related_name="media"
    )
    position = models.PositiveIntegerField(default=0)
    alt_text = models.TextField(blank=True, default="")
    original = models.FileField(upload_to=_original_upload_to)
    content_type = models.CharField(max_length=64)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=MediaStatus.choices, default=MediaStatus.PENDING
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["page", "position"], name="unique_media_position_per_page"
            )
        ]
        ordering = ["page", "position", "id"]

    def __str__(self):
        return f"{self.page} media #{self.position}"


class MediaDerivative(UUIDModel):
    media = models.ForeignKey(
        Media, on_delete=models.CASCADE, related_name="derivatives"
    )
    kind = models.CharField(max_length=16, choices=DerivativeKind.choices)
    file = models.FileField(upload_to=_derivative_upload_to)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    bytes = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["media", "kind"], name="unique_derivative_kind_per_media"
            )
        ]
        ordering = ["media", "kind"]

    def __str__(self):
        return f"{self.media} {self.kind}"
