"""Allow-list authentication of share links: invitation, access code, enumeration safety, log."""
import re
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework import status

from sharelinks.access import GENERIC_REQUEST_MESSAGE, INVALID_CODE_MESSAGE
from sharelinks.events import EventType
from sharelinks.models import AccessRequestToken, ShareLinkAllowedEmail, ShareLinkEvent

ALLOWED = 'guest@example.org'
OTHER = 'stranger@example.net'


@pytest.fixture(autouse=True)
def allowlist_on(settings):
    settings.SHARELINK_REQUIRE_ALLOWLIST = True


@pytest.fixture
def link(share_link):
    return share_link


def _request_url(link):
    return f'/api/v1/links/{link.slug}/access/request/'


def _verify_url(link):
    return f'/api/v1/links/{link.slug}/access/verify/'


def _allow(api_client, link, *emails):
    response = api_client.post(f'/api/v1/share-links/{link.id}/allowed-emails/', {'emails': list(emails)}, format='json')
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response


def _code_from_mail(message):
    match = re.search(r'\b([A-HJKMNP-Z2-9]{8})\b', message.body)
    assert match, message.body
    return match.group(1)


def _get_code(public_client, link, email=ALLOWED):
    mail.outbox.clear()
    public_client.post(_request_url(link), {'email': email}, format='json')
    assert len(mail.outbox) == 1
    return _code_from_mail(mail.outbox[0])


@pytest.mark.django_db
class TestGate:
    def test_content_requires_allowlist_session(self, public_client, link):
        response = public_client.get(f'/api/v1/links/{link.slug}/view-data/')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()['protectionType'] == 'allowlist'

    def test_opening_without_access_logs_one_attempt_per_session(self, public_client, link):
        for _ in range(3):
            public_client.get(f'/api/v1/links/{link.slug}/view-data/')
        attempts = ShareLinkEvent.objects.filter(share_link=link, event_type=EventType.ACCESS_ATTEMPT)
        assert attempts.count() == 1

    def test_meta_advertises_allowlist(self, public_client, link):
        meta = public_client.get(f'/api/v1/links/{link.slug}/').json()
        assert meta['requires_allowlist'] is True
        assert meta['is_restricted'] is True

    def test_full_flow_grants_access(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        code = _get_code(public_client, link)
        response = public_client.post(_verify_url(link), {'email': ALLOWED, 'code': code}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert public_client.get(f'/api/v1/links/{link.slug}/view-data/').status_code == status.HTTP_200_OK

    def test_policy_off_removes_gate(self, public_client, link, settings):
        settings.SHARELINK_REQUIRE_ALLOWLIST = False
        assert public_client.get(f'/api/v1/links/{link.slug}/view-data/').status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestInvitation:
    def test_adding_emails_sends_invitation(self, api_client, link):
        mail.outbox.clear()
        response = _allow(api_client, link, ALLOWED)
        assert len(response.json()['added']) == 1
        assert [m.to for m in mail.outbox] == [[ALLOWED]]
        entry = ShareLinkAllowedEmail.objects.get(share_link=link, email=ALLOWED)
        assert entry.invitation_sent_at is not None

    def test_invitation_never_contains_a_code(self, api_client, link):
        mail.outbox.clear()
        _allow(api_client, link, ALLOWED)
        assert not re.search(r'\b[A-HJKMNP-Z2-9]{8}\b', mail.outbox[0].body.replace(link.slug, ''))

    def test_duplicates_and_invalid_are_reported(self, api_client, link):
        _allow(api_client, link, ALLOWED)
        response = api_client.post(
            f'/api/v1/share-links/{link.id}/allowed-emails/',
            {'emails': [ALLOWED.upper(), 'not-an-email']}, format='json',
        )
        body = response.json()
        assert body['existing'] == [ALLOWED]
        assert body['invalid'] == ['not-an-email']

    def test_only_owner_side_can_manage(self, public_client, link):
        response = public_client.post(f'/api/v1/share-links/{link.id}/allowed-emails/', {'emails': [ALLOWED]}, format='json')
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


@pytest.mark.django_db
class TestEnumerationSafety:
    def test_response_identical_for_listed_and_unlisted(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        mail.outbox.clear()
        listed = public_client.post(_request_url(link), {'email': ALLOWED}, format='json')
        unlisted = public_client.post(_request_url(link), {'email': OTHER}, format='json')
        assert listed.status_code == unlisted.status_code == status.HTTP_200_OK
        assert listed.json() == unlisted.json()
        assert listed.json()['message'] == GENERIC_REQUEST_MESSAGE

    def test_unlisted_gets_no_email(self, public_client, link):
        mail.outbox.clear()
        public_client.post(_request_url(link), {'email': OTHER}, format='json')
        assert mail.outbox == []

    def test_unlisted_cannot_verify_any_code(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        code = _get_code(public_client, link)
        response = public_client.post(_verify_url(link), {'email': OTHER, 'code': code}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()['message'] == INVALID_CODE_MESSAGE

    def test_wrong_code_message_matches_missing_code_message(self, public_client, link):
        response = public_client.post(_verify_url(link), {'email': OTHER, 'code': 'AAAAAAAA'}, format='json')
        assert response.json()['message'] == INVALID_CODE_MESSAGE


@pytest.mark.django_db
class TestCodeRules:
    def test_code_is_single_use(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        code = _get_code(public_client, link)
        body = {'email': ALLOWED, 'code': code}
        assert public_client.post(_verify_url(link), body, format='json').status_code == status.HTTP_200_OK
        from rest_framework.test import APIClient
        assert APIClient().post(_verify_url(link), body, format='json').status_code == status.HTTP_400_BAD_REQUEST

    def test_code_is_stored_hashed(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        code = _get_code(public_client, link)
        token = AccessRequestToken.objects.get(share_link=link, email=ALLOWED)
        assert code not in token.token_hash

    def test_expired_code_is_rejected(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        code = _get_code(public_client, link)
        AccessRequestToken.objects.filter(share_link=link).update(expires_at=timezone.now() - timedelta(minutes=1))
        response = public_client.post(_verify_url(link), {'email': ALLOWED, 'code': code}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_attempt_cap_locks_the_code(self, api_client, public_client, link, settings):
        _allow(api_client, link, ALLOWED)
        code = _get_code(public_client, link)
        for _ in range(settings.ACCESS_CODE_MAX_ATTEMPTS):
            public_client.post(_verify_url(link), {'email': ALLOWED, 'code': 'WRONGCDE'}, format='json')
        response = public_client.post(_verify_url(link), {'email': ALLOWED, 'code': code}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_new_request_invalidates_previous_code(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        first = _get_code(public_client, link)
        second = _get_code(public_client, link)
        assert first != second
        response = public_client.post(_verify_url(link), {'email': ALLOWED, 'code': first}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_requests_are_rate_limited_per_email(self, api_client, public_client, link, settings):
        _allow(api_client, link, ALLOWED)
        mail.outbox.clear()
        for _ in range(settings.ACCESS_REQUESTS_PER_EMAIL + 3):
            public_client.post(_request_url(link), {'email': ALLOWED}, format='json')
        assert len(mail.outbox) <= settings.ACCESS_REQUESTS_PER_EMAIL
        assert ShareLinkEvent.objects.filter(share_link=link, event_type=EventType.ACCESS_REQUEST_THROTTLED).exists()


@pytest.mark.django_db
class TestRevocation:
    def test_revoke_takes_effect_on_existing_session(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        code = _get_code(public_client, link)
        public_client.post(_verify_url(link), {'email': ALLOWED, 'code': code}, format='json')
        assert public_client.get(f'/api/v1/links/{link.slug}/view-data/').status_code == status.HTTP_200_OK

        entry = ShareLinkAllowedEmail.objects.get(share_link=link, email=ALLOWED)
        assert api_client.delete(f'/api/v1/share-links/{link.id}/allowed-emails/{entry.id}/').status_code == status.HTTP_204_NO_CONTENT
        assert public_client.get(f'/api/v1/links/{link.slug}/view-data/').status_code == status.HTTP_401_UNAUTHORIZED

    def test_revoked_entry_gets_no_code(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        entry = ShareLinkAllowedEmail.objects.get(share_link=link, email=ALLOWED)
        api_client.delete(f'/api/v1/share-links/{link.id}/allowed-emails/{entry.id}/')
        mail.outbox.clear()
        public_client.post(_request_url(link), {'email': ALLOWED}, format='json')
        assert mail.outbox == []


@pytest.mark.django_db
class TestEventLog:
    def _types(self, link):
        return set(ShareLinkEvent.objects.filter(share_link=link).values_list('event_type', flat=True))

    def test_all_outcomes_are_logged(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        public_client.get(f'/api/v1/links/{link.slug}/view-data/')
        public_client.post(_request_url(link), {'email': OTHER}, format='json')
        code = _get_code(public_client, link)
        public_client.post(_verify_url(link), {'email': ALLOWED, 'code': code}, format='json')
        types = self._types(link)
        assert {
            EventType.INVITATION_SENT,
            EventType.ACCESS_REQUEST,
            EventType.ACCESS_REQUEST_DENIED,
            EventType.ACCESS_REQUEST_ALLOWED,
            EventType.ACCESS_GRANTED,
        } <= types

    def test_log_never_contains_the_code(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        code = _get_code(public_client, link)
        public_client.post(_verify_url(link), {'email': ALLOWED, 'code': code}, format='json')
        for event in ShareLinkEvent.objects.filter(share_link=link):
            assert code not in str(event.metadata)

    def test_owner_reads_and_filters_log(self, api_client, public_client, link):
        public_client.post(_request_url(link), {'email': OTHER}, format='json')
        response = api_client.get(f'/api/v1/share-links/{link.id}/events/', {'event_type': EventType.ACCESS_REQUEST_DENIED})
        assert response.status_code == status.HTTP_200_OK
        results = response.json()['results']
        assert results and all(r['event_type'] == EventType.ACCESS_REQUEST_DENIED for r in results)
        assert results[0]['email'] == OTHER

    def test_log_is_not_public(self, public_client, link):
        response = public_client.get(f'/api/v1/share-links/{link.id}/events/')
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_other_users_cannot_read_log(self, api_client, link, user2):
        from rest_framework.test import APIClient
        other = APIClient()
        other.force_authenticate(user2)
        assert other.get(f'/api/v1/share-links/{link.id}/events/').status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestViewerEvents:
    def _grant(self, api_client, public_client, link):
        _allow(api_client, link, ALLOWED)
        code = _get_code(public_client, link)
        public_client.post(_verify_url(link), {'email': ALLOWED, 'code': code}, format='json')

    def test_requires_authorised_session(self, public_client, link):
        response = public_client.post(f'/api/v1/links/{link.slug}/events/', {'event_type': 'print_attempt'}, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_records_client_event(self, api_client, public_client, link):
        self._grant(api_client, public_client, link)
        response = public_client.post(f'/api/v1/links/{link.slug}/events/', {'event_type': 'print_attempt'}, format='json')
        assert response.status_code == status.HTTP_204_NO_CONTENT
        event = ShareLinkEvent.objects.get(share_link=link, event_type='print_attempt')
        assert event.email == ALLOWED

    def test_rejects_server_only_types(self, api_client, public_client, link):
        self._grant(api_client, public_client, link)
        response = public_client.post(
            f'/api/v1/links/{link.slug}/events/', {'event_type': EventType.ACCESS_GRANTED}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
