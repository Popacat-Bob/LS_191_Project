from django.contrib import admin

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["id", "customer", "status", "total_amount", "order_date"]
    list_filter = ["status", "order_date"]
    search_fields = ["customer__full_name", "customer__facebook_id"]
    inlines = [OrderItemInline]
    readonly_fields = ["total_amount"]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ["order", "item", "quantity", "unit_price"]
    search_fields = ["order__customer__full_name", "item__mining_code"]
