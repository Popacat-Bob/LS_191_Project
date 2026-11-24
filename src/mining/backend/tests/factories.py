"""Factories for domain models used by tests."""

from datetime import timedelta
from decimal import Decimal

import factory
from django.utils import timezone

from accounts.models import Customer, User
from activitylog.models import ActivityLog
from integrations.models import WebhookEvent
from inventory.models import InventoryItem
from invoicing.models import Invoice, Payment
from mining.models import BackupMiner, LiveComment, Reservation
from orders.models import Order, OrderItem


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    name = factory.Faker("name")
    role = User.Role.LIVESELLER_ADMIN


class CustomerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Customer

    facebook_id = factory.Sequence(lambda n: f"facebook-{n}")
    full_name = factory.Faker("name")


class InventoryItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = InventoryItem

    mining_code = factory.Sequence(lambda n: f"ITEM-{n}")
    name = factory.Faker("word")
    selling_price = Decimal("100.00")
    acquisition_cost = Decimal("40.00")


class LiveCommentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = LiveComment

    fb_comment_id = factory.Sequence(lambda n: f"comment-{n}")
    live_video_id = "live-video-1"
    message = "mine ITEM-1"
    fb_commenter_id = factory.Sequence(lambda n: f"commenter-{n}")
    fb_commenter_name = factory.Faker("name")
    created_time = factory.LazyFunction(timezone.now)


class ReservationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Reservation

    item = factory.SubFactory(InventoryItemFactory)
    customer = factory.SubFactory(CustomerFactory)
    source_comment = factory.SubFactory(LiveCommentFactory)
    qty = 1
    status = Reservation.Status.ACTIVE
    payment_deadline = factory.LazyFunction(
        lambda: timezone.now() + timedelta(hours=24)
    )


class BackupMinerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BackupMiner

    item = factory.SubFactory(InventoryItemFactory)
    customer = factory.SubFactory(CustomerFactory)
    comment = factory.SubFactory(LiveCommentFactory)
    priority = factory.Sequence(lambda n: n + 1)


class OrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Order

    customer = factory.SubFactory(CustomerFactory)
    status = Order.Status.UNPAID


class OrderItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OrderItem

    order = factory.SubFactory(OrderFactory)
    item = factory.SubFactory(InventoryItemFactory)
    quantity = 1
    unit_price = Decimal("100.00")


class InvoiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Invoice

    order = factory.SubFactory(OrderFactory)
    invoice_number = factory.Sequence(lambda n: f"INV-{n:06d}")
    total = Decimal("100.00")
    payment_deadline = factory.LazyFunction(
        lambda: timezone.now() + timedelta(hours=24)
    )


class PaymentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Payment

    order = factory.SubFactory(OrderFactory)
    amount = Decimal("100.00")


class ActivityLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ActivityLog

    action = "created"
    target_type = "InventoryItem"
    target_id = "1"


class WebhookEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = WebhookEvent

    source = WebhookEvent.Source.FACEBOOK
    event_id = factory.Sequence(lambda n: f"event-{n}")
    payload = factory.LazyFunction(dict)
