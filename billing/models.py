from django.conf import settings
from django.db import models

from core.models import UUIDModel


class PlanStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    FROZEN = "frozen", "Frozen"
    ARCHIVED = "archived", "Archived"


class ComicPlan(UUIDModel):
    """A creator's paid Panels plan for a comic (hosting + storage quota).

    This is the platform subscription, not a reader's membership in the comic.
    """

    comic = models.OneToOneField(
        "comics.Comic", on_delete=models.CASCADE, related_name="plan"
    )
    status = models.CharField(
        max_length=16, choices=PlanStatus.choices, default=PlanStatus.ACTIVE
    )
    quota_bytes = models.PositiveBigIntegerField()
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    stripe_addon_item_id = models.CharField(max_length=255, blank=True, default="")
    current_period_end = models.DateTimeField(null=True, blank=True)
    grace_until = models.DateTimeField(null=True, blank=True)
    frozen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Plan({self.comic}:{self.status})"


class BillingCustomer(UUIDModel):
    """A Stripe customer owned by one user, shared by all their comics."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_customer",
    )
    stripe_customer_id = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} @ {self.stripe_customer_id}"
