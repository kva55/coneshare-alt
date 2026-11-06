"""A user whose password somebody else knows can do nothing but change it, until they do."""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def flagged(organization):
    return User.objects.create_user(username='new@example.com', email='new@example.com', password='Initial-Pass-1!x',
                                    organization=organization, must_change_password=True)


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def test_the_flag_is_off_for_ordinary_users(user):
    assert user.must_change_password is False


def test_the_session_endpoint_tells_the_app(flagged):
    r = _client(flagged).get('/api/v1/auth/me/')
    assert r.status_code == 200 and r.json()['must_change_password'] is True


def test_the_flag_cannot_be_cleared_through_the_profile(flagged):
    r = _client(flagged).patch('/api/v1/auth/me/', {'must_change_password': False}, format='json')
    flagged.refresh_from_db()
    assert flagged.must_change_password is True


def test_changing_the_password_clears_the_flag_and_it_may_not_be_reused(flagged):
    c = _client(flagged)
    r = c.post('/api/v1/users/set-password/', {'old_password': 'Initial-Pass-1!x', 'new_password1': 'Initial-Pass-1!x',
                                                'new_password2': 'Initial-Pass-1!x'}, format='json')
    assert r.status_code == 400
    r = c.post('/api/v1/users/set-password/', {'old_password': 'Initial-Pass-1!x', 'new_password1': 'Better-Pass-2!y',
                                                'new_password2': 'Better-Pass-2!y'}, format='json')
    assert r.status_code == 204
    flagged.refresh_from_db()
    assert flagged.must_change_password is False and flagged.check_password('Better-Pass-2!y')


def test_the_gate_blocks_other_calls_when_the_cookie_is_used(flagged, settings):
    """The gate lives in the authentication class, so it applies to every view."""
    from rest_framework.exceptions import PermissionDenied
    from core.auth_cookies import require_password_change_gate
    from django.test import RequestFactory
    request = RequestFactory().get('/api/v1/documents/')
    with pytest.raises(PermissionDenied) as caught:
        require_password_change_gate(flagged, request)
    assert caught.value.detail['code'] == 'password_change_required'
    for allowed in ('/api/v1/auth/me/', '/api/v1/token/logout/', '/api/v1/users/set-password/'):
        require_password_change_gate(flagged, RequestFactory().get(allowed))


def test_an_unflagged_user_is_never_gated(user):
    from core.auth_cookies import require_password_change_gate
    from django.test import RequestFactory
    require_password_change_gate(user, RequestFactory().get('/api/v1/documents/'))
