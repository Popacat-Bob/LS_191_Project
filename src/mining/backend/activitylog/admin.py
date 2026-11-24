from django.contrib import admin

from .models import ActivityLog


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = [
        "created_at",
        "user",
        "action",
        "target_type",
        "target_id",
        "reason",
    ]
    list_filter = ["action", "target_type", "created_at"]
    search_fields = ["target_type", "target_id", "reason", "user__email"]
    readonly_fields = [
        "user",
        "action",
        "target_type",
        "target_id",
        "previous_value",
        "new_value",
        "reason",
        "created_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
