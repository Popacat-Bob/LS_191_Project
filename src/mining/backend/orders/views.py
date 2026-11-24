"""Read-only order API and invoice-delivery trigger."""

from rest_framework import generics, status
from rest_framework.response import Response

from accounts.permissions import ORDERS_SEND_INVOICE, ORDERS_VIEW, HasAction
from invoicing.services import send_invoice_for_order
from orders.models import Order
from orders.serializers import OrderFilterSerializer, OrderSerializer


class OrderListView(generics.ListAPIView):
    """Paginated order list with customer and status filters."""

    serializer_class = OrderSerializer
    permission_classes = [HasAction(ORDERS_VIEW)]

    def get_queryset(self):
        filters = OrderFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        queryset = (
            Order.objects.select_related("customer", "invoice")
            .prefetch_related("items__item")
            .order_by("-order_date", "-pk")
        )
        values = filters.validated_data
        if "status" in values:
            queryset = queryset.filter(status=values["status"])
        if "customer" in values:
            customer = values["customer"]
            queryset = (
                queryset.filter(customer_id=int(customer))
                if customer.isdecimal()
                else queryset.filter(customer__facebook_id=customer)
            )
        return queryset


class SendOrderInvoiceView(generics.GenericAPIView):
    """Send an invoice for an eligible unpaid order."""

    permission_classes = [HasAction(ORDERS_SEND_INVOICE)]

    def post(self, request, order_id: int) -> Response:
        invoice = send_invoice_for_order(order_id)
        return Response(
            {"invoice_id": invoice.pk, "status": invoice.status},
            status=status.HTTP_202_ACCEPTED,
        )
