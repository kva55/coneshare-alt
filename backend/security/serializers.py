from rest_framework import serializers

from .models import SecurityEvent, SecuritySettings
from .engine import RULE_IDS
from .capture import CAPTURE_ACTIONS, CAPTURE_EVENTS

FIELDS = [
    'code_requests_per_email', 'code_requests_per_ip', 'code_request_window_minutes',
    'failed_auth_alerts_enabled', 'failed_auth_threshold', 'failed_auth_window_minutes',
    'alert_group_email', 'alert_notify_account',
    'probe_detection_enabled', 'probe_log_authenticated', 'probe_log_unknown_areas',
    'probe_alert_enabled', 'probe_alert_cooldown_minutes', 'probe_disabled_rules',
    'nda_link_url', 'nda_link_text', 'monitoring_enabled', 'login_notice_enabled', 'login_notice_text', 'checkout_tracing_enabled',
    'throttling_enabled', 'user_requests_per_minute', 'viewer_requests_per_minute', 'general_requests_per_minute', 'capture_actions', 'capture_lockout_minutes',
]


class SecuritySettingsSerializer(serializers.ModelSerializer):
    probe_disabled_rules = serializers.ListField(child=serializers.ChoiceField(choices=RULE_IDS), required=False)
    capture_actions = serializers.DictField(child=serializers.ChoiceField(choices=CAPTURE_ACTIONS), required=False)

    class Meta:
        model = SecuritySettings
        fields = FIELDS

    def validate_nda_link_url(self, value):
        from urllib.parse import urlparse
        value = (value or '').strip()
        if value:
            parsed = urlparse(value)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc:   # never javascript:, data: and the like
                raise serializers.ValidationError('Enter a full http:// or https:// address.')
        return value

    def validate_nda_link_text(self, value):
        return (value or '').strip()[:120]

    def validate_login_notice_text(self, value):
        if len(value) > 2000:
            raise serializers.ValidationError('At most 2000 characters.')
        return value.strip()

    def validate_capture_actions(self, value):
        unknown = sorted(set(value) - set(CAPTURE_EVENTS))
        if unknown:
            raise serializers.ValidationError(f"Unknown signals: {', '.join(unknown)}")
        return value

    def validate(self, attrs):
        merged = {f: attrs.get(f, getattr(self.instance, f, None)) for f in FIELDS}
        for field, (low, high) in SecuritySettings.LIMITS.items():
            value = merged[field]
            if value is not None and not low <= value <= high:
                raise serializers.ValidationError({field: f'Must be between {low} and {high}.'})
        needs_group = merged['probe_alert_enabled']
        if needs_group and not merged['alert_group_email']:
            raise serializers.ValidationError({'alert_group_email': 'Set an alert address to send probe alerts to.'})
        return attrs


class SecurityEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = SecurityEvent
        fields = ['id', 'created_at', 'kind', 'rule', 'method', 'path', 'query_excerpt', 'ip_address',
                  'user_agent', 'authenticated', 'actor', 'detail']
        read_only_fields = fields
