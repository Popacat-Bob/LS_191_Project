from django.contrib import admin

from .models import BackupMiner, LiveComment, Reservation


@admin.register(LiveComment)
class LiveCommentAdmin(admin.ModelAdmin):
    list_display = [
        "fb_comment_id",
        "fb_commenter_name",
        "parsed_code",
        "outcome",
        "created_time",
    ]
    list_filter = ["outcome", "live_video_id"]
    search_fields = ["fb_comment_id", "fb_commenter_name", "message", "parsed_code"]
    readonly_fields = ["received_at", "message", "raw_message"]


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ["item", "customer", "qty", "status", "payment_deadline"]
    list_filter = ["status"]
    search_fields = ["item__mining_code", "customer__full_name"]
    readonly_fields = ["status"]


@admin.register(BackupMiner)
class BackupMinerAdmin(admin.ModelAdmin):
    list_display = ["item", "customer", "priority", "status"]
    list_filter = ["status"]
    search_fields = ["item__mining_code", "customer__full_name"]
