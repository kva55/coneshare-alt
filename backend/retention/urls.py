from django.urls import path

from . import views

urlpatterns = [
    path('retention/settings/', views.RetentionSettingsView.as_view(), name='retention-settings'),
    path('retention/run/', views.RetentionRunView.as_view(), name='retention-run'),
    path('retention/log/', views.RetentionLogView.as_view(), name='retention-log'),
]
