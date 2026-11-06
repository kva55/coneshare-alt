import pytest
from rest_framework import status
from rest_framework.test import APIClient

from sharelinks.events import EventType, record_event
from sharelinks.models import ShareLink, ViewSession


@pytest.fixture
def activity(share_link):
    ViewSession.objects.create(share_link=share_link, viewer_email='a@x.io', duration_seconds=120)
    ViewSession.objects.create(share_link=share_link, viewer_email='a@x.io', duration_seconds=30)
    ViewSession.objects.create(share_link=share_link, viewer_email='b@x.io', duration_seconds=10)
    for event in (EventType.PRINT_ATTEMPT, EventType.PRINT_ATTEMPT, EventType.SCREENSHOT_KEY,
                  EventType.SAVE_ATTEMPT, EventType.DEVTOOLS_KEY, EventType.TAB_HIDDEN):
        record_event(share_link, event, None, 'a@x.io')
    return share_link


@pytest.mark.django_db
class TestViewerActivity:
    def test_time_and_attempts_per_viewer(self, api_client, activity):
        data = api_client.get(f'/api/v1/share-links/{activity.id}/viewer-activity/').json()
        by_email = {row['email']: row for row in data['results']}
        a = by_email['a@x.io']
        assert a['sessions'] == 2 and a['seconds'] == 150
        assert a['counts'] == {'print_attempt': 2, 'screenshot_key': 1, 'save_attempt': 1,
                               'devtools_key': 1, 'tab_hidden': 1}
        assert a['attempts'] == 5          # tab changes are not counted as attempts
        assert by_email['b@x.io']['attempts'] == 0 and by_email['b@x.io']['seconds'] == 10
        assert 'save_attempt' in data['event_types']

    def test_document_level_aggregates_its_links(self, api_client, activity, document, user):
        other = ShareLink.objects.create(document=document, created_by=user)
        ViewSession.objects.create(share_link=other, viewer_email='a@x.io', duration_seconds=50)
        data = api_client.get(f'/api/v1/documents/{document.id}/viewer-activity/').json()
        a = next(row for row in data['results'] if row['email'] == 'a@x.io')
        assert a['seconds'] == 200 and a['sessions'] == 3

    def test_only_those_who_manage_the_link_can_read_it(self, activity, user2, public_client):
        other = APIClient()
        other.force_authenticate(user2)
        assert other.get(f'/api/v1/share-links/{activity.id}/viewer-activity/').status_code == status.HTTP_404_NOT_FOUND
        assert public_client.get(f'/api/v1/share-links/{activity.id}/viewer-activity/').status_code in (401, 403)

    def test_viewers_can_report_the_new_signals(self, public_client, api_client, share_link, settings):
        settings.SHARELINK_REQUIRE_ALLOWLIST = False
        public_client.get(f'/api/v1/links/{share_link.slug}/view-data/')
        for event in ('save_attempt', 'devtools_key', 'window_blur'):
            response = public_client.post(f'/api/v1/links/{share_link.slug}/events/', {'event_type': event}, format='json')
            assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
class TestEventReliability:
    def test_many_quick_events_are_all_kept(self, public_client, share_link, settings):
        settings.SHARELINK_REQUIRE_ALLOWLIST = False
        public_client.get(f'/api/v1/links/{share_link.slug}/view-data/')
        for _ in range(20):
            public_client.post(f'/api/v1/links/{share_link.slug}/events/', {'event_type': 'context_menu'}, format='json')
        from sharelinks.models import ShareLinkEvent
        assert ShareLinkEvent.objects.filter(share_link=share_link, event_type='context_menu').count() == 20

    def test_one_noisy_viewer_does_not_use_up_everyones_allowance(self, share_link, settings, monkeypatch):
        settings.SHARELINK_REQUIRE_ALLOWLIST = False
        monkeypatch.setattr('sharelinks.access_views.CLIENT_EVENTS_PER_MINUTE', 3)
        noisy, calm = APIClient(), APIClient()
        for client in (noisy, calm):
            client.get(f'/api/v1/links/{share_link.slug}/view-data/')
        for _ in range(10):
            noisy.post(f'/api/v1/links/{share_link.slug}/events/', {'event_type': 'context_menu'}, format='json')
        assert calm.post(f'/api/v1/links/{share_link.slug}/events/', {'event_type': 'print_attempt'}, format='json').status_code == 204
        from sharelinks.models import ShareLinkEvent
        assert ShareLinkEvent.objects.filter(event_type='print_attempt').count() == 1
        assert ShareLinkEvent.objects.filter(event_type='context_menu').count() == 3

    def test_drag_signal_is_accepted(self, public_client, share_link, settings):
        settings.SHARELINK_REQUIRE_ALLOWLIST = False
        public_client.get(f'/api/v1/links/{share_link.slug}/view-data/')
        assert public_client.post(f'/api/v1/links/{share_link.slug}/events/', {'event_type': 'drag_attempt'}, format='json').status_code == 204
