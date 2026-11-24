"""Reset records associated with marked demo inventory items."""

from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from accounts.models import Customer
from inventory.models import InventoryItem
from inventory.services import manage_inventory
from invoicing.models import Invoice, Payment
from mining.demo_showcase import DEMO_NAME_PREFIX, clear_showcase_state
from mining.models import BackupMiner, LiveComment, Reservation
from orders.models import Order, OrderItem


class Command(BaseCommand):
    help = "Delete marked demo data and recreate the short-story demo inventory."

    demo_items = (
        ("DEMO-101", "Vintage denim jacket", Decimal("350.00")),
        ("DEMO-102", "Floral dress", Decimal("250.00")),
        ("DEMO-103", "Leather bag", Decimal("500.00")),
    )

    def handle(self, *args, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("demo_reset is available only when DEBUG is True.")

        with transaction.atomic():
            item_ids = list(
                InventoryItem.objects.filter(
                    Q(name__startswith=DEMO_NAME_PREFIX)
                    | Q(mining_code__startswith="DEMO-")
                )
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            reservation_ids = list(
                Reservation.objects.filter(item_id__in=item_ids).values_list(
                    "pk", flat=True
                )
            )
            comment_ids = set(
                LiveComment.objects.filter(
                    parsed_code__istartswith="DEMO-"
                ).values_list("pk", flat=True)
            )
            comment_ids.update(
                LiveComment.objects.filter(
                    reservation_id__in=reservation_ids
                ).values_list("pk", flat=True)
            )
            comment_ids.update(
                LiveComment.objects.filter(
                    backup_positions__item_id__in=item_ids
                ).values_list("pk", flat=True)
            )
            comment_ids.update(
                LiveComment.objects.filter(
                    created_reservations__item_id__in=item_ids
                ).values_list("pk", flat=True)
            )
            comment_ids.update(
                LiveComment.objects.filter(
                    Q(live_video_id__startswith="demo-live-")
                    | Q(live_video_id__startswith="demo-continuous-")
                ).values_list("pk", flat=True)
            )
            order_ids = list(
                OrderItem.objects.filter(item_id__in=item_ids)
                .order_by("order_id")
                .values_list("order_id", flat=True)
                .distinct()
            )
            demo_customer_ids = set(
                Reservation.objects.filter(pk__in=reservation_ids).values_list(
                    "customer_id", flat=True
                )
            )
            demo_customer_ids.update(
                Customer.objects.filter(
                    facebook_id__in=LiveComment.objects.filter(
                        pk__in=comment_ids
                    ).values("fb_commenter_id")
                ).values_list("pk", flat=True)
            )
            order_ids.extend(
                Order.objects.filter(
                    customer_id__in=demo_customer_ids,
                    items__isnull=True,
                )
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            order_ids = sorted(set(order_ids))
            mixed_order_ids = (
                OrderItem.objects.filter(order_id__in=order_ids)
                .exclude(item_id__in=item_ids)
                .values_list("order_id", flat=True)
                .distinct()
            )
            if mixed_order_ids.exists():
                raise CommandError(
                    "A demo-linked order contains non-demo items; demo_reset "
                    "will not delete unrelated order lines."
                )
            invoice_ids = list(
                Invoice.objects.filter(order_id__in=order_ids).values_list(
                    "pk", flat=True
                )
            )
            if (
                Payment.objects.filter(order_id__in=order_ids).exists()
                or Payment.objects.filter(invoice_id__in=invoice_ids).exists()
            ):
                raise CommandError(
                    "Demo orders have payment records; demo_reset will not delete "
                    "or detach payments."
                )

            deleted = {
                "invoices": Invoice.objects.filter(pk__in=invoice_ids).delete()[0],
                "orders": Order.objects.filter(pk__in=order_ids).delete()[0],
                "backups": BackupMiner.objects.filter(item_id__in=item_ids).delete()[0],
                "reservations": Reservation.objects.filter(
                    pk__in=reservation_ids
                ).delete()[0],
                "comments": LiveComment.objects.filter(pk__in=comment_ids).delete()[0],
                "items": InventoryItem.objects.filter(pk__in=item_ids).delete()[0],
            }
            items = [
                manage_inventory(
                    action="create",
                    mining_code=code,
                    name=f"{DEMO_NAME_PREFIX}{name}",
                    description="Demo item for the live mining walkthrough.",
                    selling_price=price,
                    acquisition_cost=Decimal("0.00"),
                    stock_qty=1,
                    reason="Created by demo_reset.",
                )
                for code, name, price in self.demo_items
            ]

        clear_showcase_state()
        self.stdout.write(
            self.style.SUCCESS(
                f"Reset demo data (deleted {deleted}; recreated "
                + ", ".join(
                    f"{item.mining_code} stock={item.stock_qty} "
                    f"price={item.selling_price}"
                    for item in items
                )
                + ")."
            )
        )
