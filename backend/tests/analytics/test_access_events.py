import pytest
from datetime import timedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from sharelinks.events import EventType, record_event
from sharelinks.models import ShareLink, ShareLinkEvent


@pytest.fixture
def events(share_link):
    record_event(share_link, EventType.ACCESS_REQUEST, None, 'a@x.io')
    record_event(share_link, EventType.ACCESS_REQUEST_DENIED, None, 'a@x.io')
    record_event(share_link, EventType.ACCESS_GRANTED, None, 'b@x.io')
    record_event(share_link, EventType.PRINT_ATTEMPT, None, 'b@x.io')
    return share_link


@pytest.mark.django_db
class TestAccessEvents:
    url = '/api/v1/analytics/access-events/'

    def test_requires_authentication(self):
        assert APIClient().get(self.url).status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_lists_everything_newest_first(self, api_client, events):
        data = api_client.get(self.url).json()
        assert data['count'] == 4
        times = [r['created_at'] for r in data['results']]
        assert times == sorted(times, reverse=True)

    def test_filter_to_access_requests(self, api_client, events):
        data = api_client.get(self.url, {'event_type': 'access_request,access_request_denied'}).json()
        assert {r['event_type'] for r in data['results']} == {'access_request', 'access_request_denied'}

    def test_filter_by_category_and_email(self, api_client, events):
        behavior = api_client.get(self.url, {'category': 'behavior'}).json()
        assert [r['event_type'] for r in behavior['results']] == ['print_attempt']
        by_email = api_client.get(self.url, {'email': 'a@x'}).json()
        assert by_email['count'] == 2

    def test_filter_by_date(self, api_client, events):
        ShareLinkEvent.objects.update(created_at=timezone.now() - timedelta(days=3))
        since = (timezone.now() - timedelta(days=1)).isoformat()
        assert api_client.get(self.url, {'since': since}).json()['count'] == 0

    def test_other_users_events_are_not_visible(self, events, user2):
        other = APIClient()
        other.force_authenticate(user2)
        assert other.get(self.url).json()['count'] == 0

    def test_summary_counts(self, api_client, events):
        data = api_client.get(self.url + 'summary/').json()
        assert data['counts']['access_request'] == 1
        assert data['counts']['access_request_denied'] == 1
