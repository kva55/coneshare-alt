"""A link can use a domain-wide rule instead of (or beside) individual addresses."""
import re

import pytest
from django.core import mail
from rest_framework import status

from sharelinks.access import normalize_domain, split_entries
from sharelinks.events import EventType
from sharelinks.models import ShareLinkAllowedDomain, ShareLinkEvent

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def allowlist_on(settings):
    settings.SHARELINK_REQUIRE_ALLOWLIST = True


def _add(api_client, link, *entries):
    response = api_client.post(f'/api/v1/share-links/{link.id}/allowed-emails/', {'emails': list(entries)}, format='json')
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()


def _request(client, link, email):
    mail.outbox.clear()
    return client.post(f'/api/v1/links/{link.slug}/access/request/', {'email': email}, format='json')


def _code():
    return re.search(r'\b([A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4})\b', mail.outbox[-1].body).group(1)


class TestParsing:
    def test_domains_and_emails_are_told_apart(self):
        emails, domains, invalid = split_entries(['a@b.com, @Partner.com', 'lab.local', 'nope', '*.com', 'x@'])
        assert emails == ['a@b.com']
        assert domains == ['partner.com', 'lab.local']
        assert set(invalid) == {'nope', '*.com', 'x@'}

    @pytest.mark.parametrize('value,expected', [('@Example.COM', 'example.com'), ('lab.local', 'lab.local'),
                                                ('a@b.com', None), ('*.example.com', None), ('example', None)])
    def test_normalize_domain(self, value, expected):
        assert normalize_domain(value) == expected


class TestDomainRule:
    def test_anyone_at_the_domain_can_get_in(self, api_client, public_client, share_link):
        body = _add(api_client, share_link, '@lab.local')
        assert body['domains_added'][0]['domain'] == 'lab.local'
        assert _request(public_client, share_link, 'guest@lab.local').status_code == 200
        assert len(mail.outbox) == 1
        verify = public_client.post(f'/api/v1/links/{share_link.slug}/access/verify/',
                                    {'email': 'guest@lab.local', 'code': _code()}, format='json')
        assert verify.status_code == 200
        assert public_client.get(f'/api/v1/links/{share_link.slug}/view-data/').status_code == 200

    def test_no_invitation_is_sent_when_a_domain_is_added(self, api_client, share_link):
        mail.outbox.clear()
        _add(api_client, share_link, 'lab.local')
        assert mail.outbox == []

    def test_other_domains_get_the_same_answer_and_no_mail(self, api_client, public_client, share_link):
        _add(api_client, share_link, 'lab.local')
        listed = _request(public_client, share_link, 'guest@lab.local')
        other = _request(public_client, share_link, 'mallory@evil.test')
        assert listed.json() == other.json()
        assert mail.outbox == []
        assert ShareLinkEvent.objects.filter(share_link=share_link, event_type=EventType.ACCESS_REQUEST_DENIED).exists()

    def test_subdomains_are_not_covered(self, api_client, public_client, share_link):
        _add(api_client, share_link, 'lab.local')
        _request(public_client, share_link, 'x@mail.lab.local')
        assert mail.outbox == []

    def test_works_beside_individual_addresses(self, api_client, public_client, share_link):
        _add(api_client, share_link, 'special@elsewhere.org', 'lab.local')
        _request(public_client, share_link, 'special@elsewhere.org')
        assert len(mail.outbox) == 1
        _request(public_client, share_link, 'anyone@lab.local')
        assert len(mail.outbox) == 1

    def test_revoking_the_rule_cuts_off_sessions_and_codes(self, api_client, public_client, share_link):
        _add(api_client, share_link, 'lab.local')
        _request(public_client, share_link, 'guest@lab.local')
        code = _code()
        public_client.post(f'/api/v1/links/{share_link.slug}/access/verify/', {'email': 'guest@lab.local', 'code': code}, format='json')
        assert public_client.get(f'/api/v1/links/{share_link.slug}/view-data/').status_code == 200
        entry = ShareLinkAllowedDomain.objects.get(share_link=share_link)
        assert api_client.delete(f'/api/v1/share-links/{share_link.id}/allowed-domains/{entry.id}/').status_code == 204
        assert public_client.get(f'/api/v1/links/{share_link.slug}/view-data/').status_code == 401

    def test_listing_shows_the_domain_rules(self, api_client, share_link):
        _add(api_client, share_link, 'lab.local')
        data = api_client.get(f'/api/v1/share-links/{share_link.id}/allowed-emails/').json()
        assert [d['domain'] for d in data['domains']] == ['lab.local']

    def test_invalid_domains_are_reported_not_added(self, api_client, share_link):
        body = _add(api_client, share_link, 'lab.local', '*.evil.test')
        assert body['invalid'] == ['*.evil.test']
        assert ShareLinkAllowedDomain.objects.count() == 1

    def test_only_owners_can_add_rules(self, public_client, share_link):
        response = public_client.post(f'/api/v1/share-links/{share_link.id}/allowed-emails/', {'emails': ['lab.local']}, format='json')
        assert response.status_code in (401, 403)
