import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.i18n_utils import resolve_email_language

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

    def test_update_user_language(self, api_client, user):
        url = reverse('user-detail', kwargs={'pk': user.id})
        resp = api_client.patch(url, {'language': 'zh-hans'})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['language'] == 'zh-hans'
        user.refresh_from_db()
        assert user.language == 'zh-hans'

    def test_invalid_language_rejected(self, api_client, user):
        url = reverse('user-detail', kwargs={'pk': user.id})
        resp = api_client.patch(url, {'language': 'invalid_code'})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'language' in resp.data

    def test_list_languages_endpoint(self, db):
        client = APIClient()
        url = reverse('languages')
        resp = client.get(url, HTTP_ACCEPT_LANGUAGE='ru')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data == [
            {'code': 'en', 'name': 'English'},
            {'code': 'zh-hans', 'name': '简体中文'},
            {'code': 'ru', 'name': 'Русский'},
            {'code': 'de', 'name': 'Deutsch'},
            {'code': 'fr', 'name': 'Français'},
            {'code': 'nl', 'name': 'Nederlands'},
        ]

    def test_api_error_respects_accept_language_header(self, api_client):
        """Verify API response error messages respect Accept-Language header."""
        url = reverse('set_password')
        payload = {
            'old_password': 'wrong_password',
            'new_password1': 'new_pass123',
            'new_password2': 'new_pass123',
        }
        resp = api_client.post(url, payload, HTTP_ACCEPT_LANGUAGE='zh-hans')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data['old_password'] == ['密码错误。']

    def test_api_error_in_russian(self, api_client):
        """Verify API response error messages in Russian when Accept-Language: ru."""
        url = reverse('set_password')
        payload = {
            'old_password': 'wrong_password',
            'new_password1': 'new_pass123',
            'new_password2': 'new_pass123',
        }
        resp = api_client.post(url, payload, HTTP_ACCEPT_LANGUAGE='ru')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data['old_password'] == ['Неверный пароль.']

    def test_api_error_in_german(self, api_client):
        """Verify API response error messages in German when Accept-Language: de."""
        url = reverse('set_password')
        payload = {
            'old_password': 'wrong_password',
            'new_password1': 'new_pass123',
            'new_password2': 'new_pass123',
        }
        resp = api_client.post(url, payload, HTTP_ACCEPT_LANGUAGE='de')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data['old_password'] == ['Falsches Passwort.']

    def test_api_error_in_french(self, api_client):
        """Verify API response error messages in French when Accept-Language: fr."""
        url = reverse('set_password')
        payload = {
            'old_password': 'wrong_password',
            'new_password1': 'new_pass123',
            'new_password2': 'new_pass123',
        }
        resp = api_client.post(url, payload, HTTP_ACCEPT_LANGUAGE='fr')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data['old_password'] == ['Mot de passe incorrect.']

    def test_api_error_in_dutch(self, api_client):
        """Verify API response error messages in Dutch when Accept-Language: nl."""
        url = reverse('set_password')
        payload = {
            'old_password': 'wrong_password',
            'new_password1': 'new_pass123',
            'new_password2': 'new_pass123',
        }
        resp = api_client.post(url, payload, HTTP_ACCEPT_LANGUAGE='nl')
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data['old_password'] == ['Onjuist wachtwoord.']


    @pytest.mark.parametrize("lang, expected_subject, expected_snippet", [
        ("en", "Verify your email to view 'Test Document.pdf'", "Please click the link below to view 'Test Document.pdf'."),
        ("zh-hans", "请验证您的邮箱以查看“Test Document.pdf”", "请点击下方链接查看“Test Document.pdf”："),
        ("ru", "Подтвердите email для просмотра «Test Document.pdf»", "Пожалуйста, перейдите по ссылке ниже для просмотра «Test Document.pdf»."),
        ("de", "Bestätigen Sie Ihre E-Mail-Adresse, um „Test Document.pdf“ anzuzeigen", "Bitte klicken Sie auf den unten stehenden Link, um „Test Document.pdf“ anzuzeigen."),
        ("fr", "Vérifiez votre e-mail pour afficher « Test Document.pdf »", "Veuillez cliquer sur le lien ci-dessous pour afficher « Test Document.pdf »."),
        ("nl", "Verifieer uw e-mailadres om ‘Test Document.pdf’ te bekijken", "Klik op de onderstaande link om ‘Test Document.pdf’ te bekijken."),
    ])
    def test_sharelink_email_verification_language(self, mailoutbox, share_link_requires_email_verification, lang, expected_subject, expected_snippet):
        """Verify sharelink magic link email verification respects Accept-Language header."""
        mailoutbox.clear()
        client = APIClient()
        url = reverse('share-link-request-access', kwargs={'slug': share_link_requires_email_verification.slug})
        resp = client.post(url, {'email': 'viewer@example.com'}, HTTP_ACCEPT_LANGUAGE=lang)
        assert resp.status_code == status.HTTP_200_OK
        assert len(mailoutbox) == 1
        msg = mailoutbox[0]
        assert msg.to == ['viewer@example.com']
        assert msg.subject == expected_subject
        assert expected_snippet in msg.body



    def test_sharelink_viewer_email_verification_falls_back_to_en_regardless_of_owner(self, mailoutbox, share_link_requires_email_verification):
        """Verify external viewer verification email falls back to 'en' when Accept-Language is omitted/unsupported."""
        # Set owner language to Russian
        owner = share_link_requires_email_verification.created_by
        owner.language = 'ru'
        owner.save()

        mailoutbox.clear()
        client = APIClient()
        url = reverse('share-link-request-access', kwargs={'slug': share_link_requires_email_verification.slug})
        resp = client.post(url, {'email': 'viewer@example.com'}, HTTP_ACCEPT_LANGUAGE='ja-JP,ja;q=0.9')
        assert resp.status_code == status.HTTP_200_OK
        assert len(mailoutbox) == 1
        msg = mailoutbox[0]
        # Should fall back to English per i18n plan, NOT the owner's Russian language
        assert msg.subject == "Verify your email to view 'Test Document.pdf'"
        assert "Please click the link below to view 'Test Document.pdf'." in msg.body

    @pytest.mark.parametrize("accept, user_lang, req_lang, expected", [
        ("zh-CN,zh;q=0.9", "ru", "en", "zh-hans"),
        ("ru-RU,ru;q=0.9", None, "en", "ru"),
        ("de-DE,de;q=0.9", None, "en", "de"),
        ("fr-FR,fr;q=0.9", None, "en", "fr"),
        ("nl-NL,nl;q=0.9", None, "en", "nl"),
        ("nl-BE,nl;q=0.9", None, "en", "nl"),
        ("ja-JP,ja;q=0.9", "zh-hans", "en", "zh-hans"),
        ("ja-JP,ja;q=0.9", None, "zh-hans", "zh-hans"),
        ("ja-JP,ja;q=0.9", None, None, "en"),
        ("", "ru", "en", "ru"),
        ("", "de", "en", "de"),
        ("", "fr", "en", "fr"),
        ("", "nl", "en", "nl"),
        ("", None, "zh-hans", "zh-hans"),
        ("", None, None, "en"),
    ])
    def test_resolve_email_language_priorities(self, accept, user_lang, req_lang, expected):
        """Verify resolve_email_language prioritizes supported headers, then user lang, then req lang, then 'en'."""
        result = resolve_email_language(accept_header=accept, user_lang=user_lang, request_lang=req_lang, default_lang='en')
        assert result == expected




