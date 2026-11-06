"""A link with no access rules is open: it must not ask for an email."""
import pytest
from rest_framework import status

pytestmark = pytest.mark.django_db


def _view(client, link):
    return client.get(f'/api/v1/links/{link.slug}/view-data/')


class TestOpenLinks:
    def test_a_link_without_rules_asks_for_nothing(self, public_client, share_link):
        assert _view(public_client, share_link).status_code == status.HTTP_200_OK

    def test_its_public_meta_says_it_is_not_restricted(self, public_client, share_link):
        meta = public_client.get(f'/api/v1/links/{share_link.slug}/').json()
        assert meta['requires_allowlist'] is False and meta['is_restricted'] is False

    def test_downloads_work_without_an_email(self, public_client, share_link):
        share_link.allow_download = True
        share_link.save()
        data = _view(public_client, share_link).json()
        assert data['link_settings']['allow_download'] is True

    def test_the_email_flow_is_not_offered_on_an_open_link(self, public_client, share_link):
        response = public_client.post(f'/api/v1/links/{share_link.slug}/access/request/', {'email': 'a@lab.local'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_adding_an_address_restricts_it(self, api_client, public_client, share_link):
        api_client.post(f'/api/v1/share-links/{share_link.id}/allowed-emails/', {'emails': ['a@lab.local']}, format='json')
        response = _view(public_client, share_link)
        assert response.status_code == 401 and response.json()['protectionType'] == 'allowlist'

    def test_adding_a_domain_restricts_it_too(self, api_client, public_client, share_link):
        api_client.post(f'/api/v1/share-links/{share_link.id}/allowed-emails/', {'emails': ['lab.local']}, format='json')
        assert _view(public_client, share_link).status_code == 401

    def test_revoking_the_last_entry_does_not_make_the_link_public(self, api_client, public_client, share_link):
        from sharelinks.models import ShareLinkAllowedEmail
        api_client.post(f'/api/v1/share-links/{share_link.id}/allowed-emails/', {'emails': ['a@lab.local']}, format='json')
        entry = ShareLinkAllowedEmail.objects.get()
        api_client.delete(f'/api/v1/share-links/{share_link.id}/allowed-emails/{entry.id}/')
        assert _view(public_client, share_link).status_code == 401

    def test_strict_mode_restricts_every_link(self, public_client, share_link, settings):
        settings.SHARELINK_REQUIRE_ALLOWLIST = True
        assert _view(public_client, share_link).status_code == 401
