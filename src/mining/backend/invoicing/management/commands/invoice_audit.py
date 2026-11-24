"""Read-only report of unpaid orders with live reservations and invoice gaps."""

from django.core.management.base import BaseCommand
from django.db.models import F

from invoicing.models import Invoice
from mining.models import Reservation
from orders.models import Order


class Command(BaseCommand):
    help = "Audit invoices for unpaid orders with active or confirmed reservations."

    def handle(self, *args, **options) -> None:
        orders = list(
            Order.objects.filter(
                status=Order.Status.UNPAID,
                items__item__reservations__customer_id=F("customer_id"),
                items__item__reservations__status__in=[
                    Reservation.Status.ACTIVE,
                    Reservation.Status.CONFIRMED,
                ],
            )
            .select_related("customer")
            .distinct()
            .order_by("pk")
        )
        invoices = {
            invoice.order_id: invoice
            for invoice in Invoice.objects.filter(
                order_id__in=[order.pk for order in orders]
            )
        }

        headers = (
            "order_id",
            "customer",
            "order_total",
            "invoice_status",
            "sent_at",
            "invoice_total",
            "link",
            "gaps",
        )
        rows: list[tuple[str, ...]] = []
        gap_orders = 0
        gap_conditions = 0
        for order in orders:
            invoice = invoices.get(order.pk)
            gaps: list[str] = []
            if invoice is None:
                gaps.append("no invoice")
            else:
                if invoice.status != Invoice.Status.SENT:
                    gaps.append("status not SENT")
                if invoice.sent_at is None:
                    gaps.append("sent_at null")
                if invoice.total != order.total_amount:
                    gaps.append("total mismatch")
                if not invoice.payment_link_url:
                    gaps.append("empty payment link")
            gap_orders += bool(gaps)
            gap_conditions += len(gaps)
            rows.append(
                (
                    str(order.pk),
                    order.customer.full_name,
                    str(order.total_amount),
                    invoice.status if invoice else "-",
                    invoice.sent_at.isoformat() if invoice and invoice.sent_at else "-",
                    str(invoice.total) if invoice else "-",
                    (
                        "present"
                        if invoice and invoice.payment_link_url
                        else "empty"
                        if invoice
                        else "-"
                    ),
                    ", ".join(gaps) if gaps else "OK",
                )
            )

        widths = [
            max([len(header), *(len(row[index]) for row in rows)])
            for index, header in enumerate(headers)
        ]
        self.stdout.write(
            " | ".join(
                header.ljust(widths[index]) for index, header in enumerate(headers)
            )
        )
        self.stdout.write("-+-".join("-" * width for width in widths))
        for row in rows:
            self.stdout.write(
                " | ".join(
                    value.ljust(widths[index]) for index, value in enumerate(row)
                )
            )
        self.stdout.write(
            f"Eligible orders: {len(orders)}; gap orders: {gap_orders}; "
            f"gap conditions: {gap_conditions}"
        )
