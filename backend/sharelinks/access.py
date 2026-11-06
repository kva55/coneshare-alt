"""Allow-list authentication for share links.

The flow:

1. The owner adds email addresses to a link's allow-list; each gets an *invitation* email
   (a notice with the link - it grants nothing by itself).
2. A visitor opens the link and enters an email address. The response is ALWAYS the same
   generic message, so it can't be used to discover who is on the list. Only if the address
   is on the list is a one-time *access code* mailed to it.
3. The visitor enters the code. It is single-use, short-lived and limited to a few attempts.
   On success the session is authorized as that email address (and that identity is what is
   watermarked and logged).

Every step is written to the share-link event log. The owner and signed-in collaborators
are exempt (their session is marked ``email_method`` 'owner' / 'account').
"""
import logging
from datetime import timedelta
from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.template.loader import render_to_string
from django.utils import timezone

from backend.utils import get_client_ip
import re

from core.mailer import send_app_mail
from security.services import code_request_limits, report_failed_auth

from .events import EventType, record_event
from .models import (AccessRequestToken, ShareLinkAllowedDomain, ShareLinkAllowedEmail, ShareLinkEvent,
                     Viewer)

logger = logging.getLogger(__name__)

GENERIC_REQUEST_MESSAGE = (
    "An email you provided has requested access. "
    "If that address is authorized, a one-time access code has been sent to it."
)
INVALID_CODE_MESSAGE = "Invalid or expired access code."

EXEMPT_EMAIL_METHODS = ('owner', 'account')


def requires_allowlist(link) -> bool:
    """
    Whether viewers of ``link`` must prove an address on its access rules.

    A link is restricted as soon as it has any rule: an individual address or a domain-wide rule
    (revoked ones count, so revoking the last entry never turns the link public). A link with no
    rules is open: no email is asked for. ``SHARELINK_REQUIRE_ALLOWLIST=True`` is the strict mode
    in which every link must be restricted, whatever it lists.
    """
    if getattr(settings, 'SHARELINK_REQUIRE_ALLOWLIST', False):
        return True
    has_rules = getattr(link, '_has_access_rules', None)
    if has_rules is None:
        policy = getattr(link, '_access_policy', None)
        has_rules = (link.allowed_emails.exists() or link.allowed_domains.exists()
                     or bool(policy and policy.has_access_rules))
        link._has_access_rules = has_rules
    return has_rules


def normalize_email(value) -> str:
    return (value or '').strip().lower()


def parse_emails(values):
    """Split a list of strings into (valid unique normalized emails, invalid originals)."""
    valid, invalid, seen = [], [], set()
    for raw in values or []:
        for piece in str(raw).replace(';', ',').replace('\n', ',').split(','):
            piece = piece.strip()
            if not piece:
                continue
            email = normalize_email(piece)
            try:
                validate_email(email)
            except ValidationError:
                invalid.append(piece)
                continue
            if email not in seen:
                seen.add(email)
                valid.append(email)
    return valid, invalid


DOMAIN_RE = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$')


def normalize_domain(value):
    """``'@Example.com '`` -> ``'example.com'``; None if it is not a plain domain (no wildcards)."""
    domain = str(value or '').strip().lower().lstrip('@')
    return domain if DOMAIN_RE.match(domain) else None


def split_entries(values):
    """
    Split what an owner typed into (emails, domains, invalid). ``a@b.com`` is an email;
    ``@b.com`` or ``b.com`` is a domain-wide rule.
    """
    emails, domains, invalid = [], [], []
    for raw in values or []:
        for piece in str(raw).replace(';', ',').replace('\n', ',').replace(' ', ',').split(','):
            piece = piece.strip()
            if not piece:
                continue
            if '@' in piece and not piece.startswith('@'):
                good, bad = parse_emails([piece])
                emails += [e for e in good if e not in emails]
                invalid += bad
            else:
                domain = normalize_domain(piece)
                if domain is None:
                    invalid.append(piece)
                elif domain not in domains:
                    domains.append(domain)
    return emails, domains, invalid


def domain_of(email: str) -> str:
    return normalize_email(email).rsplit('@', 1)[-1] if '@' in email else ''


def is_allowed(link, email: str) -> bool:
    """On the individual list, or covered by an active domain-wide rule."""
    email = normalize_email(email)
    if get_active_entry(link, email) is not None:
        return True
    domain = domain_of(email)
    policy = getattr(link, '_access_policy', None)   # rules set on the document / datastore itself
    if policy and (email in policy.allowed_emails or (domain and domain in policy.allowed_domains)):
        return True
    return bool(domain) and ShareLinkAllowedDomain.objects.filter(
        share_link=link, domain=domain, revoked_at__isnull=True).exists()


def get_active_entry(link, email: str):
    return ShareLinkAllowedEmail.objects.filter(
        share_link=link, email=normalize_email(email), revoked_at__isnull=True
    ).first()


def session_satisfies_allowlist(link, auth_status: dict) -> bool:
    """True if this session may pass the allow-list gate of ``link`` (re-checked on every request)."""
    auth_status = auth_status or {}
    if auth_status.get('email_method') in EXEMPT_EMAIL_METHODS:
        return True
    if not auth_status.get('allowlist_verified'):
        return False
    # Re-check the list each time so revoking an entry takes effect immediately.
    return is_allowed(link, auth_status.get('viewer_email', ''))


def _target_name(link) -> str:
    return link.document.name if link.document_id else link.dataroom.name


def _view_url(link) -> str:
    return urljoin(settings.SITE_DOMAIN, f"/view/{link.slug}")


def send_invitation(entry: ShareLinkAllowedEmail, invited_by=None, request=None) -> bool:
    """Mail the invitation notice; returns whether it was sent."""
    link = entry.share_link
    context = {
        'target_name': _target_name(link),
        'view_url': _view_url(link),
        'inviter': (invited_by.name or invited_by.email) if invited_by else '',
        'email': entry.email,
    }
    try:
        send_app_mail(
            subject=f"You have been given access to '{context['target_name']}'",
            text=render_to_string('sharelinks/invitation_email.txt', context),
            html=render_to_string('sharelinks/invitation_email.html', context),
            recipients=entry.email,
        )
    except Exception:
        logger.exception("Failed to send invitation for share link %s", link.id)
        return False
    entry.invitation_sent_at = timezone.now()
    entry.save(update_fields=['invitation_sent_at', 'updated_at'])
    record_event(link, EventType.INVITATION_SENT, request, entry.email,
                 {'invited_by': invited_by.email if invited_by else ''})
    return True


def add_allowed_emails(link, emails, invited_by=None, request=None) -> dict:
    """Add (or reinstate) emails and send invitations. Returns lists of entries by outcome."""
    added, reinstated, existing = [], [], []
    for email in emails:
        entry, created = ShareLinkAllowedEmail.objects.get_or_create(
            share_link=link, email=email, defaults={'invited_by': invited_by}
        )
        if created:
            added.append(entry)
        elif entry.revoked_at is not None:
            entry.revoked_at = None
            entry.invited_by = invited_by or entry.invited_by
            entry.save(update_fields=['revoked_at', 'invited_by', 'updated_at'])
            reinstated.append(entry)
        else:
            existing.append(entry)
    for entry in added + reinstated:
        send_invitation(entry, invited_by, request)
    return {'added': added, 'reinstated': reinstated, 'existing': existing}


def add_allowed_domains(link, domains, invited_by=None, request=None) -> dict:
    """Add (or reinstate) domain-wide rules. No invitations: there is no individual to write to."""
    added, reinstated, existing = [], [], []
    for domain in domains:
        entry, created = ShareLinkAllowedDomain.objects.get_or_create(
            share_link=link, domain=domain, defaults={'invited_by': invited_by})
        if created:
            added.append(entry)
        elif entry.revoked_at is not None:
            entry.revoked_at = None
            entry.save(update_fields=['revoked_at', 'updated_at'])
            reinstated.append(entry)
        else:
            existing.append(entry)
    for entry in added + reinstated:
        record_event(link, EventType.DOMAIN_ADDED, request, f"@{entry.domain}")
    return {'added': added, 'reinstated': reinstated, 'existing': existing}


def revoke_allowed_domain(entry: ShareLinkAllowedDomain, request=None) -> None:
    if entry.revoked_at is not None:
        return
    entry.revoked_at = timezone.now()
    entry.save(update_fields=['revoked_at', 'updated_at'])
    # Codes already issued to addresses that were only covered by this rule are void.
    AccessRequestToken.objects.filter(
        share_link=entry.share_link, email__iendswith=f"@{entry.domain}", consumed_at__isnull=True
    ).update(consumed_at=timezone.now())
    record_event(entry.share_link, EventType.ACCESS_REVOKED, request, f"@{entry.domain}")


def revoke_allowed_email(entry: ShareLinkAllowedEmail, request=None) -> None:
    if entry.revoked_at is not None:
        return
    entry.revoked_at = timezone.now()
    entry.save(update_fields=['revoked_at', 'updated_at'])
    AccessRequestToken.objects.filter(
        share_link=entry.share_link, email=entry.email, consumed_at__isnull=True
    ).update(consumed_at=timezone.now())
    record_event(entry.share_link, EventType.ACCESS_REVOKED, request, entry.email)


def _send_access_code(link, email: str, code: str) -> None:
    context = {
        'target_name': _target_name(link),
        'view_url': _view_url(link),
        'code': code,
        'ttl_minutes': getattr(settings, 'ACCESS_CODE_TTL_MINUTES', 10),
    }
    send_app_mail(
        subject=f"Your access code for '{context['target_name']}'",
        text=render_to_string('sharelinks/access_code_email.txt', context),
        html=render_to_string('sharelinks/access_code_email.html', context),
        recipients=email,
    )


def request_access_code(link, email: str, request) -> None:
    """
    Handle "this email asks for access". Never reveals the outcome to the caller: the view
    always answers with ``GENERIC_REQUEST_MESSAGE``. The outcome is only written to the log.
    """
    email = normalize_email(email)
    ip_address = get_client_ip(request) or None
    record_event(link, EventType.ACCESS_REQUEST, request, email)

    # Rate limits (the request just recorded is included in the counts).
    per_email_limit, per_ip_limit, window = code_request_limits()
    since = timezone.now() - window
    requests = ShareLinkEvent.objects.filter(
        share_link=link, event_type=EventType.ACCESS_REQUEST, created_at__gte=since
    )
    per_email = requests.filter(email=email).count()
    per_ip = requests.filter(ip_address=ip_address).count() if ip_address else 0
    if per_email > per_email_limit or per_ip > per_ip_limit:
        record_event(link, EventType.ACCESS_REQUEST_THROTTLED, request, email)
        return

    if not is_allowed(link, email):
        record_event(link, EventType.ACCESS_REQUEST_DENIED, request, email, {'reason': 'not_on_allow_list'})
        return

    _token, code = AccessRequestToken.issue(link, email, ip_address)
    delivery = 'sent'
    try:
        _send_access_code(link, email, code)
    except Exception:
        logger.exception("Failed to send access code for share link %s", link.id)
        delivery = 'failed'
    record_event(link, EventType.ACCESS_REQUEST_ALLOWED, request, email, {'delivery': delivery})


def verify_access_code(link, email: str, code: str, request) -> bool:
    """Check a submitted code. Consumes it on success; counts failures against the token."""
    email = normalize_email(email)
    token = AccessRequestToken.objects.filter(share_link=link, email=email).order_by('-created_at').first()
    if token is None or not token.is_usable() or not is_allowed(link, email):
        record_event(link, EventType.ACCESS_CODE_INVALID, request, email, {'reason': 'no_valid_code'})
        report_failed_auth('share_code', email, request)
        return False
    if not token.matches(code):
        token.failed_attempts += 1
        token.save(update_fields=['failed_attempts', 'updated_at'])
        record_event(link, EventType.ACCESS_CODE_INVALID, request, email, {'reason': 'wrong_code'})
        report_failed_auth('share_code', email, request)
        return False
    token.consumed_at = timezone.now()
    token.save(update_fields=['consumed_at', 'updated_at'])
    return True


def grant_session_access(request, link, email: str) -> None:
    """Mark the viewer's session as authorized for ``link`` as ``email``."""
    email = normalize_email(email)
    organization = link.organization
    if organization is not None:
        Viewer.objects.get_or_create(organization=organization, email=email)

    authorized = dict(request.session.get('authorized_share_links', {}))
    state = dict(authorized.get(str(link.id), {}))
    state.update({
        'allowlist_verified': True,
        'email_verified': True,
        'viewer_email': email,
        'email_method': 'allowlist_code',
    })
    state.setdefault('password_verified', False)
    state.setdefault('nda_accepted_version', 0)
    authorized[str(link.id)] = state
    request.session['authorized_share_links'] = authorized
    # New privilege level -> new session key (prevents session fixation).
    request.session.cycle_key()
    record_event(link, EventType.ACCESS_GRANTED, request, email)
