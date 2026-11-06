"""Read and write the access policy of a document or datastore (independent of share links)."""
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import split_entries
from .models import AccessPolicy

BOOLEANS = ('requires_email', 'requires_email_verification', 'require_nda', 'enable_watermark',
            'blur_when_unfocused', 'restricted_viewing_pane')


def _target(request, kind, ident):
    """(document, dataroom, label) the user may manage, or None. 'dataroom-document' means that file's document."""
    if kind == 'document':
        from documents.views import get_document_queryset_for_user
        doc = get_document_queryset_for_user(request.user).filter(id=ident).first()
        return (doc, None, doc.name) if doc else None
    if kind in ('dataroom', 'dataroom-document'):
        from datarooms.models import DataroomDocument
        from datarooms.views import get_dataroom_queryset_for_user
        rooms = get_dataroom_queryset_for_user(request.user)
        if kind == 'dataroom':
            room = rooms.filter(id=ident).first()
            return (None, room, room.name) if room else None
        dd = DataroomDocument.objects.select_related('document').filter(id=ident, dataroom__in=rooms).first()
        return (dd.document, None, dd.name or dd.document.name) if dd else None
    return None


def _body(policy, label):
    p = policy
    return {
        'target': label,
        'exists': p is not None,
        'requires_email': bool(p and p.requires_email),
        'requires_email_verification': bool(p and p.requires_email_verification),
        'has_password': bool(p and p.password),
        'require_nda': bool(p and p.require_nda),
        'nda_text': p.nda_text if p else '',
        'allow_download': None if p is None else p.allow_download,
        'enable_watermark': bool(p and p.enable_watermark),
        'watermark_text': p.watermark_text if p else '',
        'blur_when_unfocused': bool(p and p.blur_when_unfocused),
        'restricted_viewing_pane': bool(p and p.restricted_viewing_pane),
        'allowed_emails': list(p.allowed_emails) if p else [],
        'allowed_domains': list(p.allowed_domains) if p else [],
    }


class AccessPolicyView(APIView):
    """
    ``/access-policies/<document|dataroom|dataroom-document>/<id>/``. Every link to the target inherits
    these rules, and they exist whether or not a link does. PUT is partial; ``password`` sets it (blank
    keeps it), ``clear_password: true`` removes it; ``allowed`` replaces the list of allowed addresses
    and domains (``a@b.com`` or ``@b.com``).
    """
    permission_classes = [permissions.IsAuthenticated]

    def _load(self, request, kind, ident):
        target = _target(request, kind, ident)
        if target is None:
            return None, None, None
        document, dataroom, label = target
        policy = AccessPolicy.objects.filter(document=document).first() if document else \
            AccessPolicy.objects.filter(dataroom=dataroom).first()
        return (document, dataroom), policy, label

    @extend_schema(tags=['sharelinks'], responses={200: dict, 404: dict})
    def get(self, request, kind, ident):
        target, policy, label = self._load(request, kind, ident)
        if target is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(_body(policy, label))

    @extend_schema(tags=['sharelinks'], request=dict, responses={200: dict, 400: dict, 404: dict})
    def put(self, request, kind, ident):
        target, policy, label = self._load(request, kind, ident)
        if target is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        data = request.data
        if policy is None:
            policy = AccessPolicy(document=target[0], dataroom=target[1])

        for field in BOOLEANS:
            if field in data:
                setattr(policy, field, bool(data[field]))
        if 'allow_download' in data:
            policy.allow_download = data['allow_download'] if data['allow_download'] in (True, False) else None
        if 'nda_text' in data:
            policy.nda_text = str(data['nda_text'])[:20000]
        if 'watermark_text' in data:
            policy.watermark_text = str(data['watermark_text'])[:255]
        if data.get('clear_password'):
            policy.password = None
        elif data.get('password'):
            policy.password = str(data['password'])[:255]
        if policy.requires_email_verification and not policy.requires_email:
            policy.requires_email = True   # verifying an address means asking for one
        if policy.require_nda and not (policy.nda_text or '').strip():
            return Response({'nda_text': ['Write the agreement text, or switch the NDA off.']}, status=status.HTTP_400_BAD_REQUEST)

        invalid = []
        if 'allowed' in data:
            tokens = data['allowed'] if isinstance(data['allowed'], list) else [data['allowed']]
            if len(tokens) > 500:
                return Response({'allowed': ['At most 500 entries.']}, status=status.HTTP_400_BAD_REQUEST)
            emails, domains, invalid = split_entries(tokens)
            if invalid:
                return Response({'allowed': [f"Not valid: {', '.join(invalid)}"], 'invalid': invalid}, status=status.HTTP_400_BAD_REQUEST)
            policy.allowed_emails, policy.allowed_domains = emails, domains

        policy.updated_by = request.user
        policy.save()
        from security.models import SecurityEvent
        from security.services import record_security_event
        record_security_event(SecurityEvent.KIND_SETTINGS, request, rule='access_policy', actor=request.user.email,
                              detail={'target': kind, 'changed': sorted(k for k in data if k not in ('password',)) + (['password'] if data.get('password') else [])})
        return Response(_body(policy, label))
