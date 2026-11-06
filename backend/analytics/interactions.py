"""Interaction Activity: share-link events and check-out events across every datastore and file,
filtered with the display-filter language in ``filter_expr.py``.

Every filter is translated to ORM lookups from a fixed table (no raw SQL, no regex), separately for
each of the two sources, so a field that one source does not have simply never matches there.
"""
import ipaddress
from datetime import datetime, timedelta

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from checkout.models import CheckoutEvent
from sharelinks.events import CLIENT_EVENT_TYPES, SERVER_EVENT_TYPES, EventType
from sharelinks.models import ShareLink, ShareLinkEvent
from sharelinks.services import get_accessible_share_links_q

from .filter_expr import FIELDS, FilterError, parse

MAX_LIMIT = 200
ATTEMPT_TYPES = frozenset({
    EventType.PRINT_ATTEMPT, EventType.SCREENSHOT_KEY, EventType.SAVE_ATTEMPT,
    EventType.DEVTOOLS_KEY, EventType.COPY_ATTEMPT, EventType.CONTEXT_MENU, EventType.DRAG_ATTEMPT,
})
NOTHING = Q(pk__in=[])
EVERYTHING = Q(pk__isnull=False)

# field -> ORM path, per source (None: the source has no such field)
PATHS = {
    'link': {
        'event': 'event_type', 'email': 'email', 'ip': 'ip_address',
        'datastore': 'share_link__dataroom__name', 'file': 'share_link__document__name', 'link': 'share_link__name',
    },
    'checkout': {
        'event': 'event_type', 'email': 'email', 'ip': 'ip_address',
        'datastore': 'policy__dataroom__name', 'file': 'metadata__file', 'link': None,
    },
}


def _time_value(raw):
    raw = raw.strip()
    if raw[:1] in '-+' and raw[-1:] in 'mhdw' and raw[1:-1].isdigit():
        unit = {'m': 'minutes', 'h': 'hours', 'd': 'days', 'w': 'weeks'}[raw[-1]]
        delta = timedelta(**{unit: int(raw[1:-1])})
        return timezone.now() + (delta if raw[0] == '+' else -delta)
    moment = parse_datetime(raw)
    if moment is None:
        day = parse_date(raw)
        if day is None:
            raise FilterError(f"'{raw}' is not a time. Use 2026-01-31, 2026-01-31T10:00 or -24h.")
        moment = datetime(day.year, day.month, day.day)
    return timezone.make_aware(moment) if timezone.is_naive(moment) else moment


def _constant(truth):
    return EVERYTHING if truth else NOTHING


def _category(source, value):
    value = value.lower()
    if source == 'link':
        if value == 'access':
            return Q(event_type__in=SERVER_EVENT_TYPES)
        if value == 'behavior':
            return Q(event_type__in=CLIENT_EVENT_TYPES)
        return NOTHING
    if value == 'behavior':
        return Q(event_type__in=CLIENT_EVENT_TYPES)
    if value == 'checkout':
        return ~Q(event_type__in=CLIENT_EVENT_TYPES)
    return NOTHING


def _compare(source, field, op, value):
    if field == 'source':
        same = value.lower() == source
        return _constant(same if op in ('==', 'contains', 'startswith') else not same)
    if field == 'category':
        q = _category(source, value)
        return ~q if op == '!=' else q
    if field == 'time':
        moment = _time_value(value)
        lookup = {'==': 'created_at__date', '>': 'created_at__gt', '>=': 'created_at__gte',
                  '<': 'created_at__lt', '<=': 'created_at__lte', '!=': 'created_at__date'}[op]
        if op in ('==', '!='):
            q = Q(created_at__date=moment.date())
            return ~q if op == '!=' else q
        return Q(**{lookup: moment})
    path = PATHS[source][field]
    if path is None:
        return _constant(op == '!=')
    if field == 'ip' and op in ('==', '!='):
        try:
            value = str(ipaddress.ip_address(value.strip()))
        except ValueError:
            raise FilterError(f"'{value}' is not an IP address. Use ip startswith \"10.\" for a prefix.")
    suffix = {'==': '__iexact', '!=': '__iexact', 'contains': '__icontains', 'startswith': '__istartswith'}[op]
    if field == 'event':
        suffix = '' if op in ('==', '!=') else suffix
    if field == 'ip' and op in ('==', '!='):
        suffix = ''
    q = Q(**{path + suffix: value})
    return ~q if op == '!=' else q


def to_q(node, source):
    kind = node[0]
    if kind == 'and':
        return to_q(node[1], source) & to_q(node[2], source)
    if kind == 'or':
        return to_q(node[1], source) | to_q(node[2], source)
    if kind == 'not':
        return ~to_q(node[1], source)
    if kind == 'flag':
        return Q(event_type__in=ATTEMPT_TYPES)
    if kind == 'in':
        q = NOTHING
        for value in node[2]:
            q = q | _compare(source, node[1], '==', value)
        return q
    return _compare(source, node[1], node[2], node[3])


def _link_category(event_type):
    return 'access' if event_type in SERVER_EVENT_TYPES else 'behavior'


def link_row(e):
    link = e.share_link
    return {
        'id': str(e.id), 'time': e.created_at, 'source': 'link', 'event': e.event_type,
        'category': _link_category(e.event_type), 'email': e.email, 'ip': e.ip_address,
        'datastore': link.dataroom.name if link.dataroom_id else '',
        'file': link.document.name if link.document_id else '',
        'link': link.name or link.slug, 'user_agent': e.user_agent, 'detail': e.metadata or {},
    }


def checkout_row(e):
    detail = dict(e.metadata or {})
    return {
        'id': str(e.id), 'time': e.created_at, 'source': 'checkout', 'event': e.event_type,
        'category': 'behavior' if e.event_type in CLIENT_EVENT_TYPES else 'checkout',
        'email': e.email, 'ip': e.ip_address,
        'datastore': e.policy.dataroom.name if e.policy_id else '', 'file': detail.pop('file', ''),
        'link': '', 'user_agent': e.user_agent, 'detail': detail,
    }


def scoped_querysets(user):
    links = ShareLink.objects.filter(get_accessible_share_links_q(user))
    link_qs = ShareLinkEvent.objects.filter(share_link__in=links).select_related(
        'share_link__document', 'share_link__dataroom')
    if getattr(user, 'role', '') == 'admin':
        checkout_qs = CheckoutEvent.objects.filter(
            Q(policy__isnull=True) | Q(policy__organization=user.organization)).select_related('policy__dataroom')
    else:
        checkout_qs = CheckoutEvent.objects.none()
    return link_qs, checkout_qs


@extend_schema(tags=['analytics'])
class InteractionsView(APIView):
    """
    ``GET ?filter=<expression>&limit=100&before=<ISO time>``. Returns the newest matching
    interactions from share links and check-outs, plus counts per event for the whole filter.
    A bad expression is a 400 with ``{"filter": message, "position": n}``.
    """

    @extend_schema(responses={200: dict, 400: dict})
    def get(self, request):
        try:
            tree = parse(request.query_params.get('filter', ''))
            link_filter = to_q(tree, 'link') if tree else EVERYTHING
            checkout_filter = to_q(tree, 'checkout') if tree else EVERYTHING
        except FilterError as error:
            return Response({'filter': error.message, 'position': error.position}, status=status.HTTP_400_BAD_REQUEST)

        try:
            limit = max(1, min(int(request.query_params.get('limit', 100)), MAX_LIMIT))
        except ValueError:
            limit = 100
        before = parse_datetime(request.query_params.get('before', '') or '')

        link_qs, checkout_qs = scoped_querysets(request.user)
        link_qs, checkout_qs = link_qs.filter(link_filter), checkout_qs.filter(checkout_filter)

        counts = {}
        for qs in (link_qs, checkout_qs):
            for row in qs.order_by().values('event_type').annotate(n=Count('id')):
                counts[row['event_type']] = counts.get(row['event_type'], 0) + row['n']

        page_link, page_checkout = link_qs, checkout_qs
        if before:
            page_link, page_checkout = link_qs.filter(created_at__lt=before), checkout_qs.filter(created_at__lt=before)
        rows = [link_row(e) for e in page_link.order_by('-created_at')[:limit + 1]]
        rows += [checkout_row(e) for e in page_checkout.order_by('-created_at')[:limit + 1]]
        rows.sort(key=lambda r: r['time'], reverse=True)
        has_more = len(rows) > limit
        rows = rows[:limit]
        return Response({
            'results': rows, 'counts': counts, 'total': sum(counts.values()), 'has_more': has_more,
            'next_before': rows[-1]['time'] if has_more and rows else None, 'fields': list(FIELDS),
        })
