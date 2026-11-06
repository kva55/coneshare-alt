from django.urls import path

from . import views

urlpatterns = [
    path('security/settings/', views.SecuritySettingsView.as_view(), name='security-settings'),
    path('security/events/', views.SecurityEventsView.as_view(), name='security-events'),
    path('security/login-notice/', views.LoginNoticeView.as_view(), name='security-login-notice'),
    path('security/inspect/', views.SecurityInspectView.as_view(), name='security-inspect'),
    path('security/test-alert/', views.SecurityTestAlertView.as_view(), name='security-test-alert'),
]
