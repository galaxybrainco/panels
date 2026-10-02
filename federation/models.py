import uuid

from django.db import models


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    FAILED = "failed", "Retrying"
    DELIVERED = "delivered", "Delivered"
    DEAD = "dead", "Dead-lettered"


class SignatureScheme(models.TextChoices):
    CAVAGE = "cavage", "draft-cavage-http-signatures"
    RFC9421 = "rfc9421", "RFC 9421 HTTP Message Signatures"


class Delivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inbox_url = models.URLField()
    activity = models.JSONField()
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="deliveries"
    )
    status = models.CharField(
        max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=6)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]
        verbose_name_plural = "deliveries"

    def __str__(self):
        return f"{self.status} → {self.inbox_url}"


class PeerSignaturePreference(models.Model):
    domain = models.CharField(max_length=255, unique=True)
    scheme = models.CharField(max_length=16, choices=SignatureScheme.choices)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.domain}: {self.scheme}"


class ActivityStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSED = "processed", "Processed"
    REJECTED = "rejected", "Rejected"


class ActivityDirection(models.TextChoices):
    INBOUND = "inbound", "Inbound"
    OUTBOUND = "outbound", "Outbound"


class Activity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ap_id = models.URLField(unique=True)
    type = models.CharField(max_length=64)
    actor = models.ForeignKey(
        "actors.Actor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
    )
    direction = models.CharField(
        max_length=16,
        choices=ActivityDirection.choices,
        default=ActivityDirection.INBOUND,
    )
    status = models.CharField(
        max_length=16, choices=ActivityStatus.choices, default=ActivityStatus.RECEIVED
    )
    payload = models.JSONField()
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["actor", "direction", "status"])]
        verbose_name_plural = "activities"

    def __str__(self):
        return f"{self.type} {self.ap_id}"
