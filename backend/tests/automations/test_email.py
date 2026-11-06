from unittest.mock import patch

import pytest
from django.core import mail
from django.core.cache import cache
from rest_framework import status

from automations.crypto import decrypt_secret, encrypt_secret
from automations.models import EmailSettings
from automations.services import dispatch_automation_event, send_view_email
from core.mailer import send_app_mail

URL = '/api/v1/notifications/email-settings/'


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


@pytest.fixture
def notify_link(share_link, user):
    share_link.receive_email_notification = True
    share_link.save()
    return share_link


def _payload(link, user, **extra):
    return {'organization_id': str(user.organization_id), 'owner_user_id': str(user.id),
            'share_link_id': str(link.id), 'document_name': 'Deck.pdf', **extra}


@pytest.mark.django_db
class TestViewEmail:
    def test_owner_is_emailed_when_enabled(self, notify_link, user):
        mail.outbox.clear()
        assert send_view_email('document_viewed', _payload(notify_link, user, viewer_email='v@x.io')) is True
        assert [m.to for m in mail.outbox] == [[user.email]]
        assert 'v@x.io' in mail.outbox[0].body

    def test_nothing_is_sent_when_the_link_has_it_off(self, share_link, user):
        mail.outbox.clear()
        assert send_view_email('document_viewed', _payload(share_link, user)) is False
        assert mail.outbox == []

    def test_other_events_do_not_send(self, notify_link, user):
        mail.outbox.clear()
        assert send_view_email('access_granted', _payload(notify_link, user)) is False

    def test_repeat_views_are_collapsed(self, notify_link, user):
        mail.outbox.clear()
        for _ in range(3):
            send_view_email('document_viewed', _payload(notify_link, user, viewer_email='v@x.io'))
        assert len(mail.outbox) == 1

    def test_dispatch_sends_it(self, notify_link, user):
        mail.outbox.clear()
        dispatch_automation_event('document_viewed', _payload(notify_link, user))
        assert len(mail.outbox) == 1

    def test_mail_failure_does_not_raise(self, notify_link, user):
        with patch('core.mailer.send_app_mail', side_effect=OSError('smtp down')):
            assert send_view_email('document_viewed', _payload(notify_link, user)) is False


@pytest.mark.django_db
class TestEmailSettingsApi:
    def test_admin_only(self, api_client, public_client):
        assert api_client.get(URL).status_code == status.HTTP_403_FORBIDDEN
        assert public_client.get(URL).status_code in (401, 403)

    def test_defaults_to_the_environment(self, admin_api_client):
        body = admin_api_client.get(URL).json()
        assert body['source'] == 'environment' and body['enabled'] is False

    def test_save_never_returns_the_password(self, admin_api_client):
        response = admin_api_client.put(URL, {
            'enabled': True, 'host': 'smtp.example.com', 'port': 587, 'username': 'u', 'password': 'hunter2-secret',
            'use_tls': True, 'from_email': 'noreply@example.com'}, format='json')
        assert response.status_code == 200, response.content
        body = response.json()
        assert body['has_password'] is True and 'password' not in body
        assert 'hunter2-secret' not in str(body)
        stored = EmailSettings.load()
        assert 'hunter2-secret' not in stored.password_encrypted
        assert stored.password == 'hunter2-secret'

    def test_blank_password_keeps_the_stored_one(self, admin_api_client):
        admin_api_client.put(URL, {'host': 'smtp.example.com', 'from_email': 'a@example.com', 'password': 'one'}, format='json')
        admin_api_client.put(URL, {'port': 2525, 'password': ''}, format='json')
        stored = EmailSettings.load()
        assert stored.password == 'one' and stored.port == 2525

    def test_validation(self, admin_api_client):
        both = admin_api_client.put(URL, {'use_tls': True, 'use_ssl': True}, format='json')
        assert both.status_code == 400
        missing = admin_api_client.put(URL, {'enabled': True}, format='json')
        assert missing.status_code == 400

    def test_test_message_goes_only_to_the_admin(self, admin_api_client, admin_user):
        mail.outbox.clear()
        response = admin_api_client.post(URL + 'test/')
        assert response.status_code == 200
        assert [m.to for m in mail.outbox] == [[admin_user.email]]

    def test_test_failure_reports_only_the_error_type(self, admin_api_client):
        with patch('automations.views.send_app_mail', side_effect=OSError('535 bad password for secret-user')):
            response = admin_api_client.post(URL + 'test/')
        assert response.status_code == 400
        assert 'secret-user' not in response.json()['detail']


@pytest.mark.django_db
class TestMailer:
    def test_uses_environment_when_ui_settings_are_off(self):
        mail.outbox.clear()
        send_app_mail('s', 'b', 'a@x.io')
        assert len(mail.outbox) == 1

    def test_ui_settings_take_over_and_set_the_sender(self):
        config = EmailSettings(enabled=True, host='smtp.example.com', port=587, use_tls=True, from_email='ui@example.com')
        config.set_password('pw')
        config.save()
        sent = {}

        class Fake:
            def __init__(self, **kwargs):
                sent.update(kwargs)

            def send_messages(self, messages):
                sent['from'] = messages[0].from_email
                return len(messages)

            def open(self):
                return True

            def close(self):
                pass

        with patch('core.mailer.PinnedSMTPBackend', Fake):
            send_app_mail('s', 'b', 'a@x.io')
        assert sent['host'] == 'smtp.example.com' and sent['password'] == 'pw'
        assert sent['from'] == 'ui@example.com'

    def test_blocked_smtp_host_fails_closed(self):
        from rest_framework import serializers
        from core.mailer import PinnedSMTPBackend
        backend = PinnedSMTPBackend(host='10.0.0.5', port=25)
        with patch('core.mailer.resolve_destination', side_effect=serializers.ValidationError('blocked')):
            with pytest.raises(serializers.ValidationError):
                backend.open()


def test_encryption_round_trip():
    token = encrypt_secret('s3cret')
    assert token and 's3cret' not in token
    assert decrypt_secret(token) == 's3cret'
    assert decrypt_secret('garbage') == ''


@pytest.mark.django_db
class TestMailSecurity:
    base = {'enabled': True, 'host': 'mail.example.com', 'port': 25, 'from_email': 'a@example.com'}

    def test_unencrypted_needs_an_explicit_opt_in(self, admin_api_client):
        plain = dict(self.base, use_tls=False, use_ssl=False)
        refused = admin_api_client.put(URL, plain, format='json')
        assert refused.status_code == 400 and 'allow_insecure' in refused.json()
        assert admin_api_client.put(URL, dict(plain, allow_insecure=True), format='json').status_code == 200

    def test_credentials_are_not_sent_over_a_connection_that_is_not_encrypted(self, admin_api_client):
        response = admin_api_client.put(URL, dict(self.base, use_tls=False, use_ssl=False, username='u', password='p'), format='json')
        assert response.status_code == 400

    def test_encrypted_connections_need_no_opt_in(self, admin_api_client):
        assert admin_api_client.put(URL, dict(self.base, port=587, use_tls=True), format='json').status_code == 200

    @pytest.mark.parametrize('host', ['smtp://evil.example.com', 'host.example.com:25', 'a b', 'x/../y', '-bad'])
    def test_hosts_must_be_plain_names(self, admin_api_client, host):
        assert admin_api_client.put(URL, dict(self.base, use_tls=True, host=host), format='json').status_code == 400

    def test_inbox_without_ssl_also_needs_the_opt_in(self, admin_api_client):
        response = admin_api_client.put(URL, {'imap_host': 'imap.example.com', 'imap_use_ssl': False}, format='json')
        assert response.status_code == 400

    def test_inbox_password_is_write_only_and_encrypted(self, admin_api_client):
        response = admin_api_client.put(URL, {'imap_host': 'imap.example.com', 'imap_username': 'u',
                                              'imap_password': 'inbox-secret', 'imap_test_address': 'u@example.com'}, format='json')
        assert response.status_code == 200
        body = response.json()
        assert body['has_imap_password'] is True and 'imap_password' not in body
        assert 'inbox-secret' not in str(body)
        stored = EmailSettings.load()
        assert 'inbox-secret' not in stored.imap_password_encrypted and stored.imap_password == 'inbox-secret'

    def test_changes_are_audited_without_values(self, admin_api_client):
        from security.models import SecurityEvent
        admin_api_client.put(URL, dict(self.base, use_tls=True, port=587, password='hunter2'), format='json')
        event = SecurityEvent.objects.get(kind=SecurityEvent.KIND_SETTINGS, rule='mail_settings')
        assert 'password' in event.detail['changed'] and 'host' in event.detail['changed']
        assert 'hunter2' not in str(event.detail) and event.actor


@pytest.mark.django_db
class TestReceiveTest:
    URL = URL + 'test-receive/'

    def _configure(self):
        config = EmailSettings(imap_host='imap.example.com', imap_test_address='inbox@example.com', imap_username='inbox')
        config.save()

    def test_admin_only(self, api_client):
        assert api_client.post(self.URL).status_code == status.HTTP_403_FORBIDDEN

    def test_needs_the_inbox_settings(self, admin_api_client):
        assert admin_api_client.post(self.URL).status_code == 400

    def test_reports_success_and_sends_a_token(self, admin_api_client):
        self._configure()
        mail.outbox.clear()
        with patch('automations.views.check_inbox_for_token', return_value=(True, 1.5)) as check:
            response = admin_api_client.post(self.URL)
        assert response.status_code == 200 and response.json()['seconds'] == 1.5
        assert [m.to for m in mail.outbox] == [['inbox@example.com']]
        token = check.call_args.args[1]
        assert token in mail.outbox[0].subject

    def test_reports_when_the_message_never_arrives(self, admin_api_client):
        self._configure()
        with patch('automations.views.check_inbox_for_token', return_value=(False, 20.0)):
            response = admin_api_client.post(self.URL)
        assert response.status_code == 400 and response.json()['stage'] == 'arrival'

    def test_inbox_errors_are_reported_by_type_only(self, admin_api_client):
        self._configure()
        with patch('automations.views.check_inbox_for_token', side_effect=OSError('login failed for inbox user secret')):
            response = admin_api_client.post(self.URL)
        assert response.status_code == 400 and response.json()['stage'] == 'receive'
        assert 'secret' not in response.json()['detail']

    def test_send_failures_are_reported_before_the_inbox_is_touched(self, admin_api_client):
        self._configure()
        with patch('automations.views.send_app_mail', side_effect=OSError('smtp down')), \
             patch('automations.views.check_inbox_for_token') as check:
            response = admin_api_client.post(self.URL)
        assert response.json()['stage'] == 'send' and not check.called

    def test_rate_limited(self, admin_api_client):
        self._configure()
        with patch('automations.views.check_inbox_for_token', return_value=(True, 1.0)):
            codes = [admin_api_client.post(self.URL).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]


class TestInboxCheck:
    class FakeIMAP:
        def __init__(self, hits_after):
            self.searches, self.hits_after, self.deleted, self.expunged, self.out = 0, hits_after, [], False, False

        def select(self, box):
            assert box == 'INBOX'

        def search(self, charset, *criteria):
            self.searches += 1
            assert criteria[0] == 'SUBJECT'
            return 'OK', [b'7' if self.searches > self.hits_after else b'']

        def store(self, number, op, flag):
            self.deleted.append((number, op, flag))

        def expunge(self):
            self.expunged = True

        def noop(self):
            pass

        def logout(self):
            self.out = True

    def test_finds_the_token_deletes_only_that_message_and_logs_out(self):
        from core.mailer import check_inbox_for_token
        fake = self.FakeIMAP(hits_after=0)
        with patch('core.mailer._connect_imap', return_value=fake):
            found, _seconds = check_inbox_for_token(EmailSettings(), 'receive-test-abc')
        assert found and fake.deleted == [(b'7', '+FLAGS', '\\Deleted')] and fake.expunged and fake.out

    def test_gives_up_after_the_wait(self):
        from core.mailer import check_inbox_for_token
        fake = self.FakeIMAP(hits_after=99)
        with patch('core.mailer._connect_imap', return_value=fake), patch('core.mailer.time.sleep'):
            with patch('core.mailer.time.monotonic', side_effect=[0, 0, 5, 30]):
                found, _ = check_inbox_for_token(EmailSettings(), 'x', wait_seconds=20)
        assert not found and not fake.deleted and fake.out

    def test_a_blocked_inbox_host_fails_closed(self):
        from rest_framework import serializers
        from core.mailer import _connect_imap
        with patch('core.mailer.resolve_destination', side_effect=serializers.ValidationError('blocked')):
            with pytest.raises(serializers.ValidationError):
                _connect_imap(EmailSettings(imap_host='10.0.0.5', imap_port=993))
