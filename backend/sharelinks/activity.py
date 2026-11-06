"""Per-viewer activity: how long each viewer spent, and what they tried to do to the page."""
from django.db.models import Count, Max, Sum
from django.db.models.fields.json import KeyTextTransform

from .events import CLIENT_EVENT_TYPES
from .models import ShareLinkEvent, ViewSession

TRACKED = sorted(CLIENT_EVENT_TYPES)


def viewer_activity(links):
    """
    ``links``: queryset/list of ShareLinks. Returns a list (most recent first) of
    ``{email, sessions, seconds, last_seen, counts: {event_type: n}, attempts}`` where
    ``attempts`` is the total of the suspicious ones (everything except tab/window focus changes).
    """
    sessions = (
        ViewSession.objects.filter(share_link__in=links)
        .order_by().values('viewer_email')
        .annotate(sessions=Count('id'), seconds=Sum('duration_seconds'), last_seen=Max('viewed_at'))
    )
    rows = {
        (row['viewer_email'] or '').lower(): {
            'email': (row['viewer_email'] or '').lower(),
            'sessions': row['sessions'],
            'seconds': row['seconds'] or 0,
            'last_seen': row['last_seen'],
            'counts': {},
            'source': 'link',
        }
        for row in sessions
    }
    events = (
        ShareLinkEvent.objects.filter(share_link__in=links, event_type__in=TRACKED)
        .order_by().values('email', 'event_type').annotate(n=Count('id'), last=Max('created_at'))
    )
    for row in events:
        email = (row['email'] or '').lower()
        entry = rows.setdefault(email, {'email': email, 'sessions': 0, 'seconds': 0, 'last_seen': row['last'], 'counts': {}, 'source': 'link'})
        entry['counts'][row['event_type']] = row['n']
        if entry['last_seen'] is None or row['last'] > entry['last_seen']:
            entry['last_seen'] = row['last']
    # Print Screen in more detail: which modifiers were held, and whether the press or only the release was seen
    # (Windows often swallows the press). Fn is invisible to a browser, so Fn+PrintScreen counts as plain PrintScreen.
    variants = (
        ShareLinkEvent.objects.filter(share_link__in=links, event_type='screenshot_key')
        .annotate(variant=KeyTextTransform('variant', 'metadata'), phase=KeyTextTransform('phase', 'metadata'))
        .order_by().values('email', 'variant', 'phase').annotate(n=Count('id'))
    )
    for row in variants:
        entry = rows.get((row['email'] or '').lower())
        if entry is None:
            continue
        label = row['variant'] or 'plain'
        if row['phase'] == 'keyup':
            label += ' (release only)'
        bucket = entry.setdefault('screenshot_variants', {})
        bucket[label] = bucket.get(label, 0) + row['n']
    quiet = {'tab_hidden', 'window_blur'}
    for entry in rows.values():
        entry['attempts'] = sum(n for t, n in entry['counts'].items() if t not in quiet)
    return sorted(rows.values(), key=lambda e: e['last_seen'] or 0, reverse=True)
