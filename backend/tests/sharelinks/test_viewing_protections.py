"""Experimental viewing protections: owner-set flags reach the viewer (client-side deterrents)."""
import pytest
from rest_framework import status


@pytest.mark.django_db
class TestViewingProtectionFlags:
    def test_default_off(self, share_link):
        assert share_link.blur_when_unfocused is False
        assert share_link.restricted_viewing_pane is False

    def test_owner_can_set_both(self, api_client, share_link):
        response = api_client.patch(
            f'/api/v1/share-links/{share_link.id}/',
            {'blur_when_unfocused': True, 'restricted_viewing_pane': True}, format='json')
        assert response.status_code == status.HTTP_200_OK, response.content
        share_link.refresh_from_db()
        assert share_link.blur_when_unfocused and share_link.restricted_viewing_pane
        assert response.json()['blur_when_unfocused'] is True
        assert response.json()['restricted_viewing_pane'] is True

    def test_viewer_receives_the_flags(self, public_client, share_link):
        share_link.blur_when_unfocused = True
        share_link.save()
        data = public_client.get(f'/api/v1/links/{share_link.slug}/view-data/').json()
        settings = data.get('link_settings') or data
        assert settings['blur_when_unfocused'] is True
        assert settings['restricted_viewing_pane'] is False

    def test_flags_are_independent_of_each_other(self, public_client, share_link):
        share_link.restricted_viewing_pane = True
        share_link.save()
        data = public_client.get(f'/api/v1/links/{share_link.slug}/view-data/').json()
        settings = data.get('link_settings') or data
        assert settings['blur_when_unfocused'] is False
        assert settings['restricted_viewing_pane'] is True
