from django.contrib import admin

from .models import InventoryItem


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = [
        "mining_code",
        "name",
        "selling_price",
        "stock_qty",
        "reserved_qty",
        "availability_status",
        "is_archived",
    ]
    list_filter = ["availability_status", "is_archived"]
    search_fields = ["mining_code", "name"]
    readonly_fields = ["stock_qty", "reserved_qty", "availability_status"]
