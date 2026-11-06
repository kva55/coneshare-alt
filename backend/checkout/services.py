"""Rules of /checkout.

Guests are assigned to a datastore (dataroom) by e-mail address. A guest asks for a sign-in link,
which is e-mailed (single use, 15 minutes, only valid for the /checkout page). Signed in, they see
the datastores they are assigned to and the files published there. Requesting a file starts a
check-out: immediately approved, or waiting for an admin when the file requires approval. Opening
an approved check-out starts its time window; during the window the guest views the file in the
app (pages painted into a canvas; there is no shareable link). Everything is limited per guest
and logged.
"""
import logging
from datetime import timedelta
from urllib.parse import urljoin

from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from backend.utils import get_client_ip
from core.mailer import send_app_mail
from security.services import code_request_limits, report_failed_auth

from . import hooks
from .events import EventType, record_event
from .models import (Checkout, CheckoutTraceChunk, CheckoutEvent, CheckoutFile, CheckoutGuest, CheckoutLoginLink,
                     CheckoutNdaAcceptance, CheckoutPolicy, CheckoutSession)

logger = logging.getLogger(__name__)

GENERIC_REQUEST_MESSAGE = "If this address has been given access, a sign-in link has been sent to it."
INVALID_LINK_MESSAGE = "This link is invalid or has expired."
SESSION_KEY = 'checkout_session_id'


def normalize_email(value) -> str:
    return (value or '').strip().lower()


def mask_email(email: str) -> str:
    local, _, domain = email.partition('@')
    return f"{local[:1]}***@{domain}"


def active_guest_policies(email: str):
    return CheckoutPolicy.objects.filter(
        enabled=True, guests__email=normalize_email(email), guests__revoked_at__isnull=True
    ).select_related('dataroom').distinct()


def _url(path: str) -> str:
    return urljoin(settings.SITE_DOMAIN, path)


# ----------------------------------------------------------------------------- sign-in link

def request_login_link(email: str, request) -> None:
    """Never reveals the outcome to the caller; everything is logged."""
    email = normalize_email(email)
    ip_address = get_client_ip(request) or None
    record_event(EventType.AUTH_REQUESTED, request, email)

    per_email, per_ip, window = code_request_limits()
    recent = CheckoutEvent.objects.filter(event_type=EventType.AUTH_REQUESTED, created_at__gte=timezone.now() - window)
    if recent.filter(email=email).count() > per_email or \
            (ip_address and recent.filter(ip_address=ip_address).count() > per_ip):
        record_event(EventType.AUTH_THROTTLED, request, email, metadata={'limit_per_email': per_email})
        return

    if not active_guest_policies(email).exists():
        # Say *why* in the log (the caller still gets the same answer): a guest of a datastore that is
        # switched off looks the same from outside as a stranger, but not to the admin.
        listed = CheckoutGuest.objects.filter(email=email, revoked_at__isnull=True).exists()
        record_event(EventType.AUTH_DENIED, request, email,
                     metadata={'reason': 'datastore_disabled' if listed else 'not_a_guest'})
        return

    _row, token = CheckoutLoginLink.issue(email, ip_address)
    delivery = 'sent'
    try:
        send_app_mail(
            subject="Your link to your files",
            text=render_to_string('checkout/login_link_email.txt', {
                'link': _url(f"/checkout#token={token}"), 'minutes': settings.CHECKOUT_LOGIN_LINK_MINUTES}),
            recipients=email,
        )
    except Exception:
        logger.exception("Failed to send checkout sign-in link")
        delivery = 'failed'
    record_event(EventType.LOGIN_LINK_SENT, request, email, metadata={'delivery': delivery})


def peek_login_link(token: str, request):
    """The masked address a link belongs to, or None. Does not use the link up, so mail scanners that
    open links cannot burn it; signing in needs an explicit POST."""
    link = CheckoutLoginLink.find(token)
    if link is None or not link.is_usable() or not active_guest_policies(link.email).exists():
        report_failed_auth('checkout_link', get_client_ip(request) or 'unknown', request)
        return None
    return mask_email(link.email)


def consume_login_link(token: str, request):
    """Sign in with a link (single use). Returns a new CheckoutSession or None."""
    with transaction.atomic():
        link = CheckoutLoginLink.objects.select_for_update().filter(
            token_hash=CheckoutLoginLink.hash_token(token or '')).first()
        if link is None or not link.is_usable() or not active_guest_policies(link.email).exists():
            record_event(EventType.AUTH_FAILED, request, link.email if link else '', metadata={'reason': 'invalid_link'})
            report_failed_auth('checkout_link', get_client_ip(request) or 'unknown', request)
            return None
        link.consumed_at = timezone.now()
        link.save(update_fields=['consumed_at', 'updated_at'])
    session = CheckoutSession.objects.create(
        email=link.email,
        expires_at=timezone.now() + timedelta(minutes=settings.CHECKOUT_SESSION_MINUTES),
        ip_address=get_client_ip(request) or None,
    )
    request.session[SESSION_KEY] = str(session.id)
    request.session.cycle_key()  # new privilege level -> new session key
    record_event(EventType.AUTH_GRANTED, request, link.email)
    return session


def get_session(request):
    """The active CheckoutSession of this browser session, or None. Re-validated on every call."""
    session_id = request.session.get(SESSION_KEY)
    if not session_id:
        return None
    session = CheckoutSession.objects.filter(id=session_id).first()
    if session is None or not session.is_active() or not active_guest_policies(session.email).exists():
        return None
    return session


def end_session(request) -> None:
    session = get_session(request)
    if session:
        session.ended_at = timezone.now()
        session.save(update_fields=['ended_at', 'updated_at'])
        record_event(EventType.SIGNED_OUT, request, session.email)
    request.session.pop(SESSION_KEY, None)


# ----------------------------------------------------------------------------- what a guest sees

def attempts_used(policy: CheckoutPolicy, email: str) -> int:
    """Requests this guest made under ``policy`` in the attempt window (signing in again does not reset it)."""
    since = timezone.now() - timedelta(hours=settings.CHECKOUT_ATTEMPT_WINDOW_HOURS)
    return Checkout.objects.filter(policy=policy, email=email, created_at__gte=since).count()


def nda_accepted(policy: CheckoutPolicy, email: str, entry: CheckoutFile = None) -> bool:
    """Has this guest accepted the datastore's NDA, when the file (or the datastore) asks for one?"""
    needed = entry.nda_needed() if entry is not None else policy.require_nda
    if not needed:
        return True
    return CheckoutNdaAcceptance.objects.filter(policy=policy, email=email, nda_version=policy.nda_version).exists()


def _file_state(email: str, entry: CheckoutFile):
    latest = Checkout.objects.filter(email=email, file=entry).order_by('-created_at').first()
    if latest is None:
        return 'available', None
    if latest.is_active():
        return ('open' if latest.status == Checkout.OPEN else latest.status), latest
    if latest.status == Checkout.DENIED and \
            latest.created_at + timedelta(hours=settings.CHECKOUT_REQUEST_VALID_HOURS) > timezone.now():
        return 'denied', latest
    return 'available', None


def available_datarooms(session: CheckoutSession):
    """Datastores the guest is assigned to, with their published files and the state of each."""
    rooms = []
    for policy in active_guest_policies(session.email):
        used = attempts_used(policy, session.email)
        accepted = CheckoutNdaAcceptance.objects.filter(
            policy=policy, email=session.email, nda_version=policy.nda_version).exists()
        files = []
        nda_files = 0
        for entry in policy.files.filter(visible=True).select_related('policy', 'dataroom_document__document').order_by('created_at'):
            nda_files += entry.nda_needed()
            doc = entry.dataroom_document
            version = doc.document.versions.filter(is_primary=True).first()
            state, checkout = _file_state(session.email, entry)
            files.append({
                'id': str(entry.id),
                'name': doc.name or doc.document.name,
                'size': version.file_size if version else None,
                'requires_approval': entry.requires_approval,
                'requires_nda': entry.nda_needed(),
                'allow_download': entry.download_allowed(),
                'state': state,
                'checkout_id': str(checkout.id) if checkout else None,
                'expires_at': checkout.expires_at if checkout and checkout.status == Checkout.OPEN else None,
            })
        rooms.append({
            'id': str(policy.id),
            'name': policy.dataroom.name,
            'checkout_minutes': policy.checkout_minutes,
            'attempts_remaining': max(policy.max_attempts - used, 0),
            'attempts_max': policy.max_attempts,
            'allow_download': policy.allow_download,
            'nda': {'required': bool(nda_files), 'accepted': accepted,
                    'text': policy.nda_text if nda_files and not accepted else ''},
            'files': files,
        })
    return rooms


def accept_nda(session: CheckoutSession, policy_id: str, request) -> bool:
    policy = active_guest_policies(session.email).filter(id=policy_id).first()
    if policy is None or not (policy.nda_text or '').strip():
        return False
    CheckoutNdaAcceptance.objects.get_or_create(
        policy=policy, email=session.email, nda_version=policy.nda_version,
        defaults={'ip_address': get_client_ip(request) or None})
    record_event(EventType.NDA_ACCEPTED, request, session.email, policy, {'version': policy.nda_version})
    return True


# ----------------------------------------------------------------------------- requests

class CheckoutRefused(Exception):
    def __init__(self, message, status_code=403, code=''):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _owner_email(entry: CheckoutFile) -> str:
    owner = entry.dataroom_document.document.created_by
    return owner.email if owner else ''


def _notify_target(policy: CheckoutPolicy, entry: CheckoutFile) -> str:
    if policy.notify_mode == CheckoutPolicy.NOTIFY_GROUP:
        return policy.notify_email
    if policy.notify_mode == CheckoutPolicy.NOTIFY_OWNER:
        return _owner_email(entry)
    return ''


def _approver_emails(policy: CheckoutPolicy, entry: CheckoutFile) -> list:
    """
    Who is told that a request waits for approval. This does not depend on the datastore's "notify"
    setting (which is about check-outs and may be 'log only'): a request that needs a decision must reach
    someone who can make it. That is the file's owner, the group address if one is set, and the organization's
    administrators when the owner is not one (only administrators can approve).
    """
    from django.contrib.auth import get_user_model
    owner = entry.dataroom_document.document.created_by
    found = []
    if owner and owner.email:
        found.append(owner.email)
    if policy.notify_mode == CheckoutPolicy.NOTIFY_GROUP and policy.notify_email:
        found.append(policy.notify_email)
    if not owner or getattr(owner, 'role', '') != 'admin':
        admins = get_user_model().objects.filter(organization_id=policy.organization_id, role='admin', is_active=True)
        found.extend(a.email for a in admins if a.email)
    unique = list(dict.fromkeys(e.strip().lower() for e in found if e))
    if not unique:
        logger.warning("Approval needed for %s but there is nobody to e-mail (no owner, group address or administrator)",
                       _file_name(entry))
    return unique


def _file_name(entry: CheckoutFile) -> str:
    return entry.dataroom_document.name or entry.dataroom_document.document.name


def _send(subject, template, context, recipient):
    if not recipient:
        return
    try:
        send_app_mail(subject=subject, text=render_to_string(template, context), recipients=recipient)
    except Exception:
        logger.exception("Failed to send checkout e-mail")


def request_file(session: CheckoutSession, file_id: str, request) -> Checkout:
    """Ask for a file. Re-asking while a request is still current costs no attempt."""
    entry = CheckoutFile.objects.select_related('policy__dataroom', 'dataroom_document__document').filter(id=file_id).first()
    policy = entry.policy if entry else None
    # Same answer for "does not exist" and "not for you", so ids cannot be probed.
    if entry is None or not entry.visible or not policy.has_active_guest(session.email):
        record_event(EventType.CHECKOUT_DENIED, request, session.email, policy, {'reason': 'not_available'})
        raise CheckoutRefused("This file is not available.", 404)
    if not nda_accepted(policy, session.email, entry):
        raise CheckoutRefused("Accept the non-disclosure agreement first.", 403, code='nda_required')

    name = _file_name(entry)
    payload = {'organization_id': str(policy.organization_id), 'email': session.email, 'file': name,
               'datastore': policy.dataroom.name, 'checkout_minutes': policy.checkout_minutes,
               'requires_approval': entry.requires_approval}
    with transaction.atomic():
        # Serialize requests per datastore so the attempt cap cannot be raced.
        CheckoutPolicy.objects.select_for_update().get(pk=policy.pk)
        state, current = _file_state(session.email, entry)
        if current is not None and state != 'denied':
            return current
        if state == 'denied':
            raise CheckoutRefused("This request was denied.", 403, code='denied')
        if attempts_used(policy, session.email) >= policy.max_attempts:
            record_event(EventType.CHECKOUT_DENIED, request, session.email, policy, {'reason': 'attempt_limit'})
            hooks.emit(hooks.CHECKOUT_DENIED, {**payload, 'reason': 'attempt_limit'})
            raise CheckoutRefused("You have used all of your requests for now.", 403, code='attempt_limit')
        checkout = Checkout.objects.create(
            session=session, policy=policy, file=entry, email=session.email,
            status=Checkout.PENDING if entry.requires_approval else Checkout.APPROVED,
            max_downloads=policy.downloads_per_checkout if entry.download_allowed() else 0,
            ip_address=get_client_ip(request) or None,
        )

    record_event(EventType.FILE_REQUESTED, request, session.email, policy, {'file': name})
    hooks.emit(hooks.FILE_REQUESTED, payload)
    if checkout.status == Checkout.PENDING:
        record_event(EventType.APPROVAL_REQUIRED, request, session.email, policy, {'file': name})
        hooks.emit(hooks.APPROVAL_REQUIRED, {**payload, 'checkout_id': str(checkout.id)})
        _send(f"Approval needed: {name}", 'checkout/approval_needed_email.txt',
              {'file_name': name, 'datastore_name': policy.dataroom.name, 'email': session.email,
               'url': _url('/check-out-center')}, _approver_emails(policy, entry))
    return checkout


def get_guest_checkout(session: CheckoutSession, checkout_id: str):
    checkout = Checkout.objects.select_related('policy__dataroom', 'file__dataroom_document__document').filter(
        id=checkout_id, email=session.email).first()
    if checkout is None or not checkout.policy.has_active_guest(session.email):
        raise CheckoutRefused("Check-out not found.", 404)
    return checkout


def open_checkout(session: CheckoutSession, checkout_id: str, request) -> Checkout:
    """Open an approved request: starts the time window. Opening an open one just returns it."""
    checkout = get_guest_checkout(session, checkout_id)
    policy, entry = checkout.policy, checkout.file
    if not entry.visible:
        raise CheckoutRefused("This file is not available.", 404)
    if not nda_accepted(policy, session.email, entry):
        raise CheckoutRefused("Accept the non-disclosure agreement first.", 403, code='nda_required')
    if checkout.status == Checkout.OPEN:
        if checkout.window_open():
            return checkout
        _end(checkout)
        raise CheckoutRefused("This check-out has ended. Request the file again.", 403, code='ended')
    if checkout.status == Checkout.PENDING:
        raise CheckoutRefused("This request is waiting for approval.", 409, code='pending')
    if checkout.status in (Checkout.DENIED, Checkout.ENDED):
        raise CheckoutRefused("This request is no longer open.", 403, code=checkout.status)
    if checkout.request_expired():
        _end(checkout)
        raise CheckoutRefused("This request has expired. Request the file again.", 403, code='expired')

    with transaction.atomic():
        checkout = Checkout.objects.select_for_update().get(pk=checkout.pk)
        if checkout.status != Checkout.APPROVED:
            raise CheckoutRefused("This request is no longer open.", 403)
        now = timezone.now()
        checkout.status = Checkout.OPEN
        checkout.opened_at = now
        checkout.expires_at = now + timedelta(minutes=policy.checkout_minutes)
        checkout.save(update_fields=['status', 'opened_at', 'expires_at', 'updated_at'])

    name = _file_name(entry)
    record_event(EventType.FILE_CHECKED_OUT, request, session.email, policy, {'file': name})
    hooks.emit(hooks.FILE_CHECKED_OUT, {
        'organization_id': str(policy.organization_id), 'email': session.email, 'file': name,
        'datastore': policy.dataroom.name, 'checkout_minutes': policy.checkout_minutes,
        'checkout_id': str(checkout.id)})
    _send(f"File checked out: {name}", 'checkout/checkout_notice_email.txt', {
        'file_name': name, 'datastore_name': policy.dataroom.name, 'email': session.email,
        'when': timezone.now().isoformat(timespec='seconds'), 'minutes': policy.checkout_minutes,
        'ip_address': checkout.ip_address or 'unknown'}, _notify_target(policy, entry))
    return checkout


def _end(checkout: Checkout) -> None:
    checkout.status = Checkout.ENDED
    checkout.expires_at = min(checkout.expires_at or timezone.now(), timezone.now())
    checkout.save(update_fields=['status', 'expires_at', 'updated_at'])


def return_checkout(session: CheckoutSession, checkout_id: str, request) -> Checkout:
    """The guest ends an open check-out early ("check in")."""
    checkout = get_guest_checkout(session, checkout_id)
    if checkout.status == Checkout.OPEN:
        viewed = int((timezone.now() - (checkout.opened_at or timezone.now())).total_seconds())
        _end(checkout)
        record_event(EventType.CHECKOUT_RETURNED, request, session.email, checkout.policy,
                     {'file': _file_name(checkout.file), 'seconds': max(viewed, 0)})
    return checkout


# ----------------------------------------------------------------------------- admin decisions

def decide(checkout: Checkout, approve: bool, admin, request) -> bool:
    """Approve or deny a pending request. Returns False if it was not pending any more."""
    with transaction.atomic():
        checkout = Checkout.objects.select_for_update().select_related(
            'policy__dataroom', 'file__dataroom_document__document').get(pk=checkout.pk)
        if checkout.status != Checkout.PENDING:
            return False
        checkout.status = Checkout.APPROVED if approve else Checkout.DENIED
        checkout.decided_by = admin
        checkout.decided_at = timezone.now()
        checkout.save(update_fields=['status', 'decided_by', 'decided_at', 'updated_at'])
    name = _file_name(checkout.file)
    record_event(EventType.REQUEST_APPROVED if approve else EventType.REQUEST_DENIED, request, checkout.email,
                 checkout.policy, {'file': name, 'by': admin.email})
    _send(f"Your request was {'approved' if approve else 'denied'}: {name}", 'checkout/decision_email.txt', {
        'decision': 'approved' if approve else 'denied', 'approved': approve, 'file_name': name,
        'url': _url('/checkout'), 'hours': settings.CHECKOUT_REQUEST_VALID_HOURS}, checkout.email)
    if not approve:
        hooks.emit(hooks.CHECKOUT_DENIED, {'organization_id': str(checkout.policy.organization_id),
                                           'email': checkout.email, 'file': name, 'reason': 'admin_denied'})
    return True


CLIENT_EVENTS_PER_MINUTE = 240


def record_viewer_event(session: CheckoutSession, checkout_id: str, event_type: str, metadata, request) -> bool:
    """
    A behaviour signal from the guest's viewer (print, save, screenshot key...). Accepted while the
    check-out is open and for two minutes after it ended, so a last key press is not lost.
    Returns False if it was dropped by the per-session limit.
    """
    from django.core.cache import cache
    checkout = get_guest_checkout(session, checkout_id)
    grace = checkout.expires_at and checkout.expires_at + timedelta(minutes=2) > timezone.now()
    if checkout.status not in (Checkout.OPEN, Checkout.ENDED) or not (checkout.window_open() or grace):
        raise CheckoutRefused("This check-out is not open.", 403)
    bucket = f"checkout-events:{request.session.session_key}:{checkout.id}"
    cache.add(bucket, 0, 60)
    if cache.incr(bucket) > CLIENT_EVENTS_PER_MINUTE:
        return False
    from security.capture import effective_actions, respond
    from security.services import snapshot
    action = effective_actions(snapshot()['capture_actions']).get(event_type, 'log')
    if action == 'ignore':
        return True
    record_event(event_type, request, session.email, checkout.policy,
                 {**(metadata or {}), 'file': _file_name(checkout.file), 'action': action,
                  'document': str(checkout.file.dataroom_document.document_id), 'checkout': str(checkout.id)})
    respond(action, request=request, event_type=event_type, who=session.email,
            where=f"{_file_name(checkout.file)} in {checkout.policy.dataroom.name}")
    if action == 'end' and checkout.status == Checkout.OPEN:
        _end(checkout)
    return True


TRACE_MAX_CHUNKS = 400            # per check-out
TRACE_MAX_SAMPLES = 600           # per request, per kind
TRACE_REQUESTS_PER_MINUTE = 20


def _clean_samples(rows, width, page_max):
    out = []
    for row in (rows if isinstance(rows, list) else [])[:TRACE_MAX_SAMPLES]:
        if not isinstance(row, (list, tuple)) or len(row) != width:
            continue
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in row):
            continue
        t, page = row[0], row[1]
        if not (0 <= t <= 2 ** 40) or not (0 <= page <= page_max):
            continue
        out.append([t, page] + [min(1000, max(0, v)) for v in row[2:]])
    return out


def record_trace(session: CheckoutSession, checkout_id: str, moves, views, request, clicks=None, now=None) -> bool:
    """
    Store a batch of cursor/page-in-view samples for the session report. Does nothing (returns False)
    when tracing is switched off in Security settings. Same window rule as viewer events.
    """
    from django.core.cache import cache
    from security.services import snapshot
    cfg = snapshot()
    if not cfg['monitoring_enabled'] or not cfg['checkout_tracing_enabled']:
        return False
    checkout = get_guest_checkout(session, checkout_id)
    grace = checkout.expires_at and checkout.expires_at + timedelta(minutes=2) > timezone.now()
    if checkout.status not in (Checkout.OPEN, Checkout.ENDED) or not (checkout.window_open() or grace):
        raise CheckoutRefused("This check-out is not open.", 403)
    bucket = f"checkout-trace:{request.session.session_key}:{checkout.id}"
    cache.add(bucket, 0, 60)
    if cache.incr(bucket) > TRACE_REQUESTS_PER_MINUTE:
        return False
    moves, views = _clean_samples(moves, 4, 5000), _clean_samples(views, 3, 5000)
    clicks = [c for c in _clean_samples(clicks, 5, 5000) if c[4] in (0, 1)]
    if not moves and not views and not clicks:
        return False
    client_now = now if isinstance(now, int) and not isinstance(now, bool) and 0 <= now <= 2 ** 40 else None
    if checkout.trace_chunks.count() >= TRACE_MAX_CHUNKS:
        return False
    CheckoutTraceChunk.objects.create(checkout=checkout, moves=moves, views=views, clicks=clicks, client_now=client_now)
    return True


def invite_guest(guest: CheckoutGuest, inviter) -> bool:
    try:
        send_app_mail(
            subject=f"You have been given access to files in {guest.policy.dataroom.name}",
            text=render_to_string('checkout/guest_invite_email.txt', {
                'inviter': inviter.email if inviter else 'An administrator',
                'datastore': guest.policy.dataroom.name, 'url': _url('/checkout')}),
            recipients=guest.email,
        )
        return True
    except Exception:
        logger.exception("Failed to send guest invitation")
        return False
