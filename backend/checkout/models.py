import hashlib
import json
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django_cryptography.fields import encrypt

from core.fields import ULIDField
from core.models import BaseModel, Organization


class CheckoutPolicy(BaseModel):
    """
    Check-out rules for one datastore (dataroom): who may check files out (by e-mail domain),
    for how long, how often, and who is told. Disabled by default.
    """
    NOTIFY_LOG = 'log'
    NOTIFY_OWNER = 'owner'
    NOTIFY_GROUP = 'group'
    NOTIFY_CHOICES = [
        (NOTIFY_LOG, 'Internal log only'),
        (NOTIFY_OWNER, 'Email the document owner'),
        (NOTIFY_GROUP, 'Email a group address'),
    ]

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='checkout_policies')
    dataroom = models.OneToOneField('datarooms.Dataroom', on_delete=models.CASCADE, related_name='checkout_policy')
    enabled = models.BooleanField(default=False)
    checkout_minutes = models.PositiveSmallIntegerField(default=5)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    # Guests view files in the app (never a shareable link). Downloads are off unless allowed.
    allow_download = models.BooleanField(default=False)
    downloads_per_checkout = models.PositiveSmallIntegerField(default=1)
    # Optional NDA every guest must accept (again whenever the text changes) before viewing.
    require_nda = models.BooleanField(default=False)
    nda_text = models.TextField(blank=True)
    nda_version = models.PositiveIntegerField(default=1)
    notify_mode = models.CharField(max_length=10, choices=NOTIFY_CHOICES, default=NOTIFY_LOG)
    notify_email = models.EmailField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['dataroom__name']

    def clean(self):
        if not 1 <= self.checkout_minutes <= settings.CHECKOUT_MAX_MINUTES:
            raise ValidationError({'checkout_minutes': f'Must be between 1 and {settings.CHECKOUT_MAX_MINUTES}.'})
        if not 1 <= self.max_attempts <= settings.CHECKOUT_MAX_ATTEMPTS_CAP:
            raise ValidationError({'max_attempts': f'Must be between 1 and {settings.CHECKOUT_MAX_ATTEMPTS_CAP}.'})
        if self.notify_mode == self.NOTIFY_GROUP and not self.notify_email:
            raise ValidationError({'notify_email': 'A group address is required for this notification mode.'})

    def save(self, *args, **kwargs):
        if self.pk:
            previous = CheckoutPolicy.objects.filter(pk=self.pk).values_list('nda_text', 'nda_version').first()
            if previous and previous[0] != self.nda_text:
                self.nda_version = previous[1] + 1
        super().save(*args, **kwargs)

    def has_active_guest(self, email: str) -> bool:
        return self.enabled and self.guests.filter(email=email.strip().lower(), revoked_at__isnull=True).exists()

    def __str__(self):
        return f"checkout policy for {self.dataroom_id}"


class CheckoutGuest(BaseModel):
    """A person (by e-mail address) assigned to a datastore's check-out. Revoked, never deleted."""
    policy = models.ForeignKey(CheckoutPolicy, on_delete=models.CASCADE, related_name='guests')
    email = models.EmailField()
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='+')
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [models.UniqueConstraint(fields=['policy', 'email'], name='unique_checkout_guest')]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class CheckoutFile(BaseModel):
    """A dataroom document the admin has published for check-out under a policy."""
    policy = models.ForeignKey(CheckoutPolicy, on_delete=models.CASCADE, related_name='files')
    dataroom_document = models.ForeignKey('datarooms.DataroomDocument', on_delete=models.CASCADE, related_name='+')
    # Per-file access settings. They belong to the file in the datastore, not to any share link.
    visible = models.BooleanField(default=True)            # shown to guests at all
    # Experimental: an admin must approve each request for this file before the guest can open it.
    requires_approval = models.BooleanField(default=False)
    requires_nda = models.BooleanField(null=True, blank=True)       # None: follow the datastore
    allow_download = models.BooleanField(null=True, blank=True)     # None: follow the datastore

    def nda_needed(self) -> bool:
        return self.policy.require_nda if self.requires_nda is None else bool(self.requires_nda)

    def download_allowed(self) -> bool:
        return self.policy.allow_download if self.allow_download is None else bool(self.allow_download)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(fields=['policy', 'dataroom_document'], name='unique_checkout_file'),
        ]


class CheckoutLoginLink(BaseModel):
    """
    Single-use sign-in link e-mailed to a guest. Only a keyed hash of the token is stored. It is
    valid for a few minutes and only for the /checkout page.
    """
    NAMESPACE = 'checkout-link'

    email = models.EmailField()
    token_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)

    @staticmethod
    def hash_token(token: str) -> str:
        return hmac.new(settings.SECRET_KEY.encode(), f"{CheckoutLoginLink.NAMESPACE}:{token}".encode(),
                        hashlib.sha256).hexdigest()

    @classmethod
    def issue(cls, email: str, ip_address=None):
        """New link for ``email`` (older unused ones stop working); returns (row, raw_token)."""
        email = email.strip().lower()
        cls.objects.filter(email=email, consumed_at__isnull=True).update(consumed_at=timezone.now())
        token = secrets.token_urlsafe(32)
        row = cls.objects.create(
            email=email, token_hash=cls.hash_token(token),
            expires_at=timezone.now() + timedelta(minutes=settings.CHECKOUT_LOGIN_LINK_MINUTES),
            requested_ip=ip_address or None,
        )
        return row, token

    @classmethod
    def find(cls, token: str):
        return cls.objects.filter(token_hash=cls.hash_token(token or '')).first()

    def is_usable(self) -> bool:
        return self.consumed_at is None and self.expires_at > timezone.now()


class CheckoutNdaAcceptance(BaseModel):
    """A guest's acceptance of a policy's NDA at a given version."""
    policy = models.ForeignKey(CheckoutPolicy, on_delete=models.CASCADE, related_name='nda_acceptances')
    email = models.EmailField()
    nda_version = models.PositiveIntegerField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['policy', 'email', 'nda_version'], name='unique_checkout_nda')]


class CheckoutSession(BaseModel):
    """A signed-in /checkout visitor (the Django session holds only this row's id)."""
    email = models.EmailField()
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    def is_active(self) -> bool:
        return self.ended_at is None and self.expires_at > timezone.now()

    @property
    def domain(self) -> str:
        return self.email.rsplit('@', 1)[-1].lower()


class Checkout(BaseModel):
    """
    One request to check a file out. Counts as one attempt. It is ``approved`` at once, or
    ``pending`` until an admin decides (files that require approval). Opening it starts the
    time window (``expires_at``); until then it must be opened within CHECKOUT_REQUEST_VALID_HOURS.
    """
    PENDING, APPROVED, DENIED, OPEN, ENDED = 'pending', 'approved', 'denied', 'open', 'ended'
    STATUS_CHOICES = [(PENDING, 'Waiting for approval'), (APPROVED, 'Approved'), (DENIED, 'Denied'),
                      (OPEN, 'Open'), (ENDED, 'Ended')]

    session = models.ForeignKey(CheckoutSession, on_delete=models.CASCADE, related_name='checkouts')
    policy = models.ForeignKey(CheckoutPolicy, on_delete=models.CASCADE, related_name='checkouts')
    file = models.ForeignKey(CheckoutFile, on_delete=models.CASCADE, related_name='checkouts')
    email = models.EmailField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=APPROVED)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    downloads = models.PositiveSmallIntegerField(default=0)
    max_downloads = models.PositiveSmallIntegerField(default=1)
    last_download_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['policy', 'email', '-created_at'], name='co_checkout_pol_em_idx')]

    def request_expired(self) -> bool:
        """An unopened request lapses after CHECKOUT_REQUEST_VALID_HOURS."""
        return self.status in (self.PENDING, self.APPROVED) and \
            self.created_at + timedelta(hours=settings.CHECKOUT_REQUEST_VALID_HOURS) < timezone.now()

    def window_open(self) -> bool:
        return self.status == self.OPEN and self.expires_at is not None and self.expires_at > timezone.now()

    def downloads_left(self) -> int:
        return max(self.max_downloads - self.downloads, 0)

    def is_active(self) -> bool:
        """Pending, approved-but-unopened or open: it still counts as the guest's current request."""
        if self.status == self.OPEN:
            return self.window_open()
        return self.status in (self.PENDING, self.APPROVED) and not self.request_expired()


class CheckoutEvent(models.Model):
    """Append-only log of everything that happens at /checkout."""
    id = ULIDField(primary_key=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    policy = models.ForeignKey(CheckoutPolicy, null=True, blank=True, on_delete=models.SET_NULL, related_name='events')
    event_type = models.CharField(max_length=40)
    email = models.EmailField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at'], name='co_event_time_idx'),
            models.Index(fields=['event_type', '-created_at'], name='co_event_type_idx'),
            models.Index(fields=['email', '-created_at'], name='co_event_email_idx'),
        ]


def _loads(text):
    try:
        value = json.loads(text or '[]')
    except ValueError:
        return []
    return value if isinstance(value, list) else []


class CheckoutTraceChunk(BaseModel):
    """
    A batch of viewer samples for one check-out (see checkout/trace_report.py). ``moves`` are
    ``[ms_since_open, page, x, y]`` (x, y in 0..1000 across the page; page 0 = not over a page) and
    ``views`` are ``[ms_since_open, page, y]`` (the page, and the point of it, at the centre of the screen).
    Bounded per check-out (TRACE_MAX_CHUNKS), and only collected while the setting is on.
    """
    checkout = models.ForeignKey(Checkout, on_delete=models.CASCADE, related_name='trace_chunks')
    # Encrypted at rest (django-cryptography, key derived from SECRET_KEY): a database dump or backup
    # does not show where anyone read. Decrypted only to build a report for an administrator.
    # Stored as encrypted text holding JSON, not as an encrypted JSONField: on SQLite a JSON column is
    # constrained to valid JSON, which ciphertext is not, so every save would be refused.
    moves_data = encrypt(models.TextField(default='[]'))
    views_data = encrypt(models.TextField(default='[]'))
    clicks_data = encrypt(models.TextField(default='[]'))   # [ms, page, x, y, 0 = button down | 1 = button up]
    # The browser's clock when it sent this batch; with created_at it places the samples on the server's clock.
    client_now = models.BigIntegerField(null=True, blank=True)

    @property
    def moves(self):
        return _loads(self.moves_data)

    @moves.setter
    def moves(self, value):
        self.moves_data = json.dumps(value or [])

    @property
    def clicks(self):
        return _loads(self.clicks_data)

    @clicks.setter
    def clicks(self, value):
        self.clicks_data = json.dumps(value or [])

    @property
    def views(self):
        return _loads(self.views_data)

    @views.setter
    def views(self, value):
        self.views_data = json.dumps(value or [])

    class Meta:
        ordering = ['created_at']
        indexes = [models.Index(fields=['checkout', 'created_at'], name='co_trace_checkout_idx')]
