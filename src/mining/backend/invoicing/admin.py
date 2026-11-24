from django.contrib import admin

from .models import Invoice, Payment


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = [
        "invoice_number",
        "order",
        "total",
        "status",
        "payment_deadline",
        "sent_at",
    ]
    list_filter = ["status", "issue_date"]
    search_fields = ["invoice_number", "order__customer__full_name"]
    readonly_fields = ["sent_at"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "invoice", "amount", "status", "reference_no"]
    list_filter = ["status", "method"]
    search_fields = ["reference_no", "event_id"]
