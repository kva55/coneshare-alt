"""Security settings access, failed-auth alerting and the (log-only) probe detector."""
import ipaddress
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from backend.utils import get_client_ip
from core.mailer import send_app_mail

from .models import SecurityEvent, SecuritySettings
from . import engine

logger = logging.getLogger(__name__)

CACHE_KEY = 'security:settings'
SNAPSHOT_TTL = 30
ALERTS_PER_HOUR = 20  # hard cap on alert e-mails, so an attacker cannot use alerts to mail-bomb


def snapshot() -> dict:
    """Current settings as a plain dict (cached briefly; the settings view clears the cache)."""
    data = cache.get(CACHE_KEY)
    if data is None:
        row = SecuritySettings.load()
        data = {f.name: getattr(row, f.name) for f in SecuritySettings._meta.concrete_fields
                if f.name not in ('id', 'created_at', 'updated_at', 'updated_by')}
        cache.set(CACHE_KEY, data, SNAPSHOT_TTL)
    return data


def clear_cache() -> None:
    cache.delete(CACHE_KEY)


def code_request_limits():
    """(per_email, per_ip, window) for e-mailed access codes and check-out links."""
    cfg = snapshot()
    return (cfg['code_requests_per_email'], cfg['code_requests_per_ip'],
            timedelta(minutes=cfg['code_request_window_minutes']))


def record_security_event(kind, request=None, rule='', actor='', authenticated=False, detail=None,
                          path='', query='') -> SecurityEvent:
    ip = ua = method = ''
    if request is not None:
        ip = get_client_ip(request) or None
        ua = (request.META.get('HTTP_USER_AGENT', '') or '')[:255]
        method = request.method[:10]
        path = path or request.path
    return SecurityEvent.objects.create(
        kind=kind, rule=rule, method=method, path=path[:512], query_excerpt=query[:200],
        ip_address=ip or None, user_agent=ua, authenticated=authenticated, actor=actor[:254],
        detail=detail or {},
    )


def send_alert(subject: str, lines, recipients) -> bool:
    """E-mail an alert, never raising, and never more than ALERTS_PER_HOUR an hour."""
    recipients = [r for r in dict.fromkeys(recipients) if r]
    if not recipients:
        return False
    cache.add('security:alerts_hour', 0, 3600)
    try:
        if cache.incr('security:alerts_hour') > ALERTS_PER_HOUR:
            return False
        send_app_mail(subject=subject, text='\n'.join(lines), recipients=recipients)
        return True
    except Exception:
        logger.exception('Could not send security alert')
        return False


def report_failed_auth(kind: str, identifier: str, request=None, account_email: str = '') -> None:
    """
    Count a failed sign-in (``kind``: login | share_code | checkout_link) for ``identifier`` and,
    when the configured threshold is reached inside the window, log it and e-mail the alert
    group and (if enabled) the targeted account. Fires once per window.
    """
    cfg = snapshot()
    if not cfg['failed_auth_alerts_enabled'] or not identifier:
        return
    identifier = identifier.strip().lower()[:254]
    key = f"security:fail:{kind}:{identifier}"
    cache.add(key, 0, cfg['failed_auth_window_minutes'] * 60)
    try:
        count = cache.incr(key)
    except ValueError:
        return
    if count != cfg['failed_auth_threshold']:
        return

    ip = (get_client_ip(request) if request is not None else '') or 'unknown'
    record_security_event(SecurityEvent.KIND_FAILED_AUTH, request, rule=kind, actor=identifier,
                          detail={'failures': count, 'window_minutes': cfg['failed_auth_window_minutes']})
    recipients = [cfg['alert_group_email']]
    if cfg['alert_notify_account'] and account_email:
        recipients.append(account_email)
    send_alert(
        f"Security alert: {count} failed sign-in attempts ({kind})",
        [f"There were {count} failed sign-in attempts for {identifier} within "
         f"{cfg['failed_auth_window_minutes']} minutes.",
         f"Type: {kind}", f"Latest source address: {ip}", '',
         "If this was not you, change your password and tell an administrator."],
        recipients,
    )


# ----------------------------------------------------------------------------- probe detection

ROWS_PER_SOURCE_AND_RULE_PER_MINUTE = 20
ROWS_PER_MINUTE = 1000
SEVERITY_LABEL = {'critical': 'critical', 'warning': 'warning', 'notice': 'notice'}


def _request_headers(request):
    """Request headers as {lower-case-name: value} from Django's META."""
    headers = {}
    for key, value in request.META.items():
        if key.startswith('HTTP_'):
            headers[key[5:].replace('_', '-').lower()] = value
    for key in ('CONTENT_LENGTH', 'CONTENT_TYPE'):
        if request.META.get(key):
            headers[key.replace('_', '-').lower()] = request.META[key]
    return headers


def _small_body_arguments(request):
    """Arguments of a small JSON or form body; anything else (uploads, big bodies) is not read."""
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        return []
    if request.method not in ('POST', 'PUT', 'PATCH') or not 0 < length <= engine.MAX_BODY:
        return []
    try:
        return engine.body_arguments(request.META.get('CONTENT_TYPE', ''), request.body)
    except Exception:  # RawPostDataException etc.: the body was already consumed
        return []


def collect_request(request):
    """Run the rules over a request as the application received it. Returns (hits, authenticated)."""
    cfg = snapshot()
    if not cfg['probe_detection_enabled']:
        return [], False
    authenticated = bool(request.COOKIES.get('cs_access'))
    if authenticated and not cfg['probe_log_authenticated']:
        return [], authenticated
    req = engine.Request(request.method, request.get_full_path(), _request_headers(request),
                         body_args=_small_body_arguments(request))
    return engine.evaluate(req, disabled=cfg['probe_disabled_rules']), authenticated


def record_probe(hits, *, request=None, method='', path='', query='', ip=None, user_agent='',
                 authenticated=False, edge=False) -> int:
    """Store hits (rate limited per source and rule so a scanner cannot fill the table) and try to alert."""
    cfg = snapshot()
    source = ip or (get_client_ip(request) if request is not None else '') or 'unknown'
    cache.add('security:probe_rows', 0, 60)
    stored = 0
    safe_query = re.sub(r'(?i)((token|code|password|secret|key|otp|view_session_id|session|auth)=)[^&]*', r'\1[redacted]', query)
    for hit in hits:
        counter = f"security:probe:{source}:{hit['rule']}"
        cache.add(counter, 0, 60)
        if cache.incr(counter) > ROWS_PER_SOURCE_AND_RULE_PER_MINUTE or cache.incr('security:probe_rows') > ROWS_PER_MINUTE:
            continue
        event = record_security_event(
            SecurityEvent.KIND_PROBE, request, rule=hit['rule'], authenticated=authenticated,
            query=safe_query, path=path, detail={'matched': hit['matched'], 'where': hit['where'],
                                                  'group': hit['group'], 'severity': hit['severity'],
                                                  **({'edge': True} if edge else {})})
        if ip or method or user_agent:   # a request seen by another component: it names its own source
            fields = {}
            if ip:
                fields['ip_address'] = ip
            if method:
                fields['method'] = method[:10]
            if user_agent:
                fields['user_agent'] = user_agent[:255]
            SecurityEvent.objects.filter(pk=event.pk).update(**fields)
        stored += 1
    if stored and cfg['probe_alert_enabled']:
        send_probe_digest()
    return stored


def unknown_area_hit(path: str, status_code: int = 404, edge: bool = False):
    if not engine.is_unknown_area(path, edge=edge):
        return []
    if not edge and status_code != 404:
        return []
    return [{'rule': 'unknown_area', 'group': 'APP', 'severity': 'notice', 'where': 'path', 'matched': path[:80]}]


def scan_request(request, response=None):
    """Compatibility wrapper: inspect one request (and its response) and record what it trips."""
    hits, authenticated = collect_request(request)
    cfg = snapshot()
    if (response is not None and cfg['probe_detection_enabled'] and cfg['probe_log_unknown_areas']
            and not (authenticated and not cfg['probe_log_authenticated'])):
        hits = hits + unknown_area_hit(request.path, response.status_code)
    if hits:
        record_probe(hits, request=request, path=request.path, query=request.META.get('QUERY_STRING', ''),
                     authenticated=authenticated)


def inspect_edge_request(payload: dict) -> int:
    """
    A request that never reached the application (the dev server or the reverse proxy answered it),
    reported by that component. ``payload``: method, uri, ip, headers, authenticated.
    """
    cfg = snapshot()
    if not cfg['probe_detection_enabled']:
        return 0
    uri = str(payload.get('uri') or '/')[:4000]
    if not uri.startswith('/'):
        uri = '/' + uri
    headers = {str(k).lower(): str(v) for k, v in (payload.get('headers') or {}).items()}
    authenticated = bool(payload.get('authenticated'))
    if authenticated and not cfg['probe_log_authenticated']:
        return 0
    req = engine.Request(str(payload.get('method') or 'GET'), uri, headers)
    hits = engine.evaluate(req, disabled=cfg['probe_disabled_rules'])
    if cfg['probe_log_unknown_areas']:
        hits += unknown_area_hit(req.path_raw.split('?')[0], edge=True)
    ip = str(payload.get('ip') or '')
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        ip = ''
    return record_probe(hits, method=req.method, path=req.path_raw[:512], query=req.query_raw, ip=ip or None,
                        user_agent=headers.get('user-agent', ''), authenticated=authenticated, edge=True) if hits else 0


# ----------------------------------------------------------------------------- alert digest

def build_digest(events):
    """Plain-text summary of probe events, grouped by rule."""
    by_rule, by_source, examples = {}, {}, []
    for e in events:
        by_rule[e.rule] = by_rule.get(e.rule, 0) + 1
        by_source[e.ip_address or 'unknown'] = by_source.get(e.ip_address or 'unknown', 0) + 1
        if len(examples) < 8 and not any(x.rule == e.rule and x.path == e.path for x in examples):
            examples.append(e)
    severity = {r['id']: r['severity'] for r in engine.RULES}
    width = max((len(r) for r in by_rule), default=10)
    lines = [f"{len(events)} suspicious request(s) from {len(by_source)} source(s)", '', 'By rule']
    for rule_id, count in sorted(by_rule.items(), key=lambda kv: (engine.SEVERITY_ORDER.get(severity.get(kv[0], 'notice'), 9), -kv[1])):
        lines.append(f"  {rule_id.ljust(width)}  {str(count).rjust(4)}   {severity.get(rule_id, '')}")
    lines += ['', 'Top sources']
    lines += [f"  {ip.ljust(40)}  {n}" for ip, n in sorted(by_source.items(), key=lambda kv: -kv[1])[:5]]
    lines += ['', 'Examples']
    for e in examples:
        what = (e.detail or {}).get('matched', '')
        lines.append(f"  {e.method or '-':<7} {e.path[:60]}  [{e.rule}] {what[:40]}".rstrip())
    lines += ['', 'These requests were logged only; nothing was blocked.',
              f"Full log: {settings.SITE_DOMAIN.rstrip('/')}/admin/security"]
    return '\n'.join(lines)


def send_probe_digest() -> bool:
    """
    E-mail one digest of the probe events not yet reported, then wait out the cool-down; events that
    arrive meanwhile are collected and go out in the next digest (the periodic task flushes them), so
    a scan produces a summary of everything, not just its first request.
    """
    cfg = snapshot()
    if not (cfg['probe_detection_enabled'] and cfg['probe_alert_enabled'] and cfg['alert_group_email']):
        return False
    window_key = 'security:probe_digest_window'
    if not cache.add(window_key, 1, cfg['probe_alert_cooldown_minutes'] * 60):
        return False
    events = list(SecurityEvent.objects.filter(kind=SecurityEvent.KIND_PROBE, alerted_at__isnull=True)
                  .order_by('created_at')[:2000])
    if not events:
        cache.delete(window_key)   # nothing to say: do not use up the window
        return False
    sent = send_alert(f"Security alert: {len(events)} suspicious request(s)", build_digest(events).split('\n'),
                      [cfg['alert_group_email']])
    if sent:
        SecurityEvent.objects.filter(pk__in=[e.pk for e in events]).update(alerted_at=timezone.now())
    else:
        cache.delete(window_key)
    return sent
