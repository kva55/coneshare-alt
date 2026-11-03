import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


User = get_user_model()


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        username="user@example.com",
        email="user@example.com",
        password="password123",
        organization=organization
    )


@pytest.fixture
def api_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
class TestUserLanguagePreference:
    """Verify user language preference CRUD and API response localization."""

    def test_user_language_default_is_english(self, user):
        assert user.language == 'en'


    def test_invalid_language_rejected(self, api_client, user):
        url = reverse('user-detail', kwargs={'pk': user.id})
        resp = api_client.patch(url, {'language': 'invalid_code'})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'language' in resp.data

    def test_list_languages_endpoint_is_english_only(self, db):
        client = APIClient()
        resp = client.get(reverse('languages'), HTTP_ACCEPT_LANGUAGE='ru')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data == [{'code': 'en', 'name': 'English'}]

    def test_non_english_language_preference_rejected(self, api_client, user):
        resp = api_client.patch(reverse('user-detail', kwargs={'pk': user.id}), {'language': 'zh-hans'})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        user.refresh_from_db()
        assert user.language == 'en'

