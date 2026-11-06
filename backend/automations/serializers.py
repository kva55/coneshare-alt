
import re

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.ssrf import DestinationResolutionError, validate_outbound_url

from .constants import MAX_WEBHOOKS_PER_ORG, WEBHOOK_EVENTS
from .models import EmailSettings, WebhookDelivery, WebhookEndpoint


class WebhookEndpointSerializer(serializers.ModelSerializer):
    events = serializers.ListField(child=serializers.ChoiceField(choices=sorted(WEBHOOK_EVENTS)), allow_empty=True)

    class Meta:
        model = WebhookEndpoint
        fields = ['id', 'name', 'url', 'events', 'is_active', 'secret_version', 'created_at', 'updated_at']
        read_only_fields = ['id', 'secret_version', 'created_at', 'updated_at']

    def validate_url(self, value):
        if not value.lower().startswith('https://') and not settings.DEBUG:
            raise serializers.ValidationError("Webhook URLs must use HTTPS.")
        try:
            validate_outbound_url(value)
        except DestinationResolutionError:
            raise serializers.ValidationError("The host name could not be resolved.")
        return value

    def validate(self, attrs):
        if self.instance is None:
            org = self.context['request'].user.organization
            if WebhookEndpoint.objects.filter(organization=org).count() >= MAX_WEBHOOKS_PER_ORG:
                raise serializers.ValidationError(f"At most {MAX_WEBHOOKS_PER_ORG} webhooks are allowed.")
        return attrs


class WebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDelivery
        fields = ['id', 'event_type', 'status', 'response_code', 'response_body_excerpt',
                  'attempt_count', 'next_retry_at', 'delivered_at', 'created_at']
        read_only_fields = fields


HOST_RE = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$')
SETTINGS_FIELDS = ['enabled', 'host', 'port', 'username', 'use_tls', 'use_ssl', 'from_email', 'allow_insecure',
                   'imap_host', 'imap_port', 'imap_use_ssl', 'imap_username', 'imap_test_address']


class EmailSettingsSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=512)
    imap_password = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=512)
    has_password = serializers.SerializerMethodField()
    has_imap_password = serializers.SerializerMethodField()

    class Meta:
        model = EmailSettings
        fields = SETTINGS_FIELDS + ['password', 'has_password', 'imap_password', 'has_imap_password']

    def get_has_password(self, obj) -> bool:
        return bool(obj.password_encrypted)

    def get_has_imap_password(self, obj) -> bool:
        return bool(obj.imap_password_encrypted)

    def _validate_host(self, value):
        value = (value or '').strip()
        if value and not HOST_RE.match(value):
            raise serializers.ValidationError('Enter a host name or address only (no scheme, port, path or spaces).')
        return value

    def validate_host(self, value):
        return self._validate_host(value)

    def validate_imap_host(self, value):
        return self._validate_host(value)

    def validate(self, attrs):
        merged = {f: attrs.get(f, getattr(self.instance, f, None)) for f in SETTINGS_FIELDS}
        probe = EmailSettings(**{k: v for k, v in merged.items() if v is not None})
        try:
            probe.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'error_dict') else exc.messages)
        return attrs
