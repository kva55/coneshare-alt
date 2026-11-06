from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import access_views, policy_views, views

router = DefaultRouter()
router.register(r'share-link-templates', views.ShareLinkTemplateViewSet)
router.register(r'share-links', views.ShareLinkViewSet)
router.register(r'qna-threads', views.QnAThreadViewSet, basename='qna-thread')
router.register(r'viewers', views.ViewerViewSet)
router.register(r'view-sessions', views.ViewSessionViewSet, basename='viewsession')

urlpatterns = [
    path('links/<slug:slug>/public-meta/', views.ShareLinkPublicMetaView.as_view(), name='share-link-public-meta'),
    path('links/<slug:slug>/verify-password/', views.ShareLinkVerifyPasswordView.as_view(), name='share-link-verify-password'),
    path('links/<slug:slug>/accept-nda/', views.ShareLinkAcceptNDAView.as_view(), name='share-link-accept-nda'),
    path('links/<slug:slug>/request-access/', views.ShareLinkRequestAccessView.as_view(), name='share-link-request-access'),
    path('links/<slug:slug>/verify-access-token/confirm/', views.ShareLinkConfirmAccessView.as_view(), name='share-link-confirm-access'),
    path('links/<slug:slug>/view-data/', views.ShareLinkViewDataView.as_view(), name='share-link-view-data'),
    path('links/<slug:slug>/qna-summary/', views.ShareLinkQnASummaryView.as_view(), name='share-link-qna-summary'),
    path('links/<slug:slug>/qna-threads/', views.ShareLinkQnAThreadListCreateView.as_view(), name='share-link-qna-threads'),
    path('links/<slug:slug>/qna-threads/<str:thread_id>/messages/', views.ShareLinkQnAMessageListCreateView.as_view(), name='share-link-qna-messages'),
    # Allow-list authentication (viewer side)
    path('links/<slug:slug>/access/request/', access_views.ShareLinkAccessRequestView.as_view(), name='sharelink-access-request'),
    path('links/<slug:slug>/access/verify/', access_views.ShareLinkAccessVerifyView.as_view(), name='sharelink-access-verify'),
    path('links/<slug:slug>/events/', access_views.ShareLinkViewerEventView.as_view(), name='sharelink-viewer-event'),
    # Allow-list management and access log (owner side)
    path('share-links/<str:link_id>/allowed-emails/', access_views.ShareLinkAllowedEmailsView.as_view(), name='sharelink-allowed-emails'),
    path('share-links/<str:link_id>/allowed-domains/<str:entry_id>/', access_views.ShareLinkAllowedDomainDetailView.as_view(), name='sharelink-allowed-domain-detail'),
    path('share-links/<str:link_id>/allowed-emails/<str:entry_id>/', access_views.ShareLinkAllowedEmailDetailView.as_view(), name='sharelink-allowed-email-detail'),
    path('share-links/<str:link_id>/allowed-emails/<str:entry_id>/resend/', access_views.ShareLinkAllowedEmailResendView.as_view(), name='sharelink-allowed-email-resend'),
    path('access-policies/<str:kind>/<str:ident>/', policy_views.AccessPolicyView.as_view(), name='access-policy'),
    path('share-links/<str:link_id>/events/', access_views.ShareLinkEventsView.as_view(), name='sharelink-events'),
    path('share-links/<str:link_id>/viewer-activity/', access_views.ShareLinkViewerActivityView.as_view(), name='sharelink-viewer-activity'),
    path('documents/<str:document_id>/viewer-activity/', access_views.DocumentViewerActivityView.as_view(), name='document-viewer-activity'),
    # POST only, script-initiated (see core/fetch_guard.py). The old GET page/ and render-page/ URLs are gone.
    path('links/<slug:slug>/pages/<int:page_number>/', views.SharePageImageView.as_view(), name='sharelink-page-image'),
    path('links/<slug:slug>/pages/<int:page_number>/tiles/', views.SharePageTilesView.as_view(), name='sharelink-page-tiles'),
    path('links/<slug:slug>/focus/', views.ShareLinkFocusView.as_view(), name='sharelink-focus'),
    path('links/<slug:slug>/download-file/', views.ShareLinkFileDownloadView.as_view(), name='sharelink-file-download'),
    path('links/<slug:slug>/download-folder/<str:folder_id>/', views.DataroomFolderDownloadView.as_view(), name='dataroom-folder-download'),
    path('links/<slug:slug>/download-dataroom/', views.DataroomDownloadAllView.as_view(), name='dataroom-download-all'),
    path('page-views/record/', views.RecordPageView.as_view(), name='record-page-view'),
    path('link-clicks/record/', views.RecordLinkClickView.as_view(), name='record-link-click'),

    path('', include(router.urls)),
]
