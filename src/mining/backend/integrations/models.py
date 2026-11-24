"""Durable records for external webhook deliveries."""

from django.db import models


class WebhookEvent(models.Model):
    """A deduplicated inbound webhook event."""

    class Source(models.TextChoices):
        FACEBOOK = "FACEBOOK", "Facebook"
        PAYMONGO = "PAYMONGO", "PayMongo"

    source = models.CharField(max_length=16, choices=Source.choices)
    event_id = models.CharField(max_length=255, unique=True)
    payload = models.JSONField()
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.source}: {self.event_id}"
