from backend.utils import get_client_ip
from sharelinks.events import sanitize_metadata

from .models import CheckoutEvent


class EventType:
    AUTH_REQUESTED = 'auth_requested'            # an e-mail asked for a sign-in link
    LOGIN_LINK_SENT = 'login_link_sent'          # ...it belongs to a guest, link mailed
    AUTH_DENIED = 'auth_denied'                  # ...it does not, nothing mailed
    AUTH_THROTTLED = 'auth_throttled'            # too many requests, ignored
    AUTH_GRANTED = 'auth_granted'                # link followed, signed in
    AUTH_FAILED = 'auth_failed'                  # invalid / expired / used link
    SIGNED_OUT = 'signed_out'
    NDA_ACCEPTED = 'nda_accepted'
    FILE_REQUESTED = 'file_requested'            # asked to check a file out
    APPROVAL_REQUIRED = 'approval_required'      # ...and an admin has to approve it
    REQUEST_APPROVED = 'request_approved'
    REQUEST_DENIED = 'request_denied'
    FILE_CHECKED_OUT = 'file_checked_out'        # opened: the time window started
    CHECKOUT_RETURNED = 'checkout_returned'      # the guest ended it early
    CHECKOUT_DENIED = 'checkout_denied'          # refused (limit reached / not available)
    FILE_DOWNLOADED = 'file_downloaded'
    DOWNLOAD_DENIED = 'download_denied'


def record_event(event_type, request=None, email='', policy=None, metadata=None):
    ip_address, user_agent = None, ''
    if request is not None:
        ip_address = get_client_ip(request) or None
        user_agent = (request.META.get('HTTP_USER_AGENT', '') or '')[:512]
    return CheckoutEvent.objects.create(
        policy=policy,
        event_type=event_type,
        email=(email or '').strip().lower(),
        ip_address=ip_address,
        user_agent=user_agent,
        metadata=sanitize_metadata(metadata),
    )
