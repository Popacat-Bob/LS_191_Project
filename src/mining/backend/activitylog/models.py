"""Audit trail for user and system state changes."""

from django.conf import settings
from django.db import models


class ActivityLog(models.Model):
    """Records who changed a target and the before/after values."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="activity_entries",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=100)
    previous_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.action}: {self.target_type} {self.target_id}"
