from django.db import models

from inventory.models import InventoryItem
from invoicing.models import Payment
from mining.models import LiveComment


def test_money_fields_use_decimal_storage():
    assert isinstance(
        InventoryItem._meta.get_field("selling_price"), models.DecimalField
    )
    assert isinstance(
        InventoryItem._meta.get_field("acquisition_cost"), models.DecimalField
    )
    assert isinstance(Payment._meta.get_field("amount"), models.DecimalField)


def test_live_comment_external_id_is_unique():
    assert LiveComment._meta.get_field("fb_comment_id").unique
