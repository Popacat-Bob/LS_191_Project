"""Facebook live comments and item reservations."""

from django.db import models


class Reservation(models.Model):
    """A temporary or confirmed claim on inventory."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        CONFIRMED = "CONFIRMED", "Confirmed"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"
        OVERRIDDEN = "OVERRIDDEN", "Overridden"

    item = models.ForeignKey(
        "inventory.InventoryItem",
        related_name="reservations",
        on_delete=models.PROTECT,
    )
    customer = models.ForeignKey(
        "accounts.Customer", related_name="reservations", on_delete=models.PROTECT
    )
    source_comment = models.ForeignKey(
        "mining.LiveComment",
        related_name="created_reservations",
        on_delete=models.PROTECT,
    )
    qty = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    payment_deadline = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "customer"],
                condition=models.Q(status__in=["ACTIVE", "CONFIRMED"]),
                name="unique_live_reservation_per_item_customer",
            )
        ]

    def __str__(self) -> str:
        return f"{self.item.mining_code} for {self.customer.full_name}"


class LiveComment(models.Model):
    """A comment received from a Facebook live video."""

    class Outcome(models.TextChoices):
        RESERVED = "RESERVED", "Reserved"
        BACKUP = "BACKUP", "Backup"
        INVALID = "INVALID", "Invalid"
        IGNORED = "IGNORED", "Ignored"
        FLAGGED_LATE = "FLAGGED_LATE", "Flagged late"

    fb_comment_id = models.CharField(max_length=255, unique=True)
    live_video_id = models.CharField(max_length=255)
    message = models.TextField()
    raw_message = models.TextField(blank=True, default="")
    fb_commenter_id = models.CharField(max_length=255)
    fb_commenter_name = models.CharField(max_length=255)
    created_time = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    parsed_code = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    outcome = models.CharField(
        max_length=16, choices=Outcome.choices, blank=True, default=""
    )
    priority = models.PositiveIntegerField(null=True, blank=True)
    reservation = models.ForeignKey(
        Reservation,
        related_name="comments",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ["created_time", "fb_comment_id"]

    def __str__(self) -> str:
        return f"{self.fb_commenter_name}: {self.message[:80]}"


class BackupMiner(models.Model):
    """An ordered claim waiting for an item's active reservation to expire."""

    class Status(models.TextChoices):
        WAITING = "WAITING", "Waiting"
        PROMOTED = "PROMOTED", "Promoted"
        DROPPED = "DROPPED", "Dropped"

    item = models.ForeignKey(
        "inventory.InventoryItem",
        related_name="backup_miners",
        on_delete=models.CASCADE,
    )
    customer = models.ForeignKey(
        "accounts.Customer", related_name="backup_mines", on_delete=models.CASCADE
    )
    comment = models.ForeignKey(
        LiveComment, related_name="backup_positions", on_delete=models.CASCADE
    )
    priority = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.WAITING
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "comment"], name="unique_backup_item_comment"
            )
        ]
        ordering = ["comment__created_time", "comment__fb_comment_id"]

    def __str__(self) -> str:
        return f"{self.item.mining_code} backup #{self.priority}"
