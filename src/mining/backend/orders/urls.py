"""Order API routes."""

from django.urls import path

from orders.views import OrderListView, SendOrderInvoiceView

urlpatterns = [
    path("", OrderListView.as_view(), name="order-list"),
    path(
        "<int:order_id>/send-invoice/",
        SendOrderInvoiceView.as_view(),
        name="order-send-invoice",
    ),
]
