from django.db import models

from actors import crypto
from core.models import UUIDModel


class Provider(models.TextChoices):
    KOFI = "kofi", "Ko-fi"
    BMAC = "bmac", "Buy Me a Coffee"


class Tier(UUIDModel):
    comic = models.ForeignKey(
        "comics.Comic", on_delete=models.CASCADE, related_name="tiers"
    )
    name = models.CharField(max_length=100)
    rank = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "name"], name="unique_tier_name_per_comic"
            ),
            models.UniqueConstraint(
                fields=["comic", "rank"], name="unique_tier_rank_per_comic"
            ),
        ]
        ordering = ["comic", "rank", "id"]

    def __str__(self):
        return f"{self.comic}:{self.name}"


class SupporterLink(UUIDModel):
    comic = models.ForeignKey(
        "comics.Comic", on_delete=models.CASCADE, related_name="supporter_links"
    )
    actor = models.ForeignKey(
        "actors.Actor", on_delete=models.CASCADE, related_name="supporter_links"
    )
    provider = models.CharField(max_length=16, choices=Provider.choices)
    provider_email = models.EmailField()
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "actor"], name="unique_supporter_per_comic"
            )
        ]

    def __str__(self):
        return f"{self.actor} supports {self.comic}"


class SupporterStatus(UUIDModel):
    link = models.ForeignKey(
        SupporterLink, on_delete=models.CASCADE, related_name="statuses"
    )
    tier = models.ForeignKey(Tier, on_delete=models.CASCADE, related_name="statuses")
    active = models.BooleanField(default=True)
    valid_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.link} @ {self.tier}"


class ProviderConnection(UUIDModel):
    comic = models.ForeignKey(
        "comics.Comic", on_delete=models.CASCADE, related_name="provider_connections"
    )
    provider = models.CharField(max_length=16, choices=Provider.choices)
    webhook_token = models.BinaryField(blank=True, default=b"")
    api_key = models.BinaryField(blank=True, default=b"")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["comic", "provider"], name="unique_provider_connection"
            )
        ]

    def set_webhook_token(self, token):
        self.webhook_token = crypto.encrypt(token.encode())

    def get_webhook_token(self):
        if not self.webhook_token:
            return ""
        return crypto.decrypt(bytes(self.webhook_token)).decode()

    def set_api_key(self, key):
        self.api_key = crypto.encrypt(key.encode())

    def get_api_key(self):
        if not self.api_key:
            return ""
        return crypto.decrypt(bytes(self.api_key)).decode()

    def __str__(self):
        return f"{self.comic}:{self.provider}"


class ProviderSupporter(UUIDModel):
    connection = models.ForeignKey(
        ProviderConnection, on_delete=models.CASCADE, related_name="supporters"
    )
    email = models.EmailField()
    tier_name = models.CharField(max_length=100, blank=True, default="")
    active = models.BooleanField(default=False)
    valid_until = models.DateTimeField(null=True, blank=True)
    last_payment_at = models.DateTimeField(null=True, blank=True)
    last_transaction_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "email"], name="unique_provider_supporter"
            )
        ]
        ordering = ["connection", "email"]

    def __str__(self):
        return f"{self.email} @ {self.connection}"
