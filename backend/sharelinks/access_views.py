"""Endpoints for allow-list authentication, the owner's allow-list management and the event log.

Public (viewer) endpoints are deliberately uniform: whether an email address is on the list is
never revealed. Owner endpoints require permission on the share link.
"""
from security.throttling import ViewerThrottle
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from core.pagination import StandardResultsSetPagination

from .access import (
    GENERIC_REQUEST_MESSAGE,
    INVALID_CODE_MESSAGE,
    add_allowed_domains,
    add_allowed_emails,
    grant_session_access,
    revoke_allowed_domain,
    split_entries,
    request_access_code,
    requires_allowlist,
    revoke_allowed_email,
    send_invitation,
    verify_access_code,
)
from .events import (
    CLIENT_EVENT_TYPES,
    EventType,
    event_category,
    event_types_for_category,
    record_event,
)
from .models import ShareLink, ShareLinkAllowedDomain, ShareLinkAllowedEmail, ShareLinkEvent
from .services import can_user_edit_share_link, can_user_manage_share_link
from .views import (
    LinkExpired,
    _dispatch_automation_event,
    _get_active_share_link,
    _verify_share_link_session_authorization,
)

MAX_EMAILS_PER_REQUEST = 200
RESEND_COOLDOWN = timedelta(seconds=60)
CLIENT_EVENTS_PER_MINUTE = 240


# --------------------------------------------------------------------------- public flow


class AccessRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class AccessVerifySerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    code = serializers.CharField(max_length=32)


def _active_link_or_error(slug):
    try:
        return _get_active_share_link(slug), None
    except NotFound as exc:
        return None, Response({"message": exc.detail}, status=status.HTTP_404_NOT_FOUND)
    except LinkExpired as exc:
        return None, Response({"message": exc.detail}, status=status.HTTP_410_GONE)


@extend_schema(tags=['sharelinks'])
class ShareLinkAccessRequestView(APIView):
    """A visitor asks for access with an email address. The answer never depends on the address."""
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=AccessRequestSerializer, responses={200: dict, 400: dict, 404: dict, 410: dict})
    def post(self, request, slug):
        link, error = _active_link_or_error(slug)
        if error:
            return error
        if not requires_allowlist(link):
            return Response({"message": "This link does not use an access list."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = AccessRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        request_access_code(link, serializer.validated_data['email'], request)
        return Response({"message": GENERIC_REQUEST_MESSAGE, "code_required": True}, status=status.HTTP_200_OK)


@extend_schema(tags=['sharelinks'])
class ShareLinkAccessVerifyView(APIView):
    """A visitor submits the access code that was emailed to them."""
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=AccessVerifySerializer, responses={200: dict, 400: dict, 404: dict, 410: dict})
    def post(self, request, slug):
        link, error = _active_link_or_error(slug)
        if error:
            return error
        if not requires_allowlist(link):
            return Response({"message": "This link does not use an access list."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = AccessVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data['email']
        if not verify_access_code(link, email, serializer.validated_data['code'], request):
            return Response({"message": INVALID_CODE_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)

        grant_session_access(request, link, email)
        _dispatch_automation_event(
            link,
            'access_granted',
            {'viewer_email': email.strip().lower(), 'email_verification_method': 'allowlist_code'},
        )
        return Response({"message": "Access granted."}, status=status.HTTP_200_OK)


class ViewerEventSerializer(serializers.Serializer):
    event_type = serializers.CharField(max_length=40)
    metadata = serializers.DictField(required=False)


@extend_schema(tags=['sharelinks'])
class ShareLinkViewerEventView(APIView):
    """Viewer behaviour reported by the viewer's page (see frontend/src/lib/viewerEvents.js)."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(request=ViewerEventSerializer, responses={204: None, 400: dict, 401: dict, 403: dict, 404: dict, 410: dict})
    def post(self, request, slug):
        link, error = _active_link_or_error(slug)
        if error:
            return error
        auth_error, auth_status = _verify_share_link_session_authorization(link, request)
        if auth_error:
            return auth_error

        serializer = ViewerEventSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        event_type = serializer.validated_data['event_type']
        if event_type not in CLIENT_EVENT_TYPES:
            return Response({"event_type": ["Unsupported event type."]}, status=status.HTTP_400_BAD_REQUEST)

        from security.capture import effective_actions, lock, respond
        from security.services import snapshot
        cfg = snapshot()
        action = effective_actions(cfg['capture_actions']).get(event_type, 'log')
        if action == 'ignore':
            return Response(status=status.HTTP_204_NO_CONTENT)
        email = (auth_status or {}).get('viewer_email', '')
        # Per browser session (not per e-mail: anonymous viewers all share the empty address, and one
        # noisy one must not use up everybody's allowance).
        bucket = f"viewer-events:{request.session.session_key}:{link.id}"
        cache.add(bucket, 0, 60)
        if cache.incr(bucket) <= CLIENT_EVENTS_PER_MINUTE:
            record_event(link, event_type, request, email, {**(serializer.validated_data.get('metadata') or {}), 'action': action})
            where = (link.document.name if link.document_id else link.dataroom.name) if (link.document_id or link.dataroom_id) else link.slug
            respond(action, request=request, event_type=event_type, who=email, where=where)
            if action == 'end':
                lock(request, f"link:{link.id}", cfg['capture_lockout_minutes'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- owner endpoints


class AllowedEmailSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)
    last_access_at = serializers.SerializerMethodField()

    class Meta:
        model = ShareLinkAllowedEmail
        fields = ['id', 'email', 'created_at', 'invitation_sent_at', 'revoked_at', 'is_active', 'last_access_at']
        read_only_fields = fields

    def get_last_access_at(self, obj):
        return self.context.get('last_access', {}).get(obj.email)


class AllowedDomainSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = ShareLinkAllowedDomain
        fields = ['id', 'domain', 'created_at', 'revoked_at', 'is_active']
        read_only_fields = fields


class ShareLinkEventSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()

    class Meta:
        model = ShareLinkEvent
        fields = ['id', 'created_at', 'event_type', 'category', 'email', 'ip_address', 'user_agent', 'metadata']
        read_only_fields = fields

    def get_category(self, obj) -> str:
        return event_category(obj.event_type)


def _link_for_owner(request, link_id, edit=False):
    link = get_object_or_404(ShareLink.objects.select_related('document', 'dataroom'), id=link_id)
    allowed = can_user_edit_share_link(request.user, link) if edit else can_user_manage_share_link(request.user, link)
    if not allowed:
        raise NotFound("Share link not found.")
    return link


@extend_schema(tags=['sharelinks'])
class ShareLinkAllowedEmailsView(APIView):
    """List the allow-list of a link, or add emails to it (each new address is sent an invitation)."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: dict})
    def get(self, request, link_id):
        link = _link_for_owner(request, link_id)
        entries = list(link.allowed_emails.all())
        last_access = dict(
            ShareLinkEvent.objects.filter(share_link=link, event_type=EventType.ACCESS_GRANTED)
            .order_by().values('email').annotate(last=Max('created_at')).values_list('email', 'last')
        )
        return Response({
            'requires_allowlist': requires_allowlist(link),
            'results': AllowedEmailSerializer(entries, many=True, context={'last_access': last_access}).data,
            'domains': AllowedDomainSerializer(link.allowed_domains.all(), many=True).data,
        })

    @extend_schema(request=dict, responses={201: dict, 400: dict})
    def post(self, request, link_id):
        """
        Body: ``{"emails": [...]}`` (or ``"email"``). Each entry is either an address
        (``a@b.com``: invited individually) or a domain (``@b.com`` / ``b.com``: a domain-wide rule
        that lets anyone at that domain request access, with no invitation sent).
        """
        link = _link_for_owner(request, link_id, edit=True)
        payload = request.data
        entries = payload.get('emails')
        if not isinstance(entries, list):
            entries = [payload.get('email')] if payload.get('email') else []
        if not entries:
            return Response({"detail": "Provide 'emails' (a list) or 'email'."}, status=status.HTTP_400_BAD_REQUEST)
        if len(entries) > MAX_EMAILS_PER_REQUEST:
            return Response(
                {"detail": f"At most {MAX_EMAILS_PER_REQUEST} entries can be added at once."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        emails, domains, invalid = split_entries(entries)
        if not emails and not domains:
            return Response({"detail": "No valid email addresses or domains were provided.", "invalid": invalid},
                            status=status.HTTP_400_BAD_REQUEST)

        result = add_allowed_emails(link, emails, request.user, request)
        domain_result = add_allowed_domains(link, domains, request.user, request)
        return Response({
            'added': AllowedEmailSerializer(result['added'], many=True).data,
            'reinstated': AllowedEmailSerializer(result['reinstated'], many=True).data,
            'existing': [entry.email for entry in result['existing']],
            'domains_added': AllowedDomainSerializer(domain_result['added'] + domain_result['reinstated'], many=True).data,
            'domains_existing': [entry.domain for entry in domain_result['existing']],
            'invalid': invalid,
        }, status=status.HTTP_201_CREATED)


@extend_schema(tags=['sharelinks'])
class ShareLinkAllowedDomainDetailView(APIView):
    """Revoke one domain-wide rule (kept in the list as revoked, for the audit trail)."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={204: None, 404: dict})
    def delete(self, request, link_id, entry_id):
        link = _link_for_owner(request, link_id, edit=True)
        entry = get_object_or_404(ShareLinkAllowedDomain, id=entry_id, share_link=link)
        revoke_allowed_domain(entry, request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['sharelinks'])
class ShareLinkAllowedEmailDetailView(APIView):
    """Revoke one entry (it stays in the list as revoked, for the audit trail)."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={204: None, 404: dict})
    def delete(self, request, link_id, entry_id):
        link = _link_for_owner(request, link_id, edit=True)
        entry = get_object_or_404(ShareLinkAllowedEmail, id=entry_id, share_link=link)
        revoke_allowed_email(entry, request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['sharelinks'])
class ShareLinkAllowedEmailResendView(APIView):
    """Send the invitation notice again."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses={200: dict, 404: dict, 429: dict})
    def post(self, request, link_id, entry_id):
        link = _link_for_owner(request, link_id, edit=True)
        entry = get_object_or_404(ShareLinkAllowedEmail, id=entry_id, share_link=link, revoked_at__isnull=True)
        if entry.invitation_sent_at and timezone.now() - entry.invitation_sent_at < RESEND_COOLDOWN:
            return Response({"detail": "An invitation was sent a moment ago. Try again shortly."},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        sent = send_invitation(entry, request.user, request)
        return Response({"sent": sent}, status=status.HTTP_200_OK)


@extend_schema(tags=['sharelinks'])
class ShareLinkEventsView(APIView):
    """
    The access log of one link, newest first. Filters: ``category`` (access|behavior),
    ``event_type`` (comma separated), ``email`` (contains), ``since`` / ``until`` (ISO 8601).
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: ShareLinkEventSerializer(many=True)})
    def get(self, request, link_id):
        link = _link_for_owner(request, link_id)
        queryset = apply_event_filters(ShareLinkEvent.objects.filter(share_link=link), request.query_params)

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(ShareLinkEventSerializer(page, many=True).data)


def apply_event_filters(queryset, params):
    """Shared by the per-link log and the analytics views."""
    category = params.get('category')
    if category:
        queryset = queryset.filter(event_type__in=event_types_for_category(category))
    event_type = params.get('event_type')
    if event_type:
        queryset = queryset.filter(event_type__in=[t.strip() for t in event_type.split(',') if t.strip()])
    email = params.get('email')
    if email:
        queryset = queryset.filter(email__icontains=email.strip())
    since = parse_datetime(params.get('since', '') or '')
    if since:
        queryset = queryset.filter(created_at__gte=since)
    until = parse_datetime(params.get('until', '') or '')
    if until:
        queryset = queryset.filter(created_at__lte=until)
    return queryset


# --------------------------------------------------------------------------- viewer activity

from .activity import TRACKED, viewer_activity  # noqa: E402


@extend_schema(tags=['sharelinks'])
class ShareLinkViewerActivityView(APIView):
    """Time spent and attempted actions per viewer of one link."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: dict})
    def get(self, request, link_id):
        link = _link_for_owner(request, link_id)
        return Response({'event_types': TRACKED, 'results': viewer_activity([link])})


@extend_schema(tags=['sharelinks'])
class DocumentViewerActivityView(APIView):
    """The same, across every link of a document the user can see."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses={200: dict, 404: dict})
    def get(self, request, document_id):
        from documents.views import get_document_queryset_for_user
        document = get_document_queryset_for_user(request.user).filter(id=document_id).first()
        if document is None:
            raise NotFound("Document not found.")
        links = [link for link in ShareLink.objects.filter(document=document)
                 if can_user_manage_share_link(request.user, link)]
        results = viewer_activity(links) if links else []
        # Check-outs have no share link; their activity belongs to the document too, labelled by source.
        # Only for people who manage the file (its owner, or an administrator of the organization).
        if document.created_by_id == request.user.id or getattr(request.user, 'role', '') == 'admin':
            from checkout.activity import checkout_activity
            results = results + checkout_activity(document)
        return Response({'event_types': TRACKED, 'results': results})
