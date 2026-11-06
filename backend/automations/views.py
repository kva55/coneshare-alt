import logging
import secrets

from django.core.cache import cache

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.mailer import check_inbox_for_token, send_app_mail
from core.pagination import StandardResultsSetPagination
from core.permissions import IsAdmin

from .constants import WEBHOOK_EVENTS
from .models import EmailSettings, WebhookDelivery, WebhookEndpoint
from .serializers import (SETTINGS_FIELDS, EmailSettingsSerializer, WebhookDeliverySerializer,
                          WebhookEndpointSerializer)
from .tasks import deliver_webhook_task

logger = logging.getLogger(__name__)


def _endpoint(request, endpoint_id):
    return get_object_or_404(WebhookEndpoint, id=endpoint_id, organization=request.user.organization)


@extend_schema(tags=['automations'])
class WebhookEventsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response({'events': [{'name': k, 'description': v} for k, v in WEBHOOK_EVENTS.items()]})


@extend_schema(tags=['automations'])
class WebhookListCreateView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(responses={200: WebhookEndpointSerializer(many=True)})
    def get(self, request):
        endpoints = WebhookEndpoint.objects.filter(organization=request.user.organization)
        return Response({'results': WebhookEndpointSerializer(endpoints, many=True).data})

    @extend_schema(request=WebhookEndpointSerializer, responses={201: dict, 400: dict})
    def post(self, request):
        serializer = WebhookEndpointSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        endpoint = serializer.save(organization=request.user.organization, created_by=request.user)
        # The signing secret is shown here so the receiver can be configured; it can be revealed again later.
        return Response({**WebhookEndpointSerializer(endpoint).data, 'signing_secret': endpoint.signing_secret},
                        status=status.HTTP_201_CREATED)


@extend_schema(tags=['automations'])
class WebhookDetailView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(request=WebhookEndpointSerializer, responses={200: WebhookEndpointSerializer})
    def patch(self, request, endpoint_id):
        endpoint = _endpoint(request, endpoint_id)
        serializer = WebhookEndpointSerializer(endpoint, data=request.data, partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(responses={204: None})
    def delete(self, request, endpoint_id):
        _endpoint(request, endpoint_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['automations'])
class WebhookSecretView(APIView):
    """Reveal the derived signing secret (GET) or rotate it (POST)."""
    permission_classes = [IsAdmin]

    @extend_schema(responses={200: dict})
    def get(self, request, endpoint_id):
        return Response({'signing_secret': _endpoint(request, endpoint_id).signing_secret})

    @extend_schema(request=None, responses={200: dict})
    def post(self, request, endpoint_id):
        endpoint = _endpoint(request, endpoint_id)
        endpoint.secret_version += 1
        endpoint.save(update_fields=['secret_version', 'updated_at'])
        return Response({'signing_secret': endpoint.signing_secret})


@extend_schema(tags=['automations'])
class WebhookTestView(APIView):
    """Queue a signed ``test`` event to the endpoint."""
    permission_classes = [IsAdmin]

    @extend_schema(request=None, responses={202: dict})
    def post(self, request, endpoint_id):
        endpoint = _endpoint(request, endpoint_id)
        import uuid
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint, event_type='test', payload={'message': 'This is a test event.'},
            idempotency_key=str(uuid.uuid4()),
        )
        transaction.on_commit(lambda: deliver_webhook_task.delay(str(delivery.id)))
        return Response({'delivery_id': str(delivery.id)}, status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=['automations'])
class WebhookDeliveriesView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(responses={200: WebhookDeliverySerializer(many=True)})
    def get(self, request, endpoint_id):
        endpoint = _endpoint(request, endpoint_id)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(endpoint.deliveries.all(), request, view=self)
        return paginator.get_paginated_response(WebhookDeliverySerializer(page, many=True).data)


@extend_schema(tags=['automations'])
class EmailSettingsView(APIView):
    """E-mail setup shown in the admin panel. The password is write-only (blank keeps it)."""
    permission_classes = [IsAdmin]

    def _body(self, config):
        from django.conf import settings
        return {
            **EmailSettingsSerializer(config).data,
            'source': 'database' if (config.pk and config.enabled) else 'environment',
            'environment_configured': bool(settings.EMAIL_HOST),
            'default_from_email': settings.DEFAULT_FROM_EMAIL,
        }

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(self._body(EmailSettings.load()))

    @extend_schema(request=EmailSettingsSerializer, responses={200: dict, 400: dict})
    def put(self, request):
        config = EmailSettings.load()
        serializer = EmailSettingsSerializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        password = serializer.validated_data.pop('password', None)
        imap_password = serializer.validated_data.pop('imap_password', None)
        before = {f: getattr(config, f) for f in SETTINGS_FIELDS}
        for field, value in serializer.validated_data.items():
            setattr(config, field, value)
        changed = sorted(f for f in SETTINGS_FIELDS if before[f] != getattr(config, f))
        if password:  # blank or missing keeps the stored password
            config.set_password(password)
            changed.append('password')
        if imap_password:
            config.set_imap_password(imap_password)
            changed.append('imap_password')
        config.updated_by = request.user
        config.save()
        if changed:  # who changed which setting; never the values
            from security.models import SecurityEvent
            from security.services import record_security_event
            record_security_event(SecurityEvent.KIND_SETTINGS, request, rule='mail_settings',
                                  actor=request.user.email, detail={'changed': changed})
        return Response(self._body(config))


def _throttled(name: str, limit: int = 3) -> bool:
    cache.add(f'mailtest:{name}', 0, 60)
    return cache.incr(f'mailtest:{name}') > limit


@extend_schema(tags=['automations'])
class EmailReceiveTestView(APIView):
    """
    Confirm that mail really arrives: send a message with a random token in the subject to the
    configured test address, then watch the configured inbox for it (up to ~20 s) and delete just
    that message. Needs the inbox settings. Three tries a minute.
    """
    permission_classes = [IsAdmin]

    @extend_schema(request=None, responses={200: dict, 400: dict, 429: dict})
    def post(self, request):
        if _throttled('receive'):
            return Response({'detail': 'Slow down: three tests a minute.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        config = EmailSettings.load()
        if not config.imap_host or not config.imap_test_address:
            return Response({'detail': 'Set the inbox host and the test address first.'}, status=status.HTTP_400_BAD_REQUEST)
        token = f"receive-test-{secrets.token_hex(6)}"
        try:
            send_app_mail(f'Coneshare {token}', 'This message checks that mail can be received. It is deleted automatically.',
                          config.imap_test_address)
        except Exception as exc:
            logger.warning('Receive test: sending failed: %s', type(exc).__name__)
            return Response({'detail': f'Sending failed ({type(exc).__name__}). Check the sending settings.', 'stage': 'send'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            found, seconds = check_inbox_for_token(config, token)
        except Exception as exc:
            logger.warning('Receive test: inbox check failed: %s', type(exc).__name__)
            return Response({'detail': f'Could not read the inbox ({type(exc).__name__}). Check host, port, encryption and credentials.',
                             'stage': 'receive'}, status=status.HTTP_400_BAD_REQUEST)
        if not found:
            return Response({'detail': f'The message was sent but did not arrive within {seconds}s.', 'stage': 'arrival'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': f'Sent and received in {seconds}s. The test message was deleted.', 'seconds': seconds})


@extend_schema(tags=['automations'])
class EmailTestView(APIView):
    """Send a test message to the signed-in admin's own address (never an arbitrary recipient)."""
    permission_classes = [IsAdmin]

    @extend_schema(request=None, responses={200: dict, 400: dict})
    def post(self, request):
        if not request.user.email:
            return Response({'detail': 'Your account has no email address.'}, status=status.HTTP_400_BAD_REQUEST)
        if _throttled('send', 5):
            return Response({'detail': 'Slow down: five tests a minute.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        try:
            send_app_mail('Test message', 'Your email settings work.', request.user.email)
        except Exception as exc:
            logger.warning('Test email failed: %s', type(exc).__name__)
            # Type only: the message may contain server banners or addresses.
            return Response({'detail': f'Sending failed ({type(exc).__name__}). Check host, port, encryption and credentials.'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': f'Test message sent to {request.user.email}.'})
