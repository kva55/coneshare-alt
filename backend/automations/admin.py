from django.contrib import admin

from .models import WebhookDelivery, WebhookEndpoint


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', 'is_active', 'created_at')
    list_filter = ('is_active', 'organization')
    search_fields = ('name', 'url')


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'endpoint', 'status', 'attempt_count', 'created_at')
    list_filter = ('status', 'event_type')
