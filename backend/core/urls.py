from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core import views

router = DefaultRouter()
router.register(r'organizations', views.OrganizationViewSet)
router.register(r'users', views.UserViewSet)
# Note: UserGroupViewSet ('groups') is intentionally omitted from routing as it is
# reserved for future group-based RBAC and has no active frontend or business consumers.

urlpatterns = [
    path('_health/', views.HealthCheckView.as_view(), name='health-check'),
    path('', include(router.urls)),
]
