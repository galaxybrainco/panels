from django.db import models

from core.models import UUIDModel


class FollowStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"


class Follow(UUIDModel):
    follower = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="following_relations"
    )
    target = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="follower_relations"
    )
    status = models.CharField(
        max_length=16, choices=FollowStatus.choices, default=FollowStatus.PENDING
    )
    activity_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "target"], name="unique_follow"
            ),
            models.CheckConstraint(
                condition=~models.Q(follower=models.F("target")),
                name="no_self_follow",
            ),
        ]

    def __str__(self):
        return f"{self.follower} → {self.target} ({self.status})"


class Like(UUIDModel):
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="likes"
    )
    object_id = models.URLField()
    page = models.ForeignKey(
        "comics.Page",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="likes",
    )
    activity_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor", "object_id"], name="unique_like")
        ]

    def __str__(self):
        return f"{self.actor} likes {self.object_id}"


class Boost(UUIDModel):
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="boosts"
    )
    object_id = models.URLField()
    page = models.ForeignKey(
        "comics.Page",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="boosts",
    )
    activity_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor", "object_id"], name="unique_boost")
        ]

    def __str__(self):
        return f"{self.actor} boosted {self.object_id}"
