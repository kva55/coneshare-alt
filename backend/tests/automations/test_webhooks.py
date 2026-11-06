import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from automations.constants import WEBHOOK_EVENTS
from automations.crypto import derive_webhook_secret
from automations.models import WebhookDelivery, WebhookEndpoint
from automations.services import dispatch_automation_event, public_payload
from automations.tasks import build_request, deliver_webhook_task
from core.ssrf import PinnedResponse

URL = '/api/v1/webhooks/'
GOOD_URL = 'https://hooks.example.com/in'


@pytest.fixture(autouse=True)
def public_resolution():
    """Webhook URLs resolve to a public address in these tests."""
    with patch('automations.serializers.validate_outbound_url', side_effect=lambda u: u), \
         patch('automations.tasks.validate_outbound_url', side_effect=lambda u: u):
        yield


@pytest.fixture
def endpoint(admin_user, organization):
    return WebhookEndpoint.objects.create(
        organization=organization, created_by=admin_user, name='Hook', url=GOOD_URL,
        events=['file_checked_out', 'file_requested'])


@pytest.mark.django_db
class TestApi:
    def test_members_cannot_manage_webhooks(self, api_client):
        assert api_client.get(URL).status_code == status.HTTP_403_FORBIDDEN
        assert api_client.post(URL, {'name': 'x', 'url': GOOD_URL, 'events': []}, format='json').status_code == 403

    def test_public_cannot(self, public_client):
        assert public_client.get(URL).status_code in (401, 403)

    def test_create_returns_secret_once_and_lists_without_it(self, admin_api_client):
        response = admin_api_client.post(URL, {'name': 'CI', 'url': GOOD_URL, 'events': ['file_checked_out']}, format='json')
        assert response.status_code == 201, response.content
        body = response.json()
        assert body['signing_secret'].startswith('whsec_')
        listing = admin_api_client.get(URL).json()['results']
        assert 'signing_secret' not in listing[0]

    def test_unknown_events_are_rejected(self, admin_api_client):
        response = admin_api_client.post(URL, {'name': 'x', 'url': GOOD_URL, 'events': ['slack_message']}, format='json')
        assert response.status_code == 400

    def test_http_urls_are_rejected_outside_debug(self, admin_api_client, settings):
        settings.DEBUG = False
        response = admin_api_client.post(URL, {'name': 'x', 'url': 'http://hooks.example.com/in', 'events': []}, format='json')
        assert response.status_code == 400

    def test_blocked_addresses_are_rejected(self, admin_api_client):
        from rest_framework import serializers
        with patch('automations.serializers.validate_outbound_url',
                   side_effect=serializers.ValidationError('Private network addresses are not permitted.')):
            response = admin_api_client.post(URL, {'name': 'x', 'url': 'https://10.0.0.5/in', 'events': []}, format='json')
        assert response.status_code == 400

    def test_other_organizations_cannot_see_an_endpoint(self, endpoint):
        from core.models import Organization, User
        other_org = Organization.objects.create(name='Other')
        other = User.objects.create_user(username='o', email='o@x.io', password='pw', organization=other_org, role='admin')
        client = APIClient()
        client.force_authenticate(other)
        assert client.get(f'{URL}{endpoint.id}/secret/').status_code == 404

    def test_rotate_changes_the_secret(self, admin_api_client, endpoint):
        before = admin_api_client.get(f'{URL}{endpoint.id}/secret/').json()['signing_secret']
        after = admin_api_client.post(f'{URL}{endpoint.id}/secret/').json()['signing_secret']
        assert before != after and before == derive_webhook_secret(str(endpoint.id), 1)

    def test_events_catalogue_only_lists_generic_events(self, api_client):
        names = {e['name'] for e in api_client.get(URL + 'events/').json()['events']}
        assert names == set(WEBHOOK_EVENTS)
        assert not names & {'slack', 'discord', 'wechat', 'feishu'}


@pytest.mark.django_db
class TestSecrets:
    def test_secret_is_derived_not_stored(self, endpoint):
        assert endpoint.signing_secret not in json.dumps(list(WebhookEndpoint.objects.values()), default=str)

    def test_secrets_differ_per_endpoint_and_version(self):
        assert derive_webhook_secret('a', 1) != derive_webhook_secret('b', 1)
        assert derive_webhook_secret('a', 1) != derive_webhook_secret('a', 2)


@pytest.mark.django_db
class TestDispatch:
    def test_only_subscribed_active_endpoints_get_deliveries(self, endpoint, organization):
        WebhookEndpoint.objects.create(organization=organization, name='Off', url=GOOD_URL,
                                       events=['file_checked_out'], is_active=False)
        WebhookEndpoint.objects.create(organization=organization, name='Other', url=GOOD_URL, events=['document_viewed'])
        with patch('automations.tasks.deliver_webhook_task.delay') as delay:
            count = dispatch_automation_event('file_checked_out', {'organization_id': str(organization.id), 'file': 'a.pdf'})
        assert count == 1 and delay.call_count == 1
        assert WebhookDelivery.objects.get().endpoint == endpoint

    def test_other_organizations_events_are_not_delivered(self, endpoint):
        with patch('automations.tasks.deliver_webhook_task.delay'):
            assert dispatch_automation_event('file_checked_out', {'organization_id': 'someone-else'}) == 0

    def test_events_without_organization_are_dropped(self, endpoint):
        assert dispatch_automation_event('file_checked_out', {'file': 'a.pdf'}) == 0

    def test_internal_identifiers_are_not_sent(self):
        cleaned = public_payload({'organization_id': 'o', 'owner_user_id': 'u', 'file': 'a.pdf'})
        assert cleaned == {'file': 'a.pdf'}


@pytest.mark.django_db
class TestDelivery:
    def _delivery(self, endpoint):
        return WebhookDelivery.objects.create(endpoint=endpoint, event_type='file_checked_out',
                                              payload={'file': 'a.pdf'}, idempotency_key='k-1')

    def test_body_is_signed_with_the_endpoint_secret(self, endpoint):
        body, headers = build_request(self._delivery(endpoint))
        expected = 'sha256=' + hmac.new(endpoint.signing_secret.encode(), body, hashlib.sha256).hexdigest()
        assert headers['X-Coneshare-Signature'] == expected
        assert json.loads(body)['event'] == 'file_checked_out'
        assert json.loads(body)['data'] == {'file': 'a.pdf'}

    def test_success(self, endpoint):
        delivery = self._delivery(endpoint)
        with patch('automations.tasks.pinned_request', return_value=PinnedResponse(200, 'ok', {})) as request:
            deliver_webhook_task(str(delivery.id))
        delivery.refresh_from_db()
        assert delivery.status == 'success' and delivery.response_code == 200
        sent = request.call_args.kwargs
        assert sent['body'] == build_request(delivery)[0]

    def test_server_error_is_retried_then_gives_up(self, endpoint):
        delivery = self._delivery(endpoint)
        with patch('automations.tasks.pinned_request', return_value=PinnedResponse(500, 'boom', {})), \
             patch('automations.tasks.deliver_webhook_task.apply_async') as retry:
            deliver_webhook_task(str(delivery.id))
            delivery.refresh_from_db()
            assert delivery.status == 'failed' and retry.called
            deliver_webhook_task(str(delivery.id))
            deliver_webhook_task(str(delivery.id))
        delivery.refresh_from_db()
        assert delivery.status == 'dead_letter' and delivery.attempt_count == 3

    def test_blocked_destination_is_not_retried(self, endpoint):
        from rest_framework import serializers
        delivery = self._delivery(endpoint)
        with patch('automations.tasks.pinned_request', side_effect=serializers.ValidationError('blocked')), \
             patch('automations.tasks.deliver_webhook_task.apply_async') as retry:
            deliver_webhook_task(str(delivery.id))
        delivery.refresh_from_db()
        assert delivery.status == 'dead_letter' and not retry.called

    def test_disabled_endpoint_is_not_called(self, endpoint):
        delivery = self._delivery(endpoint)
        endpoint.is_active = False
        endpoint.save()
        with patch('automations.tasks.pinned_request') as request:
            deliver_webhook_task(str(delivery.id))
        assert not request.called

    def test_test_endpoint_queues_a_signed_event(self, admin_api_client, endpoint):
        with patch('automations.views.deliver_webhook_task.delay'):
            response = admin_api_client.post(f'{URL}{endpoint.id}/test/')
        assert response.status_code == 202
        assert WebhookDelivery.objects.get().event_type == 'test'
