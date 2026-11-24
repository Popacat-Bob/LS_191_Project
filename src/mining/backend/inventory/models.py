"""Inventory records."""

from django.db import models
from django.db.models import Q


class InventoryItem(models.Model):
    """A sellable item tracked by its mining code."""

    class AvailabilityStatus(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        RESERVED = "RESERVED", "Reserved"
        SOLD = "SOLD", "Sold"

    mining_code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    acquisition_cost = models.DecimalField(max_digits=12, decimal_places=2)
    stock_qty = models.PositiveIntegerField(default=1)
    reserved_qty = models.PositiveIntegerField(default=0)
    availability_status = models.CharField(
        max_length=16,
        choices=AvailabilityStatus.choices,
        default=AvailabilityStatus.AVAILABLE,
    )
    is_archived = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["mining_code"],
                condition=Q(is_archived=False) & ~Q(availability_status="SOLD"),
                name="unique_active_inventory_mining_code",
            )
        ]
        ordering = ["mining_code", "id"]

    def __str__(self) -> str:
        return f"{self.mining_code} - {self.name}"
