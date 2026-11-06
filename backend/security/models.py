from django.core.exceptions import ValidationError
from django.db import models

from core.fields import ULIDField
from core.models import BaseModel

from .engine import DEFAULT_DISABLED


def default_disabled_rules():
    """Rules for stacks this app is not (PHP, Java) start switched off; they can be enabled."""
    return list(DEFAULT_DISABLED)


DEFAULT_LOGIN_NOTICE = (
    "Attention: You are authenticating to a system that is monitored for compliance. You actions may be "
    "recorded and subject to review in accordance to sensitive data handling. Proceed only if you accept "
    "these terms and conditions."
)


class SecuritySettings(BaseModel):
    """Deployment-wide security settings, edited in the admin UI (a single row)."""
    # E-mailed codes / links: how many may be requested (share links and /checkout).
    code_requests_per_email = models.PositiveSmallIntegerField(default=5)
    code_requests_per_ip = models.PositiveSmallIntegerField(default=30)
    code_request_window_minutes = models.PositiveSmallIntegerField(default=15)

    # Alerts for repeated failures (login, access codes, check-out links).
    failed_auth_alerts_enabled = models.BooleanField(default=True)
    failed_auth_threshold = models.PositiveSmallIntegerField(default=3)
    failed_auth_window_minutes = models.PositiveSmallIntegerField(default=15)
    alert_group_email = models.EmailField(blank=True)       # a group / security mailbox
    alert_notify_account = models.BooleanField(default=True)  # also tell the account that was targeted

    # Experimental: log (never block) requests that look like probing.
    probe_detection_enabled = models.BooleanField(default=False)
    probe_log_authenticated = models.BooleanField(default=True)
    probe_log_unknown_areas = models.BooleanField(default=True)
    probe_alert_enabled = models.BooleanField(default=False)
    probe_alert_cooldown_minutes = models.PositiveSmallIntegerField(default=10)
    probe_disabled_rules = models.JSONField(default=default_disabled_rules, blank=True)

    # Master switch for behavioural monitoring: viewer signals (print, copy, screenshot keys, focus...),
    # their configured responses, check-out cursor tracing and interaction analysis. Access and
    # authentication records (who asked, who was admitted or refused) and security alerts are NOT affected.
    monitoring_enabled = models.BooleanField(default=True)

    # Notice shown before anyone signs in (staff login, file request, share-link access). On by default.
    login_notice_enabled = models.BooleanField(default=True)
    login_notice_text = models.TextField(default=DEFAULT_LOGIN_NOTICE, blank=True)
    # A link shown in every NDA window (for example the Acceptable Use Policy page). Empty = no link.
    nda_link_url = models.CharField(max_length=500, blank=True, default='')
    nda_link_text = models.CharField(max_length=120, blank=True, default='Acceptable Use Policy (AUP)')
    # Record where a guest's cursor goes (and how long each page is in view) while a file is checked out,
    # for the session report. On by default; the notice above says that actions may be recorded.
    checkout_tracing_enabled = models.BooleanField(default=True)

    # Request throttling. OFF unless an administrator turns it on. When on, limits are per minute, per
    # browser session or address. Viewing makes many small requests (every page, tile, focus report and
    # behaviour signal), so it has its own, higher, limit. (Password attempts and e-mailed codes have
    # their own limits that stay on regardless.)
    throttling_enabled = models.BooleanField(default=False)
    user_requests_per_minute = models.PositiveIntegerField(default=600)       # signed-in users
    viewer_requests_per_minute = models.PositiveIntegerField(default=1500)
    general_requests_per_minute = models.PositiveIntegerField(default=300)   # everything else, anonymous callers

    # What happens when the viewer detects an attempt (print, screenshot key, save, inspect, copy, ...).
    # {event_type: action}; a missing type uses DEFAULT_CAPTURE_ACTIONS. Actions: see CAPTURE_ACTIONS.
    capture_actions = models.JSONField(default=dict, blank=True)
    capture_lockout_minutes = models.PositiveSmallIntegerField(default=5)   # how long the 'end' action locks a viewer out

    updated_by = models.ForeignKey('core.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    LIMITS = {
        'viewer_requests_per_minute': (60, 100000),
        'user_requests_per_minute': (60, 100000),
        'general_requests_per_minute': (30, 100000),
        'capture_lockout_minutes': (1, 1440),
        'code_requests_per_email': (1, 100),
        'code_requests_per_ip': (1, 1000),
        'code_request_window_minutes': (1, 1440),
        'failed_auth_threshold': (1, 50),
        'failed_auth_window_minutes': (1, 1440),
        'probe_alert_cooldown_minutes': (1, 1440),
    }

    def clean(self):
        errors = {}
        for field, (low, high) in self.LIMITS.items():
            value = getattr(self, field)
            if not low <= value <= high:
                errors[field] = f'Must be between {low} and {high}.'
        if errors:
            raise ValidationError(errors)

    @classmethod
    def load(cls):
        return cls.objects.order_by('created_at').first() or cls()


class SecurityEvent(models.Model):
    """Append-only record of security-relevant happenings (alerts, probes, settings changes)."""
    KIND_FAILED_AUTH = 'failed_auth_alert'
    KIND_PROBE = 'web_probe'
    KIND_SETTINGS = 'settings_changed'

    id = ULIDField(primary_key=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=40)
    rule = models.CharField(max_length=60, blank=True)
    method = models.CharField(max_length=10, blank=True)
    path = models.CharField(max_length=512, blank=True)
    query_excerpt = models.CharField(max_length=200, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    authenticated = models.BooleanField(default=False)   # carried a sign-in cookie
    actor = models.CharField(max_length=254, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    alerted_at = models.DateTimeField(null=True, blank=True)   # included in an alert e-mail at this time

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at'], name='sec_event_time_idx'),
            models.Index(fields=['kind', '-created_at'], name='sec_event_kind_idx'),
        ]
