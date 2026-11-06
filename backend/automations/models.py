from django.core.exceptions import ValidationError
from django.db import models

from core.models import BaseModel, Organization, User

from .crypto import decrypt_secret, derive_webhook_secret, encrypt_secret


class WebhookEndpoint(BaseModel):
    """A generic HTTPS endpoint that receives signed JSON for the events it subscribes to."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='webhook_endpoints')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='webhook_endpoints_created')
    name = models.CharField(max_length=255)
    url = models.URLField(max_length=2048)
    events = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    secret_version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['-created_at']

    @property
    def signing_secret(self) -> str:
        return derive_webhook_secret(str(self.id), self.secret_version)

    def __str__(self):
        return self.name


class WebhookDelivery(BaseModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed (will retry)'
        DEAD_LETTER = 'dead_letter', 'Gave up'

    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name='deliveries')
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    response_code = models.IntegerField(null=True, blank=True)
    response_body_excerpt = models.TextField(blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['endpoint', '-created_at'], name='wh_delivery_ep_time_idx')]


class EmailSettings(BaseModel):
    """
    Outgoing e-mail configuration, edited in the admin UI (a single row for the deployment).
    When ``enabled`` it overrides the EMAIL_* environment settings; otherwise those are used.
    """
    enabled = models.BooleanField(default=False)
    host = models.CharField(max_length=255, blank=True)
    port = models.PositiveIntegerField(default=587)
    username = models.CharField(max_length=255, blank=True)
    password_encrypted = models.TextField(blank=True)
    use_tls = models.BooleanField(default=True)
    use_ssl = models.BooleanField(default=False)
    from_email = models.EmailField(blank=True)
    # Explicit opt-in for connections without TLS (e.g. a lab server). Off: TLS is required.
    allow_insecure = models.BooleanField(default=False)
    # Optional inbox to confirm that mail really arrives ("receive test").
    imap_host = models.CharField(max_length=255, blank=True)
    imap_port = models.PositiveIntegerField(default=993)
    imap_use_ssl = models.BooleanField(default=True)
    imap_username = models.CharField(max_length=255, blank=True)
    imap_password_encrypted = models.TextField(blank=True)
    imap_test_address = models.EmailField(blank=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    def clean(self):
        if self.use_tls and self.use_ssl:
            raise ValidationError('Choose either STARTTLS or SSL/TLS, not both.')
        if not 1 <= self.port <= 65535:
            raise ValidationError({'port': 'Must be between 1 and 65535.'})
        if not 1 <= self.imap_port <= 65535:
            raise ValidationError({'imap_port': 'Must be between 1 and 65535.'})
        if self.enabled and not (self.use_tls or self.use_ssl) and not self.allow_insecure:
            raise ValidationError({'allow_insecure': 'Choose STARTTLS or SSL/TLS, or explicitly allow an unencrypted connection.'})
        if self.imap_host and not self.imap_use_ssl and not self.allow_insecure:
            raise ValidationError({'allow_insecure': 'The inbox check needs SSL/TLS unless you explicitly allow an unencrypted connection.'})
        if self.enabled and not self.host:
            raise ValidationError({'host': 'A host is required when e-mail settings are enabled.'})
        if self.enabled and not self.from_email:
            raise ValidationError({'from_email': 'A sender address is required when e-mail settings are enabled.'})

    @property
    def password(self) -> str:
        return decrypt_secret(self.password_encrypted)

    def set_password(self, plain: str) -> None:
        self.password_encrypted = encrypt_secret(plain)

    @property
    def imap_password(self) -> str:
        return decrypt_secret(self.imap_password_encrypted)

    def set_imap_password(self, plain: str) -> None:
        self.imap_password_encrypted = encrypt_secret(plain)

    @classmethod
    def load(cls) -> 'EmailSettings':
        return cls.objects.order_by('created_at').first() or cls()
