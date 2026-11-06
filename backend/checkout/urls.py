from django.urls import path

from . import views

urlpatterns = [
    # guest
    path('auth/request/', views.AuthRequestView.as_view(), name='checkout-auth-request'),
    path('auth/link/', views.AuthLinkView.as_view(), name='checkout-auth-link'),
    path('auth/consume/', views.AuthConsumeView.as_view(), name='checkout-auth-consume'),
    path('session/', views.SessionView.as_view(), name='checkout-session'),
    path('datarooms/', views.DataroomsView.as_view(), name='checkout-datarooms'),
    path('datarooms/<str:policy_id>/nda/', views.AcceptNdaView.as_view(), name='checkout-nda'),
    path('files/<str:file_id>/request/', views.RequestFileView.as_view(), name='checkout-request'),
    path('checkouts/<str:checkout_id>/', views.CheckoutDetailView.as_view(), name='checkout-detail'),
    path('checkouts/<str:checkout_id>/events/', views.CheckoutViewerEventView.as_view(), name='checkout-viewer-event'),
    path('checkouts/<str:checkout_id>/trace/', views.CheckoutTraceView.as_view(), name='checkout-trace'),
    path('checkouts/<str:checkout_id>/open/', views.OpenCheckoutView.as_view(), name='checkout-open'),
    path('checkouts/<str:checkout_id>/return/', views.ReturnCheckoutView.as_view(), name='checkout-return'),
    path('checkouts/<str:checkout_id>/pages/<int:page_number>/', views.CheckoutPageView.as_view(), name='checkout-page'),
    path('checkouts/<str:checkout_id>/pages/<int:page_number>/tiles/', views.CheckoutTilesView.as_view(), name='checkout-page-tiles'),
    path('checkouts/<str:checkout_id>/focus/', views.CheckoutFocusView.as_view(), name='checkout-focus'),
    path('checkouts/<str:checkout_id>/download/', views.CheckoutDownloadView.as_view(), name='checkout-download'),
    # per-file access settings, for whoever manages the datastore
    path('dataroom-documents/<str:dataroom_document_id>/access/', views.DocumentAccessView.as_view(), name='checkout-document-access'),
    # admin dashboard
    path('admin/policies/', views.AdminPolicyListView.as_view(), name='checkout-admin-policies'),
    path('admin/policies/<str:policy_id>/', views.AdminPolicyDetailView.as_view(), name='checkout-admin-policy'),
    path('admin/policies/<str:policy_id>/files/', views.AdminPolicyFilesView.as_view(), name='checkout-admin-policy-files'),
    path('admin/policies/<str:policy_id>/guests/', views.AdminGuestsView.as_view(), name='checkout-admin-guests'),
    path('admin/policies/<str:policy_id>/guests/<str:guest_id>/', views.AdminGuestDetailView.as_view(), name='checkout-admin-guest'),
    path('admin/approvals/', views.AdminApprovalsView.as_view(), name='checkout-admin-approvals'),
    path('admin/approvals/<str:checkout_id>/<str:decision>/', views.AdminApprovalDecisionView.as_view(), name='checkout-admin-decision'),
    path('admin/checkouts/', views.AdminCheckoutListView.as_view(), name='checkout-admin-checkouts'),
    path('admin/checkouts/<str:checkout_id>/report/', views.AdminCheckoutReportView.as_view(), name='checkout-admin-report'),
    path('admin/events/', views.AdminEventsView.as_view(), name='checkout-admin-events'),
]
