import logging
import httpx
from urllib.parse import urljoin

from django.contrib.auth import get_user_model, logout
from django.contrib.auth.signals import user_logged_in
from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.db.utils import DatabaseError
from django.utils.translation import gettext as _
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie

from core.auth_cookies import clear_auth_cookies, enforce_csrf, refresh_cookie_name, set_auth_cookies

from core.models import Organization, UserGroup
from core.permissions import IsAdmin
from core.services import blacklist_all_user_tokens
from core.serializers import (
    ChangePasswordSerializer,
    OrganizationSerializer,
    UserGroupSerializer,
    UserSerializer,
)

User = get_user_model()

logger = logging.getLogger(__name__)

class IsSelf(permissions.BasePermission):
    """
    Object-level permission to only allow users to edit their own profile.
    """
    def has_object_permission(self, request, view, obj):
        # Any user can access their own profile.
        return obj == request.user


@extend_schema(tags=['core'])
class OrganizationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint that allows organizations to be viewed.
    Scoped to the requesting user's own organization.
    """
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return Organization.objects.none()
        return Organization.objects.filter(id=user.organization_id).order_by('-created_at')


@extend_schema(tags=['core'])
class UserViewSet(mixins.RetrieveModelMixin,
                  mixins.UpdateModelMixin,
                  mixins.ListModelMixin,
                  viewsets.GenericViewSet):
    """
    API endpoint that allows a user to view and edit their own profile.
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, IsSelf]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        """
        Users can only see/edit themselves.
        """
        return User.objects.filter(pk=self.request.user.pk)


@extend_schema(tags=['core'])
class UserGroupViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows user groups to be viewed or edited.
    NOTE: Currently unmounted from core.urls. Reserved for future group-based RBAC.
    """
    queryset = UserGroup.objects.all()
    serializer_class = UserGroupSerializer
    permission_classes = [IsAuthenticated, IsAdmin]

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return UserGroup.objects.none()
        return UserGroup.objects.filter(organization=user.organization).order_by('name')

    def perform_create(self, serializer):
        serializer.save(organization=self.request.user.organization)


@extend_schema(tags=['core'])
class CustomTokenObtainPairView(TokenObtainPairView):
    """Log in. Tokens are set as httpOnly cookies and never returned in the body."""
    permission_classes = [permissions.AllowAny]

    class LoginResponseSerializer(serializers.Serializer):
        user = UserSerializer()

    @extend_schema(responses={200: LoginResponseSerializer, 401: dict})
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])

        user_logged_in.send(sender=serializer.user.__class__, request=request, user=serializer.user)

        response = Response(
            {'user': UserSerializer(serializer.user, context={'request': request}).data},
            status=status.HTTP_200_OK,
        )
        set_auth_cookies(response, serializer.validated_data['access'], serializer.validated_data.get('refresh'))
        get_token(request)  # make sure the SPA has a csrftoken cookie for later writes
        return response


@extend_schema(tags=['core'])
class CookieTokenRefreshView(APIView):
    """Rotate the token pair using the httpOnly refresh cookie."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(request=None, responses={200: dict, 401: dict})
    def post(self, request):
        enforce_csrf(request)
        raw_refresh = request.COOKIES.get(refresh_cookie_name())
        if not raw_refresh:
            return Response({'detail': _('Not authenticated.')}, status=status.HTTP_401_UNAUTHORIZED)

        serializer = TokenRefreshSerializer(data={'refresh': raw_refresh})
        try:
            serializer.is_valid(raise_exception=True)
        except (TokenError, InvalidToken):
            response = Response({'detail': _('Session expired. Please log in again.')},
                                status=status.HTTP_401_UNAUTHORIZED)
            return clear_auth_cookies(response)

        response = Response({'detail': 'refreshed'}, status=status.HTTP_200_OK)
        set_auth_cookies(response, serializer.validated_data['access'], serializer.validated_data.get('refresh'))
        return response


@extend_schema(tags=['core'])
class LogoutView(APIView):
    """Blacklist the refresh token from the cookie and clear the auth cookies."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        enforce_csrf(request)
        raw_refresh = request.COOKIES.get(refresh_cookie_name())
        if raw_refresh:
            try:
                RefreshToken(raw_refresh).blacklist()
            except TokenError:
                pass  # already expired/blacklisted: nothing to revoke
        logout(request)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        from .nocache import clear_site_cache
        return clear_site_cache(clear_auth_cookies(response))


@method_decorator(ensure_csrf_cookie, name='dispatch')
@extend_schema(tags=['core'])
class AuthMeView(APIView):
    """Return the signed-in user (used by the SPA to check its session) and set the CSRF cookie."""
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserSerializer, 401: dict})
    def get(self, request):
        return Response(UserSerializer(request.user, context={'request': request}).data)


# @extend_schema(tags=['core'])
# class RegisterView(APIView):
#     permission_classes = [permissions.AllowAny]

#     @extend_schema(
#         request=UserSerializer,
#         responses={201: UserSerializer, 400: dict},
#     )
#     def post(self, request):
#         serializer = UserSerializer(data=request.data)
#         if serializer.is_valid():
#             serializer.save()
#             return Response(serializer.data, status=status.HTTP_201_CREATED)
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=['core'])
class SetPasswordView(APIView):
    """View to set a user's password."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ChangePasswordSerializer,
        responses={204: None, 400: dict},
    )
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        if serializer.is_valid():
            user = request.user
            old_password = serializer.validated_data['old_password']

            if not user.check_password(old_password):
                return Response({"old_password": [_("Wrong password.")]}, status=status.HTTP_400_BAD_REQUEST)

            user.set_password(serializer.validated_data['new_password1'])
            user.save()
            blacklist_all_user_tokens(user)
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)




@extend_schema(tags=['core'])
class PublicSettingsView(APIView):
    permission_classes = [permissions.AllowAny]

    class PublicSettingsResponseSerializer(serializers.Serializer):
        brand_name = serializers.CharField(allow_null=True)
        brand_logo_url = serializers.CharField(allow_null=True)
        brand_website_url = serializers.CharField(allow_null=True)
        terms_url = serializers.CharField(allow_null=True, required=False)
        privacy_policy_url = serializers.CharField(allow_null=True, required=False)

    @extend_schema(responses={200: PublicSettingsResponseSerializer})
    def get(self, request):
        org = Organization.objects.first()
        brand_name = org.brand_name if org else None
        brand_logo_url = None
        brand_website_url = org.brand_website_url if org else None
        
        config = org.branding_extras or {} if org else {}
        terms_url = config.get('terms_url')
        privacy_policy_url = config.get('privacy_policy_url')

        if org and org.brand_logo and hasattr(org.brand_logo, 'url'):
            brand_logo_url = urljoin(settings.SITE_DOMAIN, org.brand_logo.url)

        return Response({
            'brand_name': brand_name,
            'brand_logo_url': brand_logo_url,
            'brand_website_url': brand_website_url,
            'terms_url': terms_url,
            'privacy_policy_url': privacy_policy_url,
        }, status=status.HTTP_200_OK)


NATIVE_LANGUAGE_NAMES = {
    'en': 'English',
}


@extend_schema(tags=['core'])
class LanguagesView(APIView):
    """
    Return a list of supported UI languages with native display names (autonyms).
    Publicly accessible endpoint.
    """
    permission_classes = [permissions.AllowAny]

    class LanguageSerializer(serializers.Serializer):
        code = serializers.CharField()
        name = serializers.CharField()

    @extend_schema(responses={200: LanguageSerializer(many=True)})
    def get(self, request):
        languages = [
            {"code": code, "name": NATIVE_LANGUAGE_NAMES.get(code, str(name))}
            for code, name in settings.LANGUAGES
        ]
        return Response(languages, status=status.HTTP_200_OK)


@extend_schema(tags=['core'])
class HealthCheckView(APIView):
    """
    Checks the health of the database, Redis cache, and file server.
    Used by Docker / Kubernetes for health checks.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'health_check'

    class HealthResponseSerializer(serializers.Serializer):
        database = serializers.CharField()
        redis = serializers.CharField()
        file_server = serializers.CharField()

    @extend_schema(
        responses={200: HealthResponseSerializer, 503: HealthResponseSerializer},
    )
    def get(self, request, *args, **kwargs):
        logger.debug('enter health check .. ')
        db_ok = False
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute("SELECT 1")
            db_ok = True
        except DatabaseError as exc:
            logger.warning('Database health check failed: %s', exc)
        logger.debug('db checked')

        redis_ok = False
        try:
            cache.set('__health_check__', '1', 1)
            redis_ok = cache.get('__health_check__') == '1'
        except Exception as exc:
            logger.warning('Redis health check failed: %s', exc)
        logger.debug('redis checked')

        fileserver_ok = False
        core_api_url = getattr(settings, 'CORE_API_URL', 'http://core:8080')
        try:
            health_url = urljoin(core_api_url.rstrip('/') + '/', 'health')
            res = httpx.get(health_url, timeout=0.5)
            fileserver_ok = res.status_code == 200
            if not fileserver_ok:
                logger.warning('File server health check returned status code: %s', res.status_code)
        except Exception as exc:
            logger.warning('File server health check failed: %s', exc)
        logger.debug('file server checked')

        health_status = {
            'database': 'ok' if db_ok else 'error',
            'redis': 'ok' if redis_ok else 'error',
            'file_server': 'ok' if fileserver_ok else 'error',
        }
        logger.debug('finish health check.')

        if db_ok and redis_ok and fileserver_ok:
            return Response(health_status, status=status.HTTP_200_OK)

        return Response(health_status, status=status.HTTP_503_SERVICE_UNAVAILABLE)
