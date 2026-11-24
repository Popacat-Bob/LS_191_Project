"""Invoices and the future payment-verification record."""

from django.db import models
from django.utils.timezone import localdate


class Invoice(models.Model):
    """An invoice with fields for a provider-hosted payment link."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SENT = "SENT", "Sent"
        PAID = "PAID", "Paid"
        EXPIRED = "EXPIRED", "Expired"
        FAILED = "FAILED", "Failed"

    order = models.OneToOneField(
        "orders.Order", related_name="invoice", on_delete=models.PROTECT
    )
    invoice_number = models.CharField(max_length=64, unique=True)
    issue_date = models.DateField(default=localdate)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    payment_deadline = models.DateTimeField()
    paymongo_link_id = models.CharField(max_length=255, blank=True)
    payment_link_url = models.URLField(max_length=2048, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )

    def __str__(self) -> str:
        return self.invoice_number


class Payment(models.Model):
    """Payment event storage reserved for the later verification phase."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VERIFIED = "VERIFIED", "Verified"
        FAILED = "FAILED", "Failed"

    order = models.ForeignKey(
        "orders.Order",
        related_name="payments",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    invoice = models.ForeignKey(
        Invoice,
        related_name="payments",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    method = models.CharField(max_length=64, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    reference_no = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    event_id = models.CharField(max_length=255, unique=True, null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(order__isnull=False)
                | models.Q(invoice__isnull=False),
                name="payment_has_order_or_invoice",
            )
        ]

    def __str__(self) -> str:
        return self.reference_no or f"Payment #{self.pk}"
