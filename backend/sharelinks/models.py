import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, models
from django.db.models import Q
from django.utils import timezone

from core.fields import ULIDField
from core.models import BaseModel, Organization, User
from django_cryptography.fields import encrypt

logger = logging.getLogger(__name__)


class ShareLinkTemplate(BaseModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='share_link_templates')
    name = models.CharField(max_length=255)
    is_default = models.BooleanField(default=False)
    expires_in_days = models.IntegerField(null=True, blank=True)
    requires_password = models.BooleanField(default=False)
    requires_email = models.BooleanField(default=False)
    requires_email_verification = models.BooleanField(default=False)
    allow_download = models.BooleanField(default=True)
    enable_watermark = models.BooleanField(default=False)
    watermark_text = models.CharField(max_length=255, blank=True)
    receive_email_notification = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class ShareLink(BaseModel):
    document = models.ForeignKey('documents.Document', on_delete=models.CASCADE, null=True, blank=True, related_name='share_links')
    dataroom = models.ForeignKey('datarooms.Dataroom', on_delete=models.CASCADE, null=True, blank=True, related_name='share_links')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='share_links_created')
    name = models.CharField(max_length=255, blank=True)
    slug = models.CharField(max_length=50, unique=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    password = encrypt(models.CharField(max_length=255, null=True, blank=True))
    requires_email = models.BooleanField(default=False)
    requires_email_verification = models.BooleanField(default=False)
    allow_download = models.BooleanField(default=True)
    enable_qna = models.BooleanField(default=True)
    enable_watermark = models.BooleanField(default=False)
    watermark_text = models.CharField(max_length=255, blank=True)
    receive_email_notification = models.BooleanField(default=False)
    # Experimental viewing protections. Client-side deterrents only: they make casual screen
    # capture harder and are logged/visible to the owner, but cannot stop a determined viewer.
    blur_when_unfocused = models.BooleanField(default=False)
    restricted_viewing_pane = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    require_nda = models.BooleanField(default=False)
    nda_text = models.TextField(blank=True, default='')
    nda_version = models.IntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(document__isnull=False, dataroom__isnull=True) |
                    Q(document__isnull=True, dataroom__isnull=False)
                ),
                name='sharelink_exactly_one_target'
            )
        ]

    def __str__(self):
        return f"{self.name}-{str(self.id)}"

    @property
    def organization(self):
        if self.dataroom_id:
            return self.dataroom.organization
        if self.document_id:
            return self.document.organization
        return None

    @property
    def organization_id(self):
        if self.dataroom_id:
            return self.dataroom.organization_id
        if self.document_id:
            return self.document.organization_id
        return None

    @property
    def qna_enabled(self) -> bool:
        """
        Effective Q&A availability for this link.

        A dataroom acts as the master switch: turning Q&A off there disables it
        for every link into that room. Individual links can only be more
        restrictive, never more permissive.
        """
        if not self.enable_qna:
            return False
        if self.dataroom_id:
            return self.dataroom.enable_qna
        return True

    def save(self, *args, **kwargs):
        if getattr(self, '_projected', False):
            # a link as seen by a viewer (with the access policy applied): saving it would write the policy into the link
            raise RuntimeError('Do not save a link that has the access policy applied.')
        if not self.slug:
            self.slug = secrets.token_urlsafe(16)
        if self.pk:
            update_fields = kwargs.get('update_fields')
            if update_fields is None or 'nda_text' in update_fields:
                old_data = ShareLink.objects.filter(pk=self.pk).values_list('nda_text', 'nda_version').first()
                if old_data and old_data[0] != self.nda_text:
                    self.nda_version = old_data[1] + 1
                    # Ensure nda_version is persisted during partial updates (e.g. PATCH)
                    # if nda_text is being explicitly saved via update_fields.
                    if update_fields is not None:
                        fields = set(update_fields)
                        fields.add('nda_version')
                        kwargs['update_fields'] = list(fields)
        super().save(*args, **kwargs)


class ShareLinkAllowedEmail(BaseModel):
    """
    One entry of a share link's allow-list. Only people on the list can request an access
    code. Entries are never deleted, only revoked, so the audit trail stays intact.
    """
    share_link = models.ForeignKey('ShareLink', on_delete=models.CASCADE, related_name='allowed_emails')
    email = models.EmailField()
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    invitation_sent_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(fields=['share_link', 'email'], name='unique_sharelink_allowed_email'),
        ]

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __str__(self):
        return f"{self.email} -> {self.share_link_id}"


class AccessPolicy(BaseModel):
    """
    Who may open a document (or a whole datastore) and how it is protected, independent of any share
    link. Every link to the target inherits it, and check-out can use it, so the rules exist from the
    moment the file is uploaded and do not depend on a link being created or shared.

    Links keep only what is about the link itself (name, expiry, notifications). When a link also has
    its own older settings, the stricter of the two applies.
    """
    document = models.OneToOneField('documents.Document', null=True, blank=True, on_delete=models.CASCADE,
                                    related_name='access_policy')
    dataroom = models.OneToOneField('datarooms.Dataroom', null=True, blank=True, on_delete=models.CASCADE,
                                    related_name='access_policy')
    password = encrypt(models.CharField(max_length=255, null=True, blank=True))
    requires_email = models.BooleanField(default=False)
    requires_email_verification = models.BooleanField(default=False)
    require_nda = models.BooleanField(default=False)
    nda_text = models.TextField(blank=True, default='')
    nda_version = models.IntegerField(default=1)
    allow_download = models.BooleanField(null=True, blank=True)       # False forbids it on every link
    enable_watermark = models.BooleanField(default=False)
    watermark_text = models.CharField(max_length=255, blank=True)
    blur_when_unfocused = models.BooleanField(default=False)
    restricted_viewing_pane = models.BooleanField(default=False)
    allowed_emails = models.JSONField(default=list, blank=True)       # normalised addresses
    allowed_domains = models.JSONField(default=list, blank=True)      # normalised domains
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(document__isnull=False, dataroom__isnull=True) | Q(document__isnull=True, dataroom__isnull=False)),
                name='accesspolicy_exactly_one_target',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            previous = AccessPolicy.objects.filter(pk=self.pk).values_list('nda_text', 'nda_version').first()
            if previous and previous[0] != self.nda_text:
                self.nda_version = previous[1] + 1
        super().save(*args, **kwargs)

    @property
    def has_access_rules(self) -> bool:
        return bool(self.allowed_emails or self.allowed_domains)


class ShareLinkAllowedDomain(BaseModel):
    """
    A domain-wide rule: anyone with an address at this domain may ask for access. Meant for links
    where no individual addresses are listed; they can still be used together (either matches).
    Revoked, never deleted.
    """
    share_link = models.ForeignKey('ShareLink', on_delete=models.CASCADE, related_name='allowed_domains')
    domain = models.CharField(max_length=253)
    invited_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(fields=['share_link', 'domain'], name='unique_sharelink_allowed_domain'),
        ]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class AccessRequestToken(BaseModel):
    """
    A one-time access code issued when someone on the allow-list asks for access.
    Only a keyed hash is stored; the code itself exists only in the email that was sent.
    """
    share_link = models.ForeignKey('ShareLink', on_delete=models.CASCADE, related_name='access_request_tokens')
    email = models.EmailField()
    token_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)

    # No look-alike characters (0/O, 1/I/L) so a code can be read out or typed reliably.
    CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
    CODE_LENGTH = 8

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['share_link', 'email', '-created_at'], name='sl_acctoken_lnk_em_idx')]

    @staticmethod
    def normalize_code(code: str) -> str:
        return ''.join(ch for ch in (code or '').upper() if ch.isalnum())

    @staticmethod
    def hash_code(share_link_id, email: str, code: str) -> str:
        message = f"{share_link_id}:{email.strip().lower()}:{AccessRequestToken.normalize_code(code)}"
        return hmac.new(settings.SECRET_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()

    @classmethod
    def issue(cls, share_link, email: str, ip_address=None):
        """Create a token for ``email`` (invalidating older ones); returns (token, raw_code)."""
        email = email.strip().lower()
        cls.objects.filter(share_link=share_link, email=email, consumed_at__isnull=True).update(
            consumed_at=timezone.now()
        )
        raw = ''.join(secrets.choice(cls.CODE_ALPHABET) for _ in range(cls.CODE_LENGTH))
        ttl = timedelta(minutes=getattr(settings, 'ACCESS_CODE_TTL_MINUTES', 10))
        token = cls.objects.create(
            share_link=share_link,
            email=email,
            token_hash=cls.hash_code(share_link.id, email, raw),
            expires_at=timezone.now() + ttl,
            requested_ip=ip_address or None,
        )
        return token, f"{raw[:4]}-{raw[4:]}"

    def is_usable(self) -> bool:
        return (
            self.consumed_at is None
            and self.expires_at > timezone.now()
            and self.failed_attempts < getattr(settings, 'ACCESS_CODE_MAX_ATTEMPTS', 5)
        )

    def matches(self, code: str) -> bool:
        candidate = self.hash_code(self.share_link_id, self.email, code)
        return hmac.compare_digest(self.token_hash, candidate)


class ShareLinkEvent(models.Model):
    """
    Append-only log of everything that happens around a share link: access attempts and
    requests (allowed / denied), code verification, invitations, and viewer behaviour
    reported by the viewer (print attempts, etc.). Event types live in sharelinks/events.py.
    """
    id = ULIDField(primary_key=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    share_link = models.ForeignKey('ShareLink', on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=40, db_index=True)
    email = models.EmailField(blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['share_link', '-created_at'], name='sl_event_link_time_idx'),
            models.Index(fields=['share_link', 'event_type', '-created_at'], name='sl_event_link_type_idx'),
        ]

    def __str__(self):
        return f"{self.event_type} on {self.share_link_id} at {self.created_at}"


class EmailVerificationToken(models.Model):
    """
    A temporary, single-use token to verify a viewer's email address
    for a share link.
    """
    id = ULIDField(primary_key=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    share_link = models.ForeignKey('ShareLink', on_delete=models.CASCADE, related_name='email_verification_tokens')
    email = models.EmailField()
    expires_at = models.DateTimeField()

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        if not self.expires_at:
            # Set expiry for 15 minutes from now.
            self.expires_at = timezone.now() + timedelta(minutes=15)
        super().save(*args, **kwargs)

    def is_expired(self):
        return self.expires_at < timezone.now()


class PreviewSession(BaseModel):
    """
    A temporary, single-use session for a user to preview a share link,
    bypassing its security settings.
    """
    token = models.CharField(max_length=64, unique=True, db_index=True)
    share_link = models.ForeignKey('ShareLink', on_delete=models.CASCADE, related_name='preview_sessions')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    expires_at = models.DateTimeField()

    def is_expired(self):
        return self.expires_at < timezone.now()


class ShareLinkDataroomSetting(BaseModel):
    share_link = models.ForeignKey('ShareLink', on_delete=models.CASCADE, related_name='dataroom_settings')
    dataroom_document = models.ForeignKey('datarooms.DataroomDocument', on_delete=models.CASCADE, null=True, blank=True)
    dataroom_folder = models.ForeignKey('datarooms.DataroomFolder', on_delete=models.CASCADE, null=True, blank=True)
    is_visible = models.BooleanField(default=True)
    allow_download = models.BooleanField(default=True)
    enable_watermark = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(dataroom_document__isnull=False, dataroom_folder__isnull=True) |
                    Q(dataroom_document__isnull=True, dataroom_folder__isnull=False)
                ),
                name='sharelinkdataroomsetting_exactly_one_target'
            ),
            models.UniqueConstraint(
                fields=['share_link', 'dataroom_document'],
                condition=Q(dataroom_document__isnull=False),
                name='unique_sharelink_dataroom_document'
            ),
            models.UniqueConstraint(
                fields=['share_link', 'dataroom_folder'],
                condition=Q(dataroom_folder__isnull=False),
                name='unique_sharelink_dataroom_folder'
            ),
        ]

    def save(self, *args, **kwargs):
        # Application-level guard against duplicate setting rows
        if self._state.adding or kwargs.get('force_insert'):
            qs = ShareLinkDataroomSetting.objects.filter(share_link_id=self.share_link_id)
            if self.dataroom_document_id and qs.filter(dataroom_document_id=self.dataroom_document_id).exists():
                raise IntegrityError("Duplicate setting for share link and dataroom document.")
            if self.dataroom_folder_id and qs.filter(dataroom_folder_id=self.dataroom_folder_id).exists():
                raise IntegrityError("Duplicate setting for share link and dataroom folder.")
        super().save(*args, **kwargs)

    def __str__(self):
        target = self.dataroom_document or self.dataroom_folder
        return f"dataroom-setting for {target}, perms: {self.is_visible}, {self.allow_download}, {self.enable_watermark}"


class DataroomVisit(models.Model):
    id = ULIDField(primary_key=True, editable=False)
    view_session = models.ForeignKey('ViewSession', on_delete=models.CASCADE, related_name='dataroom_visits')
    dataroom_document = models.ForeignKey('datarooms.DataroomDocument', on_delete=models.SET_NULL, null=True, blank=True)
    dataroom_folder = models.ForeignKey('datarooms.DataroomFolder', on_delete=models.SET_NULL, null=True, blank=True)

    # Point-in-time snapshot fields for audit trail preservation
    item_type = models.CharField(
        max_length=20,
        choices=[('document', 'Document'), ('folder', 'Folder')],
        default='document',
        blank=True
    )
    item_name = models.CharField(max_length=255, blank=True, default='')
    item_path = models.CharField(
        max_length=1024,
        blank=True,
        default='',
        help_text="Virtual folder path at time of visit, e.g. /Financials/2026"
    )
    document_type = models.CharField(max_length=50, blank=True, default='')

    visited_at = models.DateTimeField(default=timezone.now)
    downloaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['visited_at']
        constraints = [
            models.CheckConstraint(
                # This constraint allows a visit to point to a document OR a folder,
                # but not both. It also allows both to be NULL, which is what happens
                # when the target item is deleted and on_delete=SET_NULL is triggered.
                condition=~Q(dataroom_document__isnull=False, dataroom_folder__isnull=False),
                name='dataroomvisit_not_both_targets'
            )
        ]

    def save(self, *args, **kwargs):
        if not self.item_name:
            if self.dataroom_document_id:
                try:
                    ddoc = self.dataroom_document
                    if ddoc:
                        self.item_type = 'document'
                        self.item_name = ddoc.name or (ddoc.document.name if ddoc.document else '')
                        if not self.item_path:
                            self.item_path = ddoc.folder.get_full_path() if ddoc.folder else '/'
                        if not self.document_type and ddoc.document:
                            self.document_type = ddoc.document.type
                except Exception as e:
                    logger.warning(
                        "DataroomVisit.save: failed to populate snapshot for document_id=%s: %s",
                        self.dataroom_document_id, e
                    )
            elif self.dataroom_folder_id:
                try:
                    folder = self.dataroom_folder
                    if folder:
                        self.item_type = 'folder'
                        self.item_name = folder.name
                        if not self.item_path:
                            self.item_path = folder.get_full_path()
                except Exception as e:
                    logger.warning(
                        "DataroomVisit.save: failed to populate snapshot for folder_id=%s: %s",
                        self.dataroom_folder_id, e
                    )
        super().save(*args, **kwargs)


class ViewerQuerySet(models.QuerySet):
    def get_or_create(self, defaults=None, **kwargs):
        if 'email' in kwargs and isinstance(kwargs['email'], str):
            kwargs['email'] = kwargs['email'].strip().lower()
        return super().get_or_create(defaults=defaults, **kwargs)

    def get(self, *args, **kwargs):
        if 'email' in kwargs and isinstance(kwargs['email'], str):
            kwargs['email'] = kwargs['email'].strip().lower()
        return super().get(*args, **kwargs)

    def filter(self, *args, **kwargs):
        if 'email' in kwargs and isinstance(kwargs['email'], str):
            kwargs['email'] = kwargs['email'].strip().lower()
        return super().filter(*args, **kwargs)


class ViewerManager(models.Manager.from_queryset(ViewerQuerySet)):
    pass


class Viewer(models.Model):
    id = ULIDField(primary_key=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='viewers')
    email = models.EmailField()

    objects = ViewerManager()

    class Meta:
        unique_together = ('organization', 'email')

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class ViewSession(models.Model):
    id = ULIDField(primary_key=True, editable=False)
    share_link = models.ForeignKey('ShareLink', on_delete=models.CASCADE, related_name='view_sessions')
    viewer = models.ForeignKey(Viewer, on_delete=models.SET_NULL, null=True, blank=True, related_name='view_sessions')
    viewer_email = models.EmailField(blank=True, default='')
    # How viewer_email was obtained ('' = no email, or a session created before this field).
    # db_default lets an older image keep inserting rows after a rollback.
    email_verification_method = models.CharField(
        max_length=20, blank=True, default='', db_default='',
        choices=[('verified', 'Verified'), ('self_reported', 'Self-reported'),
                 ('owner', 'Owner'), ('account', 'Account')],
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    duration_seconds = models.IntegerField(default=0)
    completion_rate = models.FloatField(default=0.0)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(default=timezone.now)

    def save(self, *args, **kwargs):
        if self.viewer_email:
            self.viewer_email = self.viewer_email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"ViewSession {self.id} on {self.share_link}"

    class Meta:
        ordering = ['-viewed_at']
        indexes = [
            models.Index(fields=['share_link', '-viewed_at'], name='viewsession_link_viewed_idx'),
        ]


class QnAThread(BaseModel):
    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = (
        (STATUS_OPEN, "Open"),
        (STATUS_CLOSED, "Closed"),
    )

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="qna_threads")
    share_link = models.ForeignKey("ShareLink", on_delete=models.CASCADE, related_name="qna_threads")
    dataroom = models.ForeignKey("datarooms.Dataroom", on_delete=models.CASCADE, null=True, blank=True, related_name="qna_threads")
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, null=True, blank=True, related_name="qna_threads")
    dataroom_document = models.ForeignKey("datarooms.DataroomDocument", on_delete=models.CASCADE, null=True, blank=True, related_name="qna_threads")
    dataroom_folder = models.ForeignKey("datarooms.DataroomFolder", on_delete=models.CASCADE, null=True, blank=True, related_name="qna_threads")
    subject = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    created_by_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="qna_threads_created")
    created_by_viewer = models.ForeignKey("Viewer", on_delete=models.SET_NULL, null=True, blank=True, related_name="qna_threads_created")
    created_by_view_session = models.ForeignKey("ViewSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="qna_threads_created")

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        Q(document__isnull=False)
                        & Q(dataroom__isnull=True)
                        & Q(dataroom_document__isnull=True)
                        & Q(dataroom_folder__isnull=True)
                    )
                    | (
                        Q(document__isnull=True)
                        & Q(dataroom__isnull=False)
                        & Q(dataroom_document__isnull=True)
                        & Q(dataroom_folder__isnull=True)
                    )
                    | (
                        Q(document__isnull=True)
                        & Q(dataroom__isnull=False)
                        & Q(dataroom_document__isnull=False)
                        & Q(dataroom_folder__isnull=True)
                    )
                    | (
                        Q(document__isnull=True)
                        & Q(dataroom__isnull=False)
                        & Q(dataroom_document__isnull=True)
                        & Q(dataroom_folder__isnull=False)
                    )
                ),
                name="qna_thread_exactly_one_context",
            ),
            models.CheckConstraint(
                # Creator principals use SET_NULL so Q&A history can survive
                # user/session deletion. Enforce at most one live principal.
                condition=~(
                    Q(created_by_user__isnull=False)
                    & Q(created_by_view_session__isnull=False)
                ),
                name="qna_thread_at_most_one_creator_type",
            ),
        ]

    def __str__(self):
        return f"{self.subject[:80]} ({self.status})"


class QnAMessage(BaseModel):
    thread = models.ForeignKey(QnAThread, on_delete=models.CASCADE, related_name="messages")
    body = models.TextField()
    sent_by_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="qna_messages_sent")
    sent_by_viewer = models.ForeignKey("Viewer", on_delete=models.SET_NULL, null=True, blank=True, related_name="qna_messages_sent")
    sent_by_view_session = models.ForeignKey("ViewSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="qna_messages_sent")

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                # Sender principals use SET_NULL so message history can survive
                # user/session deletion. Enforce at most one live principal.
                condition=~(
                    Q(sent_by_user__isnull=False)
                    & Q(sent_by_view_session__isnull=False)
                ),
                name="qna_message_at_most_one_sender_type",
            ),
        ]

    def __str__(self):
        return f"Message on {self.thread_id}"


class PageView(models.Model):
    """
    Records a granular page view event within a single viewing session (ViewSession).
    """
    id = ULIDField(primary_key=True, editable=False)
    view_session = models.ForeignKey('ViewSession', on_delete=models.CASCADE, related_name='page_views')
    dataroom_visit = models.ForeignKey('DataroomVisit', on_delete=models.SET_NULL, null=True, blank=True, related_name='page_views')
    page_number = models.PositiveIntegerField()
    duration_seconds = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    media_type = models.CharField(max_length=20, default='document')

    # Video engagement metrics
    video_start_time = models.FloatField(null=True, blank=True)
    video_end_time = models.FloatField(null=True, blank=True)
    video_volume = models.IntegerField(null=True, blank=True)
    is_fullscreen = models.BooleanField(null=True, blank=True)
    playback_speed = models.FloatField(null=True, blank=True)

    # Markdown / Reading engagement metrics
    scroll_percentage = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"PageView {self.id} for ViewSession {self.view_session.id}, Page {self.page_number}"


class LinkClick(models.Model):
    id = ULIDField(primary_key=True, editable=False)
    view_session = models.ForeignKey('ViewSession', on_delete=models.CASCADE, related_name='link_clicks')
    dataroom_visit = models.ForeignKey('DataroomVisit', on_delete=models.SET_NULL, null=True, blank=True, related_name='link_clicks')
    url = models.TextField()
    page_number = models.PositiveIntegerField()
    clicked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['clicked_at']

    def __str__(self):
        return f"LinkClick on {self.url} (Session: {self.view_session_id}, Page: {self.page_number})"


class NDAAcceptance(models.Model):
    id = ULIDField(primary_key=True, editable=False)
    share_link = models.ForeignKey('ShareLink', on_delete=models.CASCADE, related_name='nda_acceptances')
    nda_version = models.IntegerField()
    view_session = models.ForeignKey('ViewSession', on_delete=models.SET_NULL, null=True, blank=True, related_name='nda_acceptances')
    viewer = models.ForeignKey('Viewer', on_delete=models.SET_NULL, null=True, blank=True, related_name='nda_acceptances')
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            # Enforce strict XOR: NDA acceptance must be scoped to either
            # a transient view_session (for anonymous viewers) or a persistent
            # viewer record (for email-verified viewers), but never both.
            # Setting both is prohibited to avoid database redundancy (as ViewSession
            # already links to Viewer) and to eliminate ambiguity between transient (session-scoped)
            # and persistent (identity-scoped) acceptances.
            models.CheckConstraint(
                condition=(
                    Q(view_session__isnull=False, viewer__isnull=True) |
                    Q(view_session__isnull=True, viewer__isnull=False)
                ),
                name='ndaacceptance_session_or_viewer'
            )
        ]

    def __str__(self):
        return f"NDAAcceptance {self.id} for ShareLink {self.share_link_id} (v{self.nda_version})"
