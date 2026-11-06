import pytest
from datetime import timedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from checkout.events import record_event as record_checkout_event
from checkout.models import CheckoutEvent, CheckoutPolicy
from sharelinks.events import EventType, record_event
from sharelinks.models import ShareLink, ShareLinkEvent

pytestmark = pytest.mark.django_db
URL = '/api/v1/analytics/interactions/'


@pytest.fixture
def data(share_link, dataroom, organization, user, document):
    room_link = ShareLink.objects.create(dataroom=dataroom, created_by=user, name='Room link')
    policy = CheckoutPolicy.objects.create(organization=organization, dataroom=dataroom, enabled=True)
    record_event(share_link, EventType.PRINT_ATTEMPT, None, 'guest@lab.local')
    record_event(share_link, EventType.SAVE_ATTEMPT, None, 'guest@lab.local')
    record_event(share_link, EventType.TAB_HIDDEN, None, 'other@lab.local')
    record_event(share_link, EventType.ACCESS_GRANTED, None, 'guest@lab.local')
    record_event(room_link, EventType.COPY_ATTEMPT, None, 'guest@lab.local')
    record_checkout_event('print_attempt', None, 'guest@lab.local', policy, {'file': 'Report.pdf'})
    record_checkout_event('file_checked_out', None, 'guest@lab.local', policy, {'file': 'Report.pdf'})
    record_checkout_event('auth_denied', None, 'nobody@lab.local', None, {'reason': 'not_a_guest'})
    return {'link': share_link, 'room_link': room_link, 'policy': policy}


def ask(client, expression='', **extra):
    return client.get(URL, {'filter': expression, **extra})


def events(client, expression):
    response = ask(client, expression)
    assert response.status_code == 200, response.content
    return response.json()['results']


class TestScope:
    def test_requires_sign_in(self):
        assert APIClient().get(URL).status_code in (401, 403)

    def test_an_admin_sees_link_and_checkout_interactions_together(self, admin_api_client, data):
        body = ask(admin_api_client).json()
        assert {r['source'] for r in body['results']} == {'link', 'checkout'}
        assert body['total'] == 8

    def test_newest_first(self, admin_api_client, data):
        times = [r['time'] for r in events(admin_api_client, '')]
        assert times == sorted(times, reverse=True)

    def test_a_member_sees_only_their_links_and_no_checkout_events(self, api_client, data):
        body = ask(api_client).json()
        assert {r['source'] for r in body['results']} == {'link'}

    def test_other_users_see_nothing_of_it(self, data, user2):
        other = APIClient()
        other.force_authenticate(user2)
        assert ask(other).json()['total'] == 0

    def test_rows_carry_datastore_file_and_link(self, admin_api_client, data):
        rows = events(admin_api_client, 'event == print_attempt')
        by_source = {r['source']: r for r in rows}
        assert by_source['link']['file'] == 'Test Document.pdf' and by_source['link']['link'] == 'Test Share Link'
        assert by_source['checkout']['file'] == 'Report.pdf' and by_source['checkout']['datastore'] == 'Test Dataroom'


class TestFilters:
    def test_equality_and_contains(self, admin_api_client, data):
        assert {r['event'] for r in events(admin_api_client, 'event == print_attempt')} == {'print_attempt'}
        assert all('guest' in r['email'] for r in events(admin_api_client, 'email contains "guest"'))
        assert len(events(admin_api_client, 'email == "OTHER@lab.local"')) == 1   # case-insensitive

    def test_logic_and_precedence(self, admin_api_client, data):
        rows = events(admin_api_client, 'event == print_attempt && source == checkout')
        assert len(rows) == 1 and rows[0]['source'] == 'checkout'
        either = events(admin_api_client, 'event == copy_attempt || event == save_attempt')
        assert {r['event'] for r in either} == {'copy_attempt', 'save_attempt'}
        grouped = events(admin_api_client, '(event == copy_attempt || event == save_attempt) && email == guest@lab.local')
        assert len(grouped) == 2

    def test_not_and_not_equal(self, admin_api_client, data):
        assert 'print_attempt' not in {r['event'] for r in events(admin_api_client, '!(event == print_attempt)')}
        assert 'print_attempt' not in {r['event'] for r in events(admin_api_client, 'event != print_attempt')}
        assert len(events(admin_api_client, 'not source == link')) == 3

    def test_in_list(self, admin_api_client, data):
        rows = events(admin_api_client, 'event in {print_attempt save_attempt copy_attempt}')
        assert len(rows) == 4

    def test_attempt_flag_covers_both_sources(self, admin_api_client, data):
        rows = events(admin_api_client, 'attempt')
        assert {r['event'] for r in rows} == {'print_attempt', 'save_attempt', 'copy_attempt'}
        assert {r['source'] for r in rows} == {'link', 'checkout'}
        assert 'tab_hidden' not in {r['event'] for r in events(admin_api_client, 'attempt')}
        assert len(events(admin_api_client, '!attempt')) == 4

    def test_datastore_file_and_link_fields(self, admin_api_client, data):
        assert {r['source'] for r in events(admin_api_client, 'datastore == "Test Dataroom"')} == {'link', 'checkout'}
        assert all(r['file'] == 'Report.pdf' for r in events(admin_api_client, 'file == "Report.pdf"'))
        assert {r['source'] for r in events(admin_api_client, 'file contains "Test Doc"')} == {'link'}
        assert len(events(admin_api_client, 'link == "Room link"')) == 1
        # a field one source lacks never matches there
        assert {r['source'] for r in events(admin_api_client, 'link contains "link"')} == {'link'}

    def test_category(self, admin_api_client, data):
        assert {r['event'] for r in events(admin_api_client, 'category == access')} == {'access_granted'}
        behavior = events(admin_api_client, 'category == behavior')
        assert {'print_attempt', 'tab_hidden'} <= {r['event'] for r in behavior}
        assert {r['event'] for r in events(admin_api_client, 'category == checkout')} == {'file_checked_out', 'auth_denied'}

    def test_ip(self, admin_api_client, data):
        ShareLinkEvent.objects.filter(email='other@lab.local').update(ip_address='10.1.2.3')
        assert len(events(admin_api_client, 'ip == 10.1.2.3')) == 1
        assert len(events(admin_api_client, 'ip startswith "10."')) == 1
        assert ask(admin_api_client, 'ip == "not-an-ip"').status_code == 400

    def test_time_filters(self, admin_api_client, data):
        ShareLinkEvent.objects.filter(event_type='save_attempt').update(created_at=timezone.now() - timedelta(days=3))
        recent = {r['event'] for r in events(admin_api_client, 'time >= -24h')}
        assert 'save_attempt' not in recent and 'print_attempt' in recent
        assert {r['event'] for r in events(admin_api_client, 'time < -2d')} == {'save_attempt'}
        today = timezone.now().date().isoformat()
        assert len(events(admin_api_client, f'time >= "{today}"')) >= 7

    def test_source(self, admin_api_client, data):
        assert {r['source'] for r in events(admin_api_client, 'source == checkout')} == {'checkout'}

    @pytest.mark.parametrize('expression', ['nonsense == 1', 'event ==', '(event == a', 'attempt == 1', 'event > a', 'time == yesterday'])
    def test_bad_expressions_are_a_400_with_a_message(self, admin_api_client, data, expression):
        response = ask(admin_api_client, expression)
        assert response.status_code == status.HTTP_400_BAD_REQUEST and response.json()['filter']

    def test_filters_cannot_reach_other_fields_or_inject(self, admin_api_client, data):
        assert ask(admin_api_client, 'password == x').status_code == 400
        assert ask(admin_api_client, "event == \"x' OR '1'='1\"").json()['total'] == 0
        assert ask(admin_api_client, 'event contains "%"').json()['total'] == 0


class TestCountsAndPaging:
    def test_counts_cover_the_whole_filter(self, admin_api_client, data):
        body = ask(admin_api_client, 'attempt', limit=1).json()
        assert len(body['results']) == 1 and body['total'] == 4
        assert body['counts'] == {'print_attempt': 2, 'save_attempt': 1, 'copy_attempt': 1}
        assert body['has_more'] is True and body['next_before']

    def test_paging_with_before(self, admin_api_client, data):
        first = ask(admin_api_client, '', limit=3).json()
        second = ask(admin_api_client, '', limit=3, before=first['next_before']).json()
        seen = {r['id'] for r in first['results']}
        assert not seen & {r['id'] for r in second['results']}
        assert all(r['time'] < first['next_before'] for r in second['results'])
