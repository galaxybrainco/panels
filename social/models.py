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
