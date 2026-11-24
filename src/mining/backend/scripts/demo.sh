#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
. ./.env
set +a

pause() {
  sleep "${DEMO_PAUSE_SECONDS:-1}"
}

echo "=== Resetting only DEMO-* inventory and related demo records ==="
uv run --active python manage.py demo_reset
pause

echo
echo "=== Three comments arrive in original-timestamp order ==="
uv run --active python manage.py simulate_comment \
  --code=DEMO-101 --user=Juan --video=demo-live \
  --comment-id=demo-101-juan --ts=2026-11-11T09:00:00+08:00 --no-prepare
pause
uv run --active python manage.py simulate_comment \
  --code=DEMO-101 --user=Maria --video=demo-live \
  --comment-id=demo-101-maria --ts=2026-11-11T09:00:05+08:00 --no-prepare
pause
uv run --active python manage.py simulate_comment \
  --code=DEMO-101 --user=Ana --video=demo-live \
  --comment-id=demo-101-ana --ts=2026-11-11T09:00:10+08:00 --no-prepare
pause

echo
echo "=== Comment outcomes ==="
uv run --active python manage.py shell <<'PY'
from mining.models import LiveComment

for comment in LiveComment.objects.filter(parsed_code__iexact="DEMO-101").order_by(
    "created_time", "fb_comment_id"
):
    print(f"{comment.fb_commenter_name}: {comment.outcome}")
PY
pause

echo
echo "=== Order and invoice ==="
uv run --active python manage.py shell <<'PY'
from invoicing.services import send_invoice
from orders.models import Order

order = (
    Order.objects.filter(items__item__mining_code="DEMO-101")
    .distinct()
    .first()
)
if order is None:
    raise RuntimeError("The demo mine did not create an order.")
invoice = send_invoice(order.pk)
if invoice is None:
    raise RuntimeError("The demo order did not produce an invoice.")
print(
    f"Order #{order.pk}: {order.status}, total={order.total_amount}; "
    f"invoice={invoice.status}, link={invoice.payment_link_url}"
)
PY
pause

echo
echo "=== Backdating and expiring the current reservation ==="
uv run --active python manage.py shell <<'PY'
from datetime import timedelta

from django.utils import timezone

from mining.expiry import expire_due_reservations
from mining.models import Reservation

reservation = Reservation.objects.get(
    item__mining_code="DEMO-101",
    status=Reservation.Status.ACTIVE,
)
reservation.payment_deadline = timezone.now() - timedelta(seconds=1)
reservation.save(update_fields=["payment_deadline"])
print(f"Expiry result: {expire_due_reservations()}")
PY
pause

echo
echo "=== Backup promotion and promoted buyer invoice ==="
uv run --active python manage.py shell <<'PY'
from invoicing.services import send_invoice
from mining.models import BackupMiner, Reservation
from orders.models import Order

for backup in BackupMiner.objects.filter(
    item__mining_code="DEMO-101",
    status=BackupMiner.Status.PROMOTED,
).select_related("customer", "comment").order_by(
    "comment__created_time", "comment__fb_comment_id"
):
    reservation = Reservation.objects.get(
        item=backup.item,
        customer=backup.customer,
        status=Reservation.Status.ACTIVE,
    )
    order = Order.objects.get(customer=backup.customer, status=Order.Status.UNPAID)
    invoice = send_invoice(order.pk)
    print(
        f"{backup.customer.full_name} promoted from {backup.comment.fb_comment_id}: "
        f"reservation={reservation.status}, order_total={order.total_amount}, "
        f"invoice={invoice.status if invoice else 'skipped'}"
    )
PY
