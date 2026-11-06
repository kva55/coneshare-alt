from django.urls import path

from .interactions import InteractionsView
from .views import (AccessEventsView, AccessEventSummaryView, AllLinksView, AllViewSessionsView, DailyVisitsView,
                    DashboardSummaryView, ViewSessionDetailView)

urlpatterns = [
    path('analytics/dashboard/', DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('analytics/daily-visits/', DailyVisitsView.as_view(), name='daily-visits'),
    path('analytics/links/', AllLinksView.as_view(), name='all-links'),
    path('analytics/interactions/', InteractionsView.as_view(), name='interactions'),
    path('analytics/access-events/', AccessEventsView.as_view(), name='access-events'),
    path('analytics/access-events/summary/', AccessEventSummaryView.as_view(), name='access-events-summary'),
    path('analytics/view-sessions/', AllViewSessionsView.as_view(), name='all-view-sessions'),
    path('analytics/view-sessions/<str:pk>/', ViewSessionDetailView.as_view(), name='view-session-detail'),
]
