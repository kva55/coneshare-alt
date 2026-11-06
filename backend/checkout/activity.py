"""Viewer activity for a document from its check-outs (the share-link side is sharelinks/activity.py)."""
from datetime import timedelta

from django.db.models import Count, Max, Q
from django.db.models.fields.json import KeyTextTransform
from django.utils import timezone

from sharelinks.events import CLIENT_EVENT_TYPES

from .models import Checkout, CheckoutEvent, CheckoutFile

TRACKED = sorted(CLIENT_EVENT_TYPES)
QUIET = {'tab_hidden', 'window_blur'}


def checkout_activity(document):
    """
    Rows ``{source: 'checkout', email, sessions, seconds, last_seen, counts, attempts, screenshot_variants}``,
    one per guest who opened this document through a check-out. Events recorded before the document id was
    kept in them are matched by file name within the policies that publish this document.
    """
    files = list(CheckoutFile.objects.filter(dataroom_document__document=document)
                 .select_related('dataroom_document'))
    if not files:
        return []
    policy_ids = {f.policy_id for f in files}
    names = {f.dataroom_document.name or document.name for f in files} | {document.name}

    rows = {}

    def row(email, last=None):
        return rows.setdefault(email, {'source': 'checkout', 'email': email, 'sessions': 0, 'seconds': 0,
                                       'last_seen': last, 'counts': {}})

    now = timezone.now()
    for c in Checkout.objects.filter(file__in=files, opened_at__isnull=False):
        entry = row((c.email or '').lower(), c.opened_at)
        entry['sessions'] += 1
        end = min(c.expires_at or now, now)
        entry['seconds'] += max(0, int((end - c.opened_at).total_seconds()))
        if entry['last_seen'] is None or c.opened_at > entry['last_seen']:
            entry['last_seen'] = c.opened_at

    mine = Q(metadata__document=str(document.id)) | (Q(metadata__document__isnull=True) & Q(metadata__file__in=names))
    events = CheckoutEvent.objects.filter(policy_id__in=policy_ids, event_type__in=TRACKED).filter(mine)
    for e in events.order_by().values('email', 'event_type').annotate(n=Count('id'), last=Max('created_at')):
        entry = row((e['email'] or '').lower(), e['last'])
        entry['counts'][e['event_type']] = e['n']
        if entry['last_seen'] is None or e['last'] > entry['last_seen']:
            entry['last_seen'] = e['last']

    variants = (events.filter(event_type='screenshot_key')
                .annotate(variant=KeyTextTransform('variant', 'metadata'), phase=KeyTextTransform('phase', 'metadata'))
                .order_by().values('email', 'variant', 'phase').annotate(n=Count('id')))
    for v in variants:
        entry = rows.get((v['email'] or '').lower())
        if entry is None:
            continue
        label = (v['variant'] or 'plain') + (' (release only)' if v['phase'] == 'keyup' else '')
        bucket = entry.setdefault('screenshot_variants', {})
        bucket[label] = bucket.get(label, 0) + v['n']

    for entry in rows.values():
        entry['attempts'] = sum(n for t, n in entry['counts'].items() if t not in QUIET)
    return sorted(rows.values(), key=lambda r: r['last_seen'].timestamp() if r['last_seen'] else 0, reverse=True)
