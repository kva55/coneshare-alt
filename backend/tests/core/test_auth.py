import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import Client
from rest_framework import status
from rest_framework.test import APIClient

from core.models import LoginActivity

User = get_user_model()



# @pytest.mark.django_db
# def test_user_registration(public_client):
#     """
#     Test that a new user can be registered via the API.
#     The new user should be automatically assigned to the default organization.
#     """
#     register_data = {
#         'email': 'newuser@coneshare.com',
#         'password': 'newpassword123',
#         'first_name': 'New',
#         'last_name': 'User',
#     }
#     url = reverse('register')
#     response = public_client.post(url, register_data)
#     assert response.status_code == status.HTTP_201_CREATED
#     assert response.data['email'] == register_data['email']
#     # Verify user exists in DB and is assigned to the default organization.
#     # The `post_migrate` signal creates the "Default Organization".
#     assert User.objects.filter(email=register_data['email']).exists()
#     new_user = User.objects.get(email=register_data['email'])
#     assert new_user.organization is not None
#     assert new_user.organization.name == "Default Organization"


@pytest.mark.django_db
def test_login_failure_wrong_password(public_client, user):
    """
    Test that a login attempt with an incorrect password fails.
    """
    login_data = {
        'email': user.email,
        'password': 'wrongpassword'
    }
    url = reverse('token_obtain_pair')
    response = public_client.post(url, login_data)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_create_superuser_via_manager():
    """
    Test that the custom UserManager's `create_superuser` method successfully
    creates a superuser and assigns the default organization.
    This test directly invokes the manager method to reproduce the `createsuperuser`
    command failure if the manager is misconfigured.
    """
    email = 'superuser@coneshare.com'
    username = 'superuser'
    password = 'superpassword123'

    # This call will fail with an IntegrityError if the organization is not set.
    user = User.objects.create_superuser(
        email=email,
        username=username,
        password=password
    )

    # Verify standard superuser attributes
    assert user.email == email
    assert user.username == username
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.check_password(password) is True

    # Verify custom attributes set by the manager
    assert user.organization is not None
    assert user.organization.name == "Default Organization"
    assert user.role == 'admin'


@pytest.mark.django_db
class TestLoginActivitySignal:
    def test_login_creates_activity_record(self, public_client, user):
        """
        Tests that a successful login via the token endpoint creates a LoginActivity record.
        """
        assert LoginActivity.objects.count() == 0

        login_data = {
            'email': user.email,
            'password': 'password'
        }
        url = reverse('token_obtain_pair')

        public_client.post(
            url,
            login_data,
            HTTP_USER_AGENT='Test Browser',
            REMOTE_ADDR='192.168.1.1'
        )

        assert LoginActivity.objects.count() == 1
        activity = LoginActivity.objects.first()
        assert activity.user == user
        assert activity.ip_address == '192.168.1.1'
        assert activity.user_agent == 'Test Browser'

    def test_failed_login_does_not_create_activity_record(self, public_client, user):
        """
        Tests that a failed login attempt does not create a LoginActivity record.
        """
        assert LoginActivity.objects.count() == 0

        login_data = {
            'email': user.email,
            'password': 'wrongpassword'
        }
        url = reverse('token_obtain_pair')
        public_client.post(url, login_data)

        assert LoginActivity.objects.count() == 0


def _login(client, user, password='password'):
    return client.post(reverse('token_obtain_pair'), {'email': user.email, 'password': password}, format='json')


@pytest.mark.django_db
class TestCookieAuth:
    def test_login_sets_httponly_cookies_and_no_tokens_in_body(self, public_client, user):
        response = _login(public_client, user)
        assert response.status_code == status.HTTP_200_OK
        assert 'access' not in response.data and 'refresh' not in response.data
        assert response.data['user']['email'] == user.email

        access = response.cookies['cs_access']
        refresh = response.cookies['cs_refresh']
        for cookie in (access, refresh):
            assert cookie['httponly']
            assert cookie['samesite'] == 'Strict'
            assert cookie.value
        assert access['path'] == '/api/'
        assert refresh['path'] == '/api/v1/token/'
        assert 'csrftoken' in response.cookies

    def test_cookies_are_secure_when_debug_is_off(self, public_client, user, settings):
        settings.AUTH_COOKIE_SECURE = True
        response = _login(public_client, user)
        assert response.cookies['cs_access']['secure']
        assert response.cookies['cs_refresh']['secure']

    def test_failed_login_sets_no_cookies(self, public_client, user):
        response = _login(public_client, user, password='wrong')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert 'cs_access' not in response.cookies and 'cs_refresh' not in response.cookies

    def test_cookie_authenticates_reads_without_csrf(self, user):
        client = APIClient()
        _login(client, user)
        response = client.get(reverse('auth_me'))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['email'] == user.email

    def test_cookie_authenticated_write_requires_csrf(self, user):
        client = APIClient(enforce_csrf_checks=True)
        login = _login(client, user)
        assert login.status_code == status.HTTP_200_OK
        url = reverse('user-detail', kwargs={'pk': user.id})

        # Cookie present, CSRF header missing -> rejected.
        denied = client.patch(url, {'name': 'Changed'}, format='json')
        assert denied.status_code == status.HTTP_403_FORBIDDEN

        token = client.cookies['csrftoken'].value
        allowed = client.patch(url, {'name': 'Changed'}, format='json', HTTP_X_CSRFTOKEN=token)
        assert allowed.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.name == 'Changed'

    def test_bearer_header_still_works_without_csrf(self, user):
        from rest_framework_simplejwt.tokens import AccessToken
        client = APIClient(enforce_csrf_checks=True)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')
        url = reverse('user-detail', kwargs={'pk': user.id})
        assert client.patch(url, {'name': 'Via header'}, format='json').status_code == status.HTTP_200_OK

    def test_refresh_rotates_cookies_using_refresh_cookie(self, user):
        client = APIClient()
        login = _login(client, user)
        old_refresh = login.cookies['cs_refresh'].value
        response = client.post(reverse('token_refresh'), format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.cookies['cs_refresh'].value != old_refresh  # rotated
        assert response.cookies['cs_access'].value
        assert 'access' not in response.data

    def test_refresh_without_cookie_is_unauthorized(self, public_client):
        response = public_client.post(reverse('token_refresh'), format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_refresh_token_in_body_is_ignored(self, user, public_client):
        from rest_framework_simplejwt.tokens import RefreshToken
        response = public_client.post(reverse('token_refresh'), {'refresh': str(RefreshToken.for_user(user))}, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_blacklists_refresh_and_clears_cookies(self, user):
        client = APIClient()
        login = _login(client, user)
        stolen_refresh = login.cookies['cs_refresh'].value

        response = client.post(reverse('logout'), format='json')
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert response.cookies['cs_access'].value == ''
        assert response.cookies['cs_refresh'].value == ''

        # The old refresh token is revoked server-side even if someone kept a copy.
        replay = APIClient()
        replay.cookies['cs_refresh'] = stolen_refresh
        assert replay.post(reverse('token_refresh'), format='json').status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_requires_csrf_token(self, user):
        client = APIClient(enforce_csrf_checks=True)
        _login(client, user)
        denied = client.post(reverse('logout'), format='json')
        assert denied.status_code == status.HTTP_403_FORBIDDEN
        token = client.cookies['csrftoken'].value
        ok = client.post(reverse('logout'), format='json', HTTP_X_CSRFTOKEN=token)
        assert ok.status_code == status.HTTP_204_NO_CONTENT

    def test_unauthenticated_me_is_401(self, public_client):
        assert public_client.get(reverse('auth_me')).status_code == status.HTTP_401_UNAUTHORIZED

