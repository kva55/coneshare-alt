"""JWT authentication carried in httpOnly cookies.

The browser never sees the tokens: they are set by the login / refresh endpoints as
``httpOnly`` cookies (``Secure`` outside DEBUG, ``SameSite=Strict``) and removed on
logout. Because a cookie is sent automatically, every state-changing request that
authenticates through the cookie must pass Django's CSRF check (the SPA sends the
``csrftoken`` cookie value back in the ``X-CSRFToken`` header).

``Authorization: Bearer <token>`` headers are still accepted for non-browser clients;
those are not subject to CSRF because browsers never attach them automatically.
"""
from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import CSRFCheck
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.settings import api_settings as jwt_settings


def _cookie_conf():
    return {
        'access_name': getattr(settings, 'AUTH_ACCESS_COOKIE', 'cs_access'),
        'refresh_name': getattr(settings, 'AUTH_REFRESH_COOKIE', 'cs_refresh'),
        'secure': getattr(settings, 'AUTH_COOKIE_SECURE', not settings.DEBUG),
        'samesite': getattr(settings, 'AUTH_COOKIE_SAMESITE', 'Strict'),
        'domain': getattr(settings, 'AUTH_COOKIE_DOMAIN', None),
        # The access token is needed by every API call; the refresh token only by the
        # token endpoints, so it is scoped to that path.
        'access_path': '/api/',
        'refresh_path': '/api/v1/token/',
    }


def access_cookie_name() -> str:
    return _cookie_conf()['access_name']


def refresh_cookie_name() -> str:
    return _cookie_conf()['refresh_name']


def set_auth_cookies(response, access: str, refresh: str | None = None):
    conf = _cookie_conf()
    common = dict(httponly=True, secure=conf['secure'], samesite=conf['samesite'], domain=conf['domain'])
    response.set_cookie(
        conf['access_name'], access,
        max_age=int(jwt_settings.ACCESS_TOKEN_LIFETIME.total_seconds()),
        path=conf['access_path'], **common,
    )
    if refresh:
        response.set_cookie(
            conf['refresh_name'], refresh,
            max_age=int(jwt_settings.REFRESH_TOKEN_LIFETIME.total_seconds()),
            path=conf['refresh_path'], **common,
        )
    return response


def clear_auth_cookies(response):
    conf = _cookie_conf()
    response.delete_cookie(conf['access_name'], path=conf['access_path'], domain=conf['domain'],
                           samesite=conf['samesite'])
    response.delete_cookie(conf['refresh_name'], path=conf['refresh_path'], domain=conf['domain'],
                           samesite=conf['samesite'])
    return response


def enforce_csrf(request):
    """Run Django's CSRF validation for a DRF request (same check SessionAuthentication uses)."""
    def dummy_get_response(_request):  # pragma: no cover - never called
        return None

    django_request = getattr(request, '_request', request)
    check = CSRFCheck(dummy_get_response)
    check.process_request(django_request)
    reason = check.process_view(django_request, None, (), {})
    if reason:
        raise exceptions.PermissionDenied(f'CSRF Failed: {reason}')


# What a user who must change their password may still call.
PASSWORD_CHANGE_ALLOWED_PATHS = ('/api/v1/auth/me/', '/api/v1/token/logout/', '/api/v1/users/set-password/')


def require_password_change_gate(user, request):
    """Refuse everything else until the password has been changed (code ``password_change_required``)."""
    if getattr(user, 'must_change_password', False) and request.path not in PASSWORD_CHANGE_ALLOWED_PATHS:
        raise exceptions.PermissionDenied(
            {'detail': 'You must change your password before continuing.', 'code': 'password_change_required'})


class CookieJWTAuthentication(JWTAuthentication):
    """Authenticate with the Authorization header if present, otherwise with the access cookie."""

    def authenticate(self, request):
        header = self.get_header(request)
        if header is not None:
            result = super().authenticate(request)
            if result is not None:
                require_password_change_gate(result[0], request)
            return result

        raw_token = request.COOKIES.get(access_cookie_name())
        if not raw_token:
            return None

        validated_token = self.get_validated_token(raw_token)
        user = self.get_user(validated_token)
        enforce_csrf(request)
        require_password_change_gate(user, request)
        return user, validated_token
