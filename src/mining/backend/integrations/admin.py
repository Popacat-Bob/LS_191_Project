from django.contrib import admin

from .models import WebhookEvent


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ["source", "event_id", "processed_at"]
    list_filter = ["source", "processed_at"]
    search_fields = ["event_id"]
    readonly_fields = ["source", "event_id", "payload", "processed_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
