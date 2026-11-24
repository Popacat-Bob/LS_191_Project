"""Orders and agreed-price line items."""

from django.db import models
from django.db.models import Q


class Order(models.Model):
    """A customer's order, including its invoice-ready total."""

    class Status(models.TextChoices):
        UNPAID = "UNPAID", "Unpaid"
        PAID = "PAID", "Paid"
        PACKAGED = "PACKAGED", "Packaged"

    customer = models.ForeignKey(
        "accounts.Customer", related_name="orders", on_delete=models.PROTECT
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.UNPAID
    )
    shipping_address = models.TextField(blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    order_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer"],
                condition=Q(status="UNPAID"),
                name="unique_unpaid_order_per_customer",
            )
        ]
        ordering = ["-order_date", "-id"]

    def __str__(self) -> str:
        return f"Order #{self.pk} - {self.customer.full_name}"


class OrderItem(models.Model):
    """An order line with the agreed unit price captured at sale time."""

    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    item = models.ForeignKey(
        "inventory.InventoryItem", related_name="order_items", on_delete=models.PROTECT
    )
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "item"], name="unique_item_per_order"
            )
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.item.mining_code}"
