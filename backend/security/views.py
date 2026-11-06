from django.core.cache import cache
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination
from core.permissions import IsAdmin

from . import services
from .capture import CAPTURE_ACTIONS, CAPTURE_EVENTS, DEFAULT_CAPTURE_ACTIONS, LABELS
from .engine import DEFAULT_DISABLED, catalogue
from .models import SecurityEvent, SecuritySettings
from .serializers import FIELDS, SecurityEventSerializer, SecuritySettingsSerializer


@extend_schema(tags=['security'])
class SecuritySettingsView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response({
            'settings': SecuritySettingsSerializer(SecuritySettings.load()).data,
            'groups': catalogue(),
            'default_disabled': DEFAULT_DISABLED,
            'capture': {
                'actions': list(CAPTURE_ACTIONS),
                'events': [{'id': e, 'label': LABELS.get(e, e), 'default': DEFAULT_CAPTURE_ACTIONS[e]} for e in CAPTURE_EVENTS],
            },
        })

    @extend_schema(request=SecuritySettingsSerializer, responses={200: dict, 400: dict})
    def put(self, request):
        row = SecuritySettings.load()
        before = SecuritySettingsSerializer(row).data
        serializer = SecuritySettingsSerializer(row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        row = serializer.save(updated_by=request.user)
        after = SecuritySettingsSerializer(row).data
        changed = sorted(f for f in FIELDS if before.get(f) != after.get(f))
        if changed:  # names only: values such as e-mail addresses stay out of the log
            services.record_security_event(SecurityEvent.KIND_SETTINGS, request, actor=request.user.email,
                                           detail={'changed': changed})
        return Response({'settings': after})


@extend_schema(tags=['security'])
class LoginNoticeView(APIView):
    """The monitoring notice shown before anyone signs in. Public (it is shown to people who are not signed in)."""
    permission_classes = []
    authentication_classes = []

    @extend_schema(responses={200: dict})
    def get(self, request):
        from .models import DEFAULT_LOGIN_NOTICE
        cfg = services.snapshot()
        text = (cfg['login_notice_text'] or '').strip() or DEFAULT_LOGIN_NOTICE
        return Response({'enabled': bool(cfg['login_notice_enabled']), 'text': text,
                         'nda_link_url': cfg['nda_link_url'], 'nda_link_text': cfg['nda_link_text'] or 'Acceptable Use Policy (AUP)'})


@extend_schema(tags=['security'])
class SecurityEventsView(APIView):
    """Filters: kind, rule, authenticated (true/false), since, until."""
    permission_classes = [IsAdmin]

    @extend_schema(responses={200: SecurityEventSerializer(many=True)})
    def get(self, request):
        qs = SecurityEvent.objects.all()
        params = request.query_params
        if params.get('kind'):
            qs = qs.filter(kind=params['kind'])
        if params.get('rule'):
            qs = qs.filter(rule=params['rule'])
        if params.get('authenticated') in ('true', 'false'):
            qs = qs.filter(authenticated=params['authenticated'] == 'true')
        since = parse_datetime(params.get('since', '') or '')
        if since:
            qs = qs.filter(created_at__gte=since)
        until = parse_datetime(params.get('until', '') or '')
        if until:
            qs = qs.filter(created_at__lte=until)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(SecurityEventSerializer(page, many=True).data)


@extend_schema(tags=['security'])
class SecurityInspectView(APIView):
    """
    For the dev server or a reverse proxy to report requests it answered itself (the app never saw
    them). Authenticated with the internal token, not a user. Body: method, uri, ip, headers, authenticated.
    """
    permission_classes = []
    authentication_classes = []

    @extend_schema(request=dict, responses={204: None, 403: dict})
    def post(self, request):
        import hmac
        from django.conf import settings
        given = request.META.get('HTTP_X_INTERNAL_TOKEN', '')
        if not given or not hmac.compare_digest(given, settings.INTERNAL_API_TOKEN):
            return Response({'detail': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)
        services.inspect_edge_request(request.data if isinstance(request.data, dict) else {})
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['security'])
class SecurityTestAlertView(APIView):
    """Send a test alert to the configured alert address (3 a minute at most)."""
    permission_classes = [IsAdmin]

    @extend_schema(request=None, responses={200: dict, 400: dict, 429: dict})
    def post(self, request):
        cache.add('security:test_alert', 0, 60)
        if cache.incr('security:test_alert') > 3:
            return Response({'detail': 'Slow down.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        address = services.snapshot()['alert_group_email']
        if not address:
            return Response({'detail': 'Set an alert address first.'}, status=status.HTTP_400_BAD_REQUEST)
        sent = services.send_alert('Security alert: test', ['This is a test of the security alert address.'], [address])
        if not sent:
            return Response({'detail': 'The alert could not be sent. Check the e-mail setup.'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': f'Test alert sent to {address}.'})
