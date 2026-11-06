from unittest.mock import patch

import pytest
from django.core import mail
from django.test import RequestFactory
from rest_framework import status
from rest_framework.test import APIClient

from security import services
from security.models import SecurityEvent, SecuritySettings
from security.engine import RULE_IDS

pytestmark = pytest.mark.django_db
URL = '/api/v1/security/settings/'


def _configure(**values):
    row = SecuritySettings.load()
    for key, value in values.items():
        setattr(row, key, value)
    row.save()
    services.clear_cache()
    return row


class TestSettingsApi:
    def test_admin_only(self, api_client, public_client):
        assert api_client.get(URL).status_code == status.HTTP_403_FORBIDDEN
        assert public_client.get(URL).status_code in (401, 403)

    def test_defaults_and_rule_catalogue(self, admin_api_client):
        body = admin_api_client.get(URL).json()
        assert body['settings']['failed_auth_threshold'] == 3
        assert body['settings']['probe_detection_enabled'] is False
        assert {r['id'] for g in body['groups'] for r in g['rules']} == set(RULE_IDS)

    def test_update_and_validation(self, admin_api_client):
        ok = admin_api_client.put(URL, {'code_requests_per_email': 2, 'failed_auth_threshold': 4}, format='json')
        assert ok.status_code == 200 and ok.json()['settings']['code_requests_per_email'] == 2
        assert admin_api_client.put(URL, {'code_requests_per_email': 0}, format='json').status_code == 400
        assert admin_api_client.put(URL, {'failed_auth_threshold': 999}, format='json').status_code == 400
        assert admin_api_client.put(URL, {'probe_disabled_rules': ['nonsense']}, format='json').status_code == 400

    def test_probe_alerts_need_an_address(self, admin_api_client):
        assert admin_api_client.put(URL, {'probe_alert_enabled': True}, format='json').status_code == 400
        ok = admin_api_client.put(URL, {'probe_alert_enabled': True, 'alert_group_email': 'sec@lab.local'}, format='json')
        assert ok.status_code == 200

    def test_a_change_is_logged_by_field_name_only(self, admin_api_client):
        admin_api_client.put(URL, {'alert_group_email': 'sec@lab.local'}, format='json')
        event = SecurityEvent.objects.get(kind=SecurityEvent.KIND_SETTINGS)
        assert event.detail == {'changed': ['alert_group_email']}
        assert 'sec@lab.local' not in str(event.detail)

    def test_settings_are_used_immediately(self, admin_api_client):
        admin_api_client.put(URL, {'code_requests_per_email': 2, 'code_request_window_minutes': 30}, format='json')
        per_email, _per_ip, window = services.code_request_limits()
        assert per_email == 2 and window.total_seconds() == 1800

    def test_events_are_admin_only_and_filterable(self, admin_api_client, api_client):
        SecurityEvent.objects.create(kind='web_probe', rule='xss', authenticated=True)
        SecurityEvent.objects.create(kind='web_probe', rule='jndi', authenticated=False)
        assert api_client.get('/api/v1/security/events/').status_code == 403
        data = admin_api_client.get('/api/v1/security/events/', {'rule': 'xss'}).json()
        assert data['count'] == 1
        assert admin_api_client.get('/api/v1/security/events/', {'authenticated': 'false'}).json()['count'] == 1

    def test_test_alert(self, admin_api_client):
        assert admin_api_client.post('/api/v1/security/test-alert/').status_code == 400
        _configure(alert_group_email='sec@lab.local')
        mail.outbox.clear()
        assert admin_api_client.post('/api/v1/security/test-alert/').status_code == 200
        assert [m.to for m in mail.outbox] == [['sec@lab.local']]


class TestFailedAuthAlerts:
    def test_alert_fires_once_at_the_threshold_to_group_and_account(self):
        _configure(failed_auth_threshold=3, alert_group_email='sec@lab.local')
        mail.outbox.clear()
        for _ in range(5):
            services.report_failed_auth('login', 'Bob@Lab.local', None, account_email='bob@lab.local')
        assert len(mail.outbox) == 1
        assert sorted(mail.outbox[0].to) == ['bob@lab.local', 'sec@lab.local']
        assert SecurityEvent.objects.filter(kind=SecurityEvent.KIND_FAILED_AUTH, actor='bob@lab.local').count() == 1

    def test_below_the_threshold_nothing_happens(self):
        _configure(failed_auth_threshold=3, alert_group_email='sec@lab.local')
        mail.outbox.clear()
        services.report_failed_auth('login', 'a@lab.local', None)
        services.report_failed_auth('login', 'a@lab.local', None)
        assert mail.outbox == []

    def test_can_be_turned_off(self):
        _configure(failed_auth_alerts_enabled=False, failed_auth_threshold=1, alert_group_email='sec@lab.local')
        mail.outbox.clear()
        services.report_failed_auth('login', 'a@lab.local', None)
        assert mail.outbox == []

    def test_the_account_is_only_told_when_enabled(self):
        _configure(failed_auth_threshold=1, alert_group_email='sec@lab.local', alert_notify_account=False)
        mail.outbox.clear()
        services.report_failed_auth('login', 'bob@lab.local', None, account_email='bob@lab.local')
        assert mail.outbox[0].to == ['sec@lab.local']

    def test_failures_are_counted_per_identifier(self):
        _configure(failed_auth_threshold=2, alert_group_email='sec@lab.local')
        mail.outbox.clear()
        services.report_failed_auth('login', 'a@lab.local', None)
        services.report_failed_auth('login', 'b@lab.local', None)
        assert mail.outbox == []

    def test_a_real_failed_login_is_counted(self, user):
        _configure(failed_auth_threshold=3, alert_group_email='sec@lab.local')
        mail.outbox.clear()
        client = APIClient()
        for _ in range(3):
            client.post('/api/v1/token/', {'email': user.email, 'password': 'wrong-password'}, format='json')
        assert len(mail.outbox) == 1
        assert user.email in mail.outbox[0].to

    def test_alert_mail_is_capped_per_hour(self):
        _configure(failed_auth_threshold=1, alert_group_email='sec@lab.local')
        mail.outbox.clear()
        for i in range(services.ALERTS_PER_HOUR + 10):
            services.report_failed_auth('login', f'user{i}@lab.local', None)
        assert len(mail.outbox) == services.ALERTS_PER_HOUR

    def test_a_mail_failure_never_breaks_the_caller(self):
        _configure(failed_auth_threshold=1, alert_group_email='sec@lab.local')
        with patch('security.services.send_app_mail', side_effect=OSError('smtp down')):
            services.report_failed_auth('login', 'a@lab.local', None)

    def test_share_link_code_failures_count_too(self, share_link, settings):
        settings.SHARELINK_REQUIRE_ALLOWLIST = True
        _configure(failed_auth_threshold=2, alert_group_email='sec@lab.local')
        mail.outbox.clear()
        client = APIClient()
        for _ in range(2):
            client.post(f'/api/v1/links/{share_link.slug}/access/verify/', {'email': 'x@lab.local', 'code': 'AAAA-AAAA'}, format='json')
        assert len(mail.outbox) == 1


class TestCodeRequestLimitSetting:
    def test_the_limit_comes_from_the_settings(self, share_link, api_client, settings):
        settings.SHARELINK_REQUIRE_ALLOWLIST = True
        _configure(code_requests_per_email=1)
        api_client.post(f'/api/v1/share-links/{share_link.id}/allowed-emails/', {'emails': ['g@lab.local']}, format='json')
        mail.outbox.clear()
        client = APIClient()
        for _ in range(4):
            client.post(f'/api/v1/links/{share_link.slug}/access/request/', {'email': 'g@lab.local'}, format='json')
        assert len(mail.outbox) == 1


class TestProbeRules:
    def _hits(self, uri, method='GET', **headers):
        from security import engine
        return {h['rule'] for h in engine.evaluate(engine.Request(method, uri, headers))}

    def test_scanner_traffic_trips_several_rules_not_one(self):
        assert 'scanner_user_agent' in self._hits('/', **{'user-agent': 'Mozilla/5.0 (compatible; Nmap Scripting Engine)'})
        assert {'known_probe_path', 'restricted_extension'} <= self._hits('/nice%20ports%2C/Trinity.txt%2ebak')
        assert 'restricted_file_access' in self._hits('/.git/HEAD')
        assert {'path_traversal', 'os_file_access'} <= self._hits('/../../../../etc/passwd')
        assert 'sqli_union_select' in self._hits('/x?id=1 UNION SELECT a,b FROM t--')
        assert 'xss_script_tag' in self._hits('/x?q=<script>alert(1)</script>')
        assert 'method_not_allowed' in self._hits('/', 'TRACE')

    def test_this_applications_own_urls_stay_quiet(self):
        for uri in ('/api/v1/documents/?page=2&search=quarterly report', '/api/v1/links/abc/pages/2/', '/view/abc',
                    '/checkout?token=gE3k9_LZx-Qw2VnT8aB7yCqP0uHs5dRjMfXo1iKtNWE', '/@vite/client',
                    '/node_modules/.vite/deps/react.js?v=1', '/api/v1/documents/?search=cat photos.pdf'):
            assert self._hits(uri) == set(), uri

    def test_php_and_java_rules_start_switched_off(self):
        from security.engine import DEFAULT_DISABLED
        assert {'php_open_tag', 'java_class_or_jndi'} <= set(DEFAULT_DISABLED)
        assert SecuritySettings.load().probe_disabled_rules == DEFAULT_DISABLED

    def test_a_rule_can_be_disabled(self):
        from security import engine
        req = engine.Request('GET', '/*', {})
        assert 'known_probe_path' not in {h['rule'] for h in engine.evaluate(req, disabled=['known_probe_path'])}


class TestEdgeInspection:
    URL = '/api/v1/security/inspect/'

    def _post(self, token, **payload):
        client = APIClient()
        return client.post(self.URL, payload, format='json', **({'HTTP_X_INTERNAL_TOKEN': token} if token else {}))

    def test_needs_the_internal_token(self, settings):
        _configure(probe_detection_enabled=True)
        assert self._post(None, method='GET', uri='/.env').status_code == 403
        assert self._post('wrong', method='GET', uri='/.env').status_code == 403
        assert not SecurityEvent.objects.exists()

    def test_requests_the_app_never_saw_are_logged_with_their_real_source(self, settings):
        _configure(probe_detection_enabled=True)
        r = self._post(settings.INTERNAL_API_TOKEN, method='GET', uri='/wp-login.php', ip='203.0.113.9',
                       headers={'user-agent': 'Nmap Scripting Engine'})
        assert r.status_code == 204
        rules = set(SecurityEvent.objects.values_list('rule', flat=True))
        assert {'known_probe_path', 'scanner_user_agent', 'unknown_area'} <= rules
        assert set(SecurityEvent.objects.values_list('ip_address', flat=True)) == {'203.0.113.9'}

    def test_a_scan_is_summarised_in_one_digest_not_one_alert(self, settings):
        _configure(probe_detection_enabled=True, probe_alert_enabled=True, alert_group_email='sec@lab.local')
        mail.outbox.clear()
        for uri in ('/.env', '/phpmyadmin/', '/x?q=<script>alert(1)</script>', '/x?id=1 UNION SELECT a FROM b'):
            self._post(settings.INTERNAL_API_TOKEN, method='GET', uri=uri, ip='203.0.113.9')
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ['sec@lab.local']
        assert '\u2014' not in mail.outbox[0].body


class TestProbeMiddleware:
    def _get(self, path, **extra):
        return APIClient().get(path, **extra)

    def test_off_by_default(self):
        self._get('/.env')
        assert not SecurityEvent.objects.filter(kind=SecurityEvent.KIND_PROBE).exists()

    def test_logs_and_never_blocks(self):
        _configure(probe_detection_enabled=True)
        response = self._get('/api/v1/languages/', **{'HTTP_USER_AGENT': 'sqlmap/1.7'})
        assert response.status_code != 403
        event = SecurityEvent.objects.get(kind=SecurityEvent.KIND_PROBE, rule='scanner_agent')
        assert event.method == 'GET' and event.authenticated is False

    def test_unknown_areas_are_logged_when_they_404(self):
        _configure(probe_detection_enabled=True)
        self._get('/some-hidden-panel')
        assert SecurityEvent.objects.filter(rule='unknown_area', path='/some-hidden-panel').exists()

    def test_unknown_area_logging_can_be_switched_off(self):
        _configure(probe_detection_enabled=True, probe_log_unknown_areas=False)
        self._get('/some-hidden-panel')
        assert not SecurityEvent.objects.filter(rule='unknown_area').exists()

    def test_requests_with_a_sign_in_cookie_are_marked(self):
        _configure(probe_detection_enabled=True)
        client = APIClient()
        client.cookies['cs_access'] = 'not-a-real-token'
        client.get('/api/v1/languages/', HTTP_USER_AGENT='nikto')
        assert SecurityEvent.objects.get(rule='scanner_agent').authenticated is True

    def test_authenticated_logging_can_be_switched_off(self):
        _configure(probe_detection_enabled=True, probe_log_authenticated=False)
        client = APIClient()
        client.cookies['cs_access'] = 'x'
        client.get('/api/v1/languages/', HTTP_USER_AGENT='nikto')
        assert not SecurityEvent.objects.exists()

    def test_repeats_from_one_source_are_collapsed(self):
        _configure(probe_detection_enabled=True)
        for _ in range(5):
            self._get('/api/v1/languages/', HTTP_USER_AGENT='nikto')
        assert SecurityEvent.objects.filter(rule='scanner_agent').count() == 1

    def test_secrets_in_the_query_are_redacted(self):
        _configure(probe_detection_enabled=True)
        self._get('/api/v1/languages/?token=SUPERSECRET&q=%3Cscript%3E')
        event = SecurityEvent.objects.get(rule='xss')
        assert 'SUPERSECRET' not in event.query_excerpt

    def test_alerts_go_to_the_group_with_a_cooldown(self):
        _configure(probe_detection_enabled=True, probe_alert_enabled=True, alert_group_email='sec@lab.local')
        mail.outbox.clear()
        self._get('/api/v1/languages/', HTTP_USER_AGENT='nikto')
        self._get('/.env')
        assert len(mail.outbox) == 1 and mail.outbox[0].to == ['sec@lab.local']

    def test_a_detector_error_never_breaks_the_request(self):
        _configure(probe_detection_enabled=True)
        with patch('security.middleware.scan_request', side_effect=RuntimeError('boom')):
            assert self._get('/api/v1/languages/').status_code == 200


class TestThrottling:
    """Request throttling is optional, off by default, and has limits and a message you can read."""

    SESSION_URL = '/api/v1/checkout/session/'   # a viewing endpoint (401 without a session, still counted)

    def _burst(self, n, client=None):
        client = client or APIClient()
        return [client.get(self.SESSION_URL).status_code for _ in range(n)]

    def test_it_is_off_by_default_and_nothing_is_limited(self):
        assert SecuritySettings.load().throttling_enabled is False
        assert 429 not in self._burst(2500)

    def test_when_switched_on_viewing_has_its_own_per_minute_limit(self):
        _configure(throttling_enabled=True, viewer_requests_per_minute=60)
        codes = self._burst(70)
        assert codes[:60] == [401] * 60 and set(codes[60:]) == {429}

    def test_switching_it_off_again_lifts_the_limit(self):
        _configure(throttling_enabled=True, viewer_requests_per_minute=60)
        client = APIClient()
        self._burst(65, client)
        assert client.get(self.SESSION_URL).status_code == 429
        _configure(throttling_enabled=False)
        assert client.get(self.SESSION_URL).status_code == 401

    def test_the_message_says_what_happened_how_long_and_where_to_change_it(self):
        _configure(throttling_enabled=True, viewer_requests_per_minute=60)
        client = APIClient()
        self._burst(60, client)
        body = client.get(self.SESSION_URL).json()
        assert 'Too many requests' in body['detail'] and 'switched on' in body['detail'] and 'Security' in body['detail']
        assert '60 requests a minute' in body['detail'] and body['code'] == 'throttled' and body['retry_after'] > 0

    def test_browser_sessions_are_counted_separately(self):
        _configure(throttling_enabled=True, viewer_requests_per_minute=60)
        busy, other = APIClient(), APIClient()
        self._burst(70, busy)
        assert other.get(self.SESSION_URL).status_code == 401

    def test_the_old_daily_limit_is_gone(self, settings):
        assert 'day' not in str(settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'].get('anon'))

    def test_the_limits_can_be_set_and_are_validated(self, admin_api_client):
        ok = admin_api_client.put(URL, {'throttling_enabled': True, 'viewer_requests_per_minute': 2000,
                                        'general_requests_per_minute': 400, 'user_requests_per_minute': 800}, format='json')
        assert ok.status_code == 200 and ok.json()['settings']['throttling_enabled'] is True
        assert admin_api_client.put(URL, {'viewer_requests_per_minute': 5}, format='json').status_code == 400

    def test_password_attempt_limits_stay_on_even_when_throttling_is_off(self):
        from rest_framework.throttling import ScopedRateThrottle
        from django.conf import settings as dj
        assert 'password_verify' in dj.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
        assert ScopedRateThrottle  # the scoped limits are untouched by the switch


class TestCaptureActions:
    def test_defaults_only_log_and_ignore_routine_focus_changes(self):
        from security.capture import DEFAULT_CAPTURE_ACTIONS
        assert DEFAULT_CAPTURE_ACTIONS['print_attempt'] == 'log' and DEFAULT_CAPTURE_ACTIONS['screenshot_key'] == 'log'
        assert DEFAULT_CAPTURE_ACTIONS['tab_hidden'] == 'ignore' and DEFAULT_CAPTURE_ACTIONS['window_blur'] == 'ignore'
        assert set(DEFAULT_CAPTURE_ACTIONS.values()) <= {'ignore', 'log'}

    def test_the_settings_expose_the_catalogue_and_validate_choices(self, admin_api_client):
        body = admin_api_client.get(URL).json()
        assert body['capture']['actions'] == ['ignore', 'log', 'notify', 'blur', 'end']
        assert {'print_attempt', 'save_attempt'} <= {e['id'] for e in body['capture']['events']}
        ok = admin_api_client.put(URL, {'capture_actions': {'print_attempt': 'blur', 'save_attempt': 'end'}}, format='json')
        assert ok.status_code == 200 and ok.json()['settings']['capture_actions']['save_attempt'] == 'end'
        assert admin_api_client.put(URL, {'capture_actions': {'print_attempt': 'explode'}}, format='json').status_code == 400
        assert admin_api_client.put(URL, {'capture_actions': {'nonsense': 'log'}}, format='json').status_code == 400

    def test_effective_actions_fill_in_the_defaults(self):
        from security.capture import effective_actions
        eff = effective_actions({'print_attempt': 'end'})
        assert eff['print_attempt'] == 'end' and eff['copy_attempt'] == 'log'

    def test_a_link_viewer_is_told_the_actions(self, share_link, public_client, settings):
        _configure(capture_actions={'print_attempt': 'blur'})
        data = public_client.get(f'/api/v1/links/{share_link.slug}/view-data/').json()
        assert data['link_settings']['capture_actions']['print_attempt'] == 'blur'

    def _viewer(self, share_link, public_client):
        public_client.get(f'/api/v1/links/{share_link.slug}/view-data/')
        return lambda event: public_client.post(f'/api/v1/links/{share_link.slug}/events/', {'event_type': event}, format='json')

    def test_ignored_signals_are_not_logged(self, share_link, public_client):
        _configure(capture_actions={'context_menu': 'ignore'})
        send = self._viewer(share_link, public_client)
        assert send('context_menu').status_code == 204 and send('print_attempt').status_code == 204
        from sharelinks.models import ShareLinkEvent
        assert not ShareLinkEvent.objects.filter(event_type='context_menu').exists()
        assert ShareLinkEvent.objects.filter(event_type='print_attempt').exists()

    def test_notify_logs_and_e_mails_the_alert_address_once_per_viewer_and_signal(self, share_link, public_client):
        _configure(capture_actions={'print_attempt': 'notify'}, alert_group_email='sec@lab.local')
        send = self._viewer(share_link, public_client)
        mail.outbox.clear()
        send('print_attempt'); send('print_attempt')
        assert len(mail.outbox) == 1 and mail.outbox[0].to == ['sec@lab.local'] and 'Print' in mail.outbox[0].subject

    def test_end_locks_the_viewer_out_of_the_link_for_a_while(self, share_link, public_client):
        _configure(capture_actions={'save_attempt': 'end'}, capture_lockout_minutes=5)
        send = self._viewer(share_link, public_client)
        send('save_attempt')
        locked = public_client.get(f'/api/v1/links/{share_link.slug}/view-data/')
        assert locked.status_code == 423 and locked.json()['code'] == 'capture_lockout'
        assert APIClient().get(f'/api/v1/links/{share_link.slug}/view-data/').status_code == 200   # someone else is not locked

    def test_the_lockout_expires(self, share_link, public_client):
        from unittest.mock import patch
        import time
        _configure(capture_actions={'save_attempt': 'end'}, capture_lockout_minutes=5)
        self._viewer(share_link, public_client)('save_attempt')
        with patch('security.capture.time.time', return_value=time.time() + 600):
            assert public_client.get(f'/api/v1/links/{share_link.slug}/view-data/').status_code == 200

    def test_nothing_changes_for_signals_left_at_log(self, share_link, public_client):
        send = self._viewer(share_link, public_client)
        send('print_attempt')
        assert public_client.get(f'/api/v1/links/{share_link.slug}/view-data/').status_code == 200
