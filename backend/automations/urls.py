from django.urls import path

from . import views

urlpatterns = [
    path('webhooks/events/', views.WebhookEventsView.as_view(), name='webhook-events'),
    path('webhooks/', views.WebhookListCreateView.as_view(), name='webhooks'),
    path('webhooks/<str:endpoint_id>/', views.WebhookDetailView.as_view(), name='webhook-detail'),
    path('webhooks/<str:endpoint_id>/secret/', views.WebhookSecretView.as_view(), name='webhook-secret'),
    path('webhooks/<str:endpoint_id>/test/', views.WebhookTestView.as_view(), name='webhook-test'),
    path('webhooks/<str:endpoint_id>/deliveries/', views.WebhookDeliveriesView.as_view(), name='webhook-deliveries'),
    path('notifications/email-settings/', views.EmailSettingsView.as_view(), name='email-settings'),
    path('notifications/email-settings/test/', views.EmailTestView.as_view(), name='email-settings-test'),
    path('notifications/email-settings/test-receive/', views.EmailReceiveTestView.as_view(), name='email-settings-test-receive'),
]
