"""Order read API serializers."""

from rest_framework import serializers

from orders.models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.mining_code", read_only=True)
    item_name = serializers.CharField(source="item.name", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "item_id", "item_code", "item_name", "quantity", "unit_price"]


class OrderSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    invoice_summary = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "customer_id",
            "customer_name",
            "status",
            "shipping_address",
            "total_amount",
            "order_date",
            "items",
            "invoice_summary",
        ]

    def get_invoice_summary(self, order: Order):
        invoice = getattr(order, "invoice", None)
        if invoice is None:
            return None
        return {
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "total": invoice.total,
            "payment_deadline": invoice.payment_deadline,
            "payment_link_url": invoice.payment_link_url,
            "sent_at": invoice.sent_at,
        }


class OrderFilterSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.Status.choices, required=False)
    customer = serializers.CharField(required=False, allow_blank=False)
