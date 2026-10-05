import os

from django.db import models

from core.models import UUIDModel


def _original_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    return f"media/{instance.id}/original{ext}"


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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["page", "position"], name="unique_media_position_per_page"
            )
        ]
        ordering = ["page", "position", "id"]

    def __str__(self):
        return f"{self.page} media #{self.position}"
