import json
import logging
import socket
from datetime import timedelta

from celery import shared_task
from django.utils import timezone
from rest_framework import serializers

from core.ssrf import (DestinationResolutionError, OutboundRequestError, pinned_request,
                       validate_outbound_url)

from .constants import (BASE_RETRY_SECONDS, MAX_DELIVERY_ATTEMPTS, RESPONSE_EXCERPT_LIMIT)
from .crypto import sign_body
from .models import WebhookDelivery
from .services import dispatch_automation_event

logger = logging.getLogger(__name__)


@shared_task
def dispatch_automation_event_task(event_type: str, payload: dict):
    return dispatch_automation_event(event_type, payload)


def build_request(delivery: WebhookDelivery):
    """(body_bytes, headers) for a delivery: JSON body signed with the endpoint's derived secret."""
    body = json.dumps({
        'id': delivery.idempotency_key,
        'event': delivery.event_type,
        'created_at': delivery.created_at.isoformat(),
        'data': delivery.payload,
    }, sort_keys=True, separators=(',', ':')).encode()
    return body, {
        'Content-Type': 'application/json',
        'X-Coneshare-Event': delivery.event_type,
        'X-Coneshare-Idempotency-Key': delivery.idempotency_key,
        'X-Coneshare-Signature': sign_body(delivery.endpoint.signing_secret, body),
    }


def _finish(delivery, *, status, message='', response_code=None, retry_in=None):
    delivery.attempt_count += 1
    delivery.status = status
    delivery.response_code = response_code
    delivery.response_body_excerpt = (message or '')[:RESPONSE_EXCERPT_LIMIT]
    delivery.next_retry_at = timezone.now() + timedelta(seconds=retry_in) if retry_in else None
    if status == WebhookDelivery.Status.SUCCESS:
        delivery.delivered_at = timezone.now()
    delivery.save()


def _fail(delivery, message, response_code=None, retryable=True):
    if not retryable or delivery.attempt_count + 1 >= MAX_DELIVERY_ATTEMPTS:
        _finish(delivery, status=WebhookDelivery.Status.DEAD_LETTER, message=message, response_code=response_code)
        return
    countdown = BASE_RETRY_SECONDS * (2 ** delivery.attempt_count)
    _finish(delivery, status=WebhookDelivery.Status.FAILED, message=message, response_code=response_code,
            retry_in=countdown)
    deliver_webhook_task.apply_async(args=[str(delivery.id)], countdown=countdown)


@shared_task
def deliver_webhook_task(delivery_id: str):
    delivery = WebhookDelivery.objects.select_related('endpoint').filter(id=delivery_id).first()
    if delivery is None or delivery.status in (WebhookDelivery.Status.SUCCESS, WebhookDelivery.Status.DEAD_LETTER):
        return
    if not delivery.endpoint.is_active:
        _fail(delivery, 'Endpoint is disabled.', retryable=False)
        return

    try:
        url = validate_outbound_url(delivery.endpoint.url)
    except (DestinationResolutionError, socket.gaierror):
        _fail(delivery, 'DNS resolution failed for destination host.')
        return
    except Exception:
        _fail(delivery, 'Invalid destination URL or blocked address.', retryable=False)
        return

    body, headers = build_request(delivery)
    try:
        # pinned_request re-resolves and connects only to a validated address (DNS-rebinding safe).
        response = pinned_request('POST', url, body=body, headers=headers, timeout=10)
    except DestinationResolutionError:
        _fail(delivery, 'DNS resolution failed for destination host.')
        return
    except serializers.ValidationError:
        _fail(delivery, 'Destination resolved to a blocked address.', retryable=False)
        return
    except OutboundRequestError as exc:
        _fail(delivery, f'Request failed: {exc}')
        return

    if 200 <= response.status_code < 300:
        _finish(delivery, status=WebhookDelivery.Status.SUCCESS, message=response.text,
                response_code=response.status_code)
    else:
        _fail(delivery, f'HTTP {response.status_code}: {response.text}', response.status_code)
