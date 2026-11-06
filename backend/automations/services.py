"""Event fan-out: one event can send the "viewed" email and/or signed webhooks."""
import logging
import uuid

from django.core.cache import cache

from .constants import (INTERNAL_PAYLOAD_KEYS, VIEW_EMAIL_DEDUPE_SECONDS, VIEW_EMAIL_EVENTS,
                        WEBHOOK_EVENTS)
from .models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)


def public_payload(payload: dict) -> dict:
    """What a webhook receiver is told: the event data minus internal identifiers."""
    return {k: v for k, v in payload.items() if k not in INTERNAL_PAYLOAD_KEYS}


def dispatch_webhooks(event_type: str, payload: dict) -> int:
    """Create + queue one delivery per active endpoint of the organization subscribed to the event."""
    if event_type not in WEBHOOK_EVENTS:
        return 0
    organization_id = payload.get('organization_id')
    if not organization_id:
        logger.warning('Webhook event dropped: missing organization_id for event=%s', event_type)
        return 0

    from .tasks import deliver_webhook_task  # local: tasks imports this module
    created = 0
    for endpoint in WebhookEndpoint.objects.filter(organization_id=organization_id, is_active=True):
        if event_type not in (endpoint.events or []):
            continue
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint, event_type=event_type, payload=public_payload(payload),
            idempotency_key=str(uuid.uuid4()),
        )
        deliver_webhook_task.delay(str(delivery.id))
        created += 1
    return created


def send_view_email(event_type: str, payload: dict) -> bool:
    """"Email me when someone views my document": only for links that switched it on."""
    if event_type not in VIEW_EMAIL_EVENTS:
        return False
    from core.mailer import send_app_mail
    from core.models import User
    from sharelinks.models import ShareLink

    link = ShareLink.objects.select_related('document', 'dataroom').filter(id=payload.get('share_link_id')).first()
    if link is None or not link.receive_email_notification:
        return False
    recipient = User.objects.filter(id=payload.get('owner_user_id'), is_active=True).first()
    if recipient is None or not recipient.email:
        return False

    visitor = (payload.get('viewer_email') or payload.get('ip_address') or 'anonymous')
    key = f"viewmail:{link.id}:{event_type}:{visitor}"
    if not cache.add(key, 1, VIEW_EMAIL_DEDUPE_SECONDS):
        return False

    target = payload.get('document_name') or payload.get('dataroom_name') or 'your shared item'
    verb = {'document_viewed': 'viewed', 'dataroom_opened': 'opened', 'document_downloaded': 'downloaded'}[event_type]
    lines = [f"Someone {verb} \"{target}\".", '']
    if payload.get('viewer_email'):
        lines.append(f"Viewer:  {payload['viewer_email']}")
    for label, key_name in (('Location', 'location'), ('Link', 'share_link_name')):
        if payload.get(key_name):
            lines.append(f"{label}:  {payload[key_name]}")
    try:
        send_app_mail(subject=f"Your {('document' if link.document_id else 'dataroom')} was {verb}: {target}",
                      text='\n'.join(lines), recipients=recipient.email)
    except Exception:
        logger.exception('Failed to send view notification email')
        return False
    return True


def dispatch_automation_event(event_type: str, payload: dict) -> int:
    """Entry point used by the Celery task. Returns the number of webhook deliveries created."""
    send_view_email(event_type, payload)
    return dispatch_webhooks(event_type, payload)
