from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from accounts.models import Customer
from activitylog.models import ActivityLog
from inventory.models import InventoryItem
from invoicing.models import Invoice, Payment
from mining import demo_showcase
from mining.models import BackupMiner, LiveComment, Reservation
from orders.models import Order
from tests.factories import (
    BackupMinerFactory,
    CustomerFactory,
    InventoryItemFactory,
    InvoiceFactory,
    LiveCommentFactory,
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ReservationFactory,
)


@pytest.mark.django_db(transaction=True)
def test_simulate_comment_command_single_run(capsys):
    from invoicing.tasks import send_invoice

    with patch.object(send_invoice, "delay"):
        call_command(
            "simulate_comment",
            code="CLI-ONE",
            user="Juan",
            ts=timezone.now().isoformat(),
            video="cli-live",
            comment_id="cli-comment-1",
        )

    assert "Juan: RESERVED" in capsys.readouterr().out
    item = InventoryItem.objects.get(mining_code="CLI-ONE")
    assert item.reserved_qty == 1


@pytest.mark.django_db(transaction=True)
def test_simulate_comment_no_prepare_does_not_increase_stock(capsys):
    item = InventoryItemFactory(
        mining_code="DEMO-NO-PREPARE",
        stock_qty=1,
        reserved_qty=0,
    )
    from invoicing.tasks import send_invoice

    with patch.object(send_invoice, "delay"):
        call_command(
            "simulate_comment",
            code=item.mining_code,
            user="Juan",
            ts="2026-11-11T09:00:00+08:00",
            comment_id="demo-no-prepare-juan",
            no_prepare=True,
        )
        call_command(
            "simulate_comment",
            code=item.mining_code,
            user="Maria",
            ts="2026-11-11T09:00:05+08:00",
            comment_id="demo-no-prepare-maria",
            no_prepare=True,
        )

    item.refresh_from_db()
    assert item.stock_qty == item.reserved_qty == 1
    outcomes = dict(
        LiveComment.objects.filter(
            fb_comment_id__in=[
                "demo-no-prepare-juan",
                "demo-no-prepare-maria",
            ]
        ).values_list("fb_commenter_name", "outcome")
    )
    assert outcomes == {"Juan": "RESERVED", "Maria": "BACKUP"}
    assert "Maria: BACKUP" in capsys.readouterr().out


@pytest.mark.django_db(transaction=True)
def test_simulate_comment_race_command_is_rerunnable_and_has_one_winner(
    monkeypatch, capsys
):
    from invoicing.tasks import send_invoice

    monkeypatch.setattr(send_invoice, "delay", lambda *args, **kwargs: None)

    call_command("simulate_comment", scenario="race", code="CLI-RACE")
    first_output = capsys.readouterr().out
    call_command("simulate_comment", scenario="race", code="CLI-RACE")
    second_output = capsys.readouterr().out

    for output in (first_output, second_output):
        assert output.count("RESERVED") == 1
        assert "Order total: 100.00" in output
    item = InventoryItem.objects.get(mining_code="CLI-RACE")
    assert item.stock_qty == item.reserved_qty == 2
    assert item.selling_price == Decimal("100.00")
    assert not ActivityLog.objects.filter(target_id="None").exists()


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_demo_reset_refuses_to_run_without_debug():
    with pytest.raises(CommandError, match="only when DEBUG is True"):
        call_command("demo_reset")


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_demo_reset_removes_preexisting_showcase_state(monkeypatch, tmp_path):
    state_path = tmp_path / "showcase.json"
    monkeypatch.setattr(demo_showcase, "SHOWCASE_STATE_PATH", state_path)
    state_path.write_text('{"code": "999"}', encoding="utf-8")

    call_command("demo_reset")

    assert not state_path.exists()


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_demo_reset_deletes_demo_records_and_preserves_unrelated_data(capsys):
    demo_item = InventoryItemFactory(mining_code="100", name="[DEMO] Old jacket")
    demo_customer = CustomerFactory()
    source_comment = LiveCommentFactory(
        parsed_code="demo-old",
        message="mine demo-old",
        fb_commenter_id=demo_customer.facebook_id,
    )
    reservation = ReservationFactory(
        item=demo_item,
        customer=demo_customer,
        source_comment=source_comment,
    )
    source_comment.reservation = reservation
    source_comment.save(update_fields=["reservation"])
    backup_comment = LiveCommentFactory(
        parsed_code="DEMO-OLD",
        fb_commenter_id="demo-backup-customer",
    )
    backup = BackupMinerFactory(
        item=demo_item,
        comment=backup_comment,
    )
    demo_order = OrderFactory(customer=demo_customer)
    OrderItemFactory(order=demo_order, item=demo_item)
    demo_invoice = InvoiceFactory(order=demo_order)

    unrelated_item = InventoryItemFactory(mining_code="101", name="Unrelated item")
    unrelated_comment = LiveCommentFactory(
        parsed_code="KEEP-ITEM",
        fb_commenter_id="keep-commenter",
    )
    unrelated_customer = CustomerFactory()
    unrelated_order = OrderFactory(customer=unrelated_customer)
    OrderItemFactory(order=unrelated_order, item=unrelated_item)
    unrelated_invoice = InvoiceFactory(order=unrelated_order)
    call_command("demo_reset")

    assert not InventoryItem.objects.filter(pk=demo_item.pk).exists()
    assert not Reservation.objects.filter(pk=reservation.pk).exists()
    assert not BackupMiner.objects.filter(pk=backup.pk).exists()
    assert not LiveComment.objects.filter(
        pk__in=[source_comment.pk, backup_comment.pk]
    ).exists()
    assert not Order.objects.filter(pk=demo_order.pk).exists()
    assert not Invoice.objects.filter(pk=demo_invoice.pk).exists()
    recreated = InventoryItem.objects.get(mining_code="DEMO-101")
    assert recreated.stock_qty == 1
    assert recreated.selling_price == Decimal("350.00")
    assert recreated.acquisition_cost == Decimal("0.00")
    assert InventoryItem.objects.get(mining_code="DEMO-102").selling_price == Decimal(
        "250.00"
    )
    assert InventoryItem.objects.get(mining_code="DEMO-103").selling_price == Decimal(
        "500.00"
    )

    assert InventoryItem.objects.filter(pk=unrelated_item.pk).exists()
    assert InventoryItem.objects.get(pk=unrelated_item.pk).mining_code == "101"
    assert LiveComment.objects.filter(pk=unrelated_comment.pk).exists()
    assert Customer.objects.filter(pk=unrelated_customer.pk).exists()
    assert Order.objects.filter(pk=unrelated_order.pk).exists()
    assert Invoice.objects.filter(pk=unrelated_invoice.pk).exists()
    assert "recreated DEMO-101" in capsys.readouterr().out


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_demo_reset_preserves_and_refuses_demo_linked_payment_records():
    demo_item = InventoryItemFactory(mining_code="DEMO-PAID")
    order = OrderFactory()
    OrderItemFactory(order=order, item=demo_item)
    invoice = InvoiceFactory(order=order)
    payment = PaymentFactory(order=order, invoice=invoice)

    with pytest.raises(CommandError, match="will not delete or detach payments"):
        call_command("demo_reset")

    assert InventoryItem.objects.filter(pk=demo_item.pk).exists()
    assert Order.objects.filter(pk=order.pk).exists()
    assert Invoice.objects.filter(pk=invoice.pk).exists()
    assert Payment.objects.filter(pk=payment.pk).exists()
    assert not InventoryItem.objects.filter(mining_code="DEMO-101").exists()


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_demo_reset_refuses_to_delete_mixed_demo_and_non_demo_order_lines():
    demo_item = InventoryItemFactory(mining_code="DEMO-MIXED")
    kept_item = InventoryItemFactory(mining_code="KEEP-MIXED")
    order = OrderFactory()
    demo_line = OrderItemFactory(order=order, item=demo_item)
    kept_line = OrderItemFactory(order=order, item=kept_item)

    with pytest.raises(CommandError, match="non-demo items"):
        call_command("demo_reset")

    assert InventoryItem.objects.filter(pk=demo_item.pk).exists()
    assert InventoryItem.objects.filter(pk=kept_item.pk).exists()
    assert Order.objects.filter(pk=order.pk).exists()
    assert order.items.filter(pk=demo_line.pk).exists()
    assert order.items.filter(pk=kept_line.pk).exists()
