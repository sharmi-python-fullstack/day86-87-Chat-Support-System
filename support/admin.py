from django.contrib import admin

from .models import Message, StaffProfile, Ticket


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "department")
    list_filter = ("department",)
    search_fields = ("user__username", "user__email")


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("subject", "user", "status", "priority", "assigned_to", "created_at")
    list_filter = ("status", "priority", "assigned_to__department")
    search_fields = ("subject", "description", "user__username")
    inlines = (MessageInline,)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("ticket", "user", "created_at")
    search_fields = ("content", "ticket__subject", "user__username")
