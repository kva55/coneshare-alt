from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from documents.models import Document, DocumentPage, DocumentVersion

JPEG = b'\xff\xd8\xff\xe0' + b'owner-page' * 5
PAGE = {'HTTP_X_PAGE_REQUEST': '1'}


@pytest.fixture
def paged_document(user, organization):
    doc = Document.objects.create(organization=organization, created_by=user, name='owned.pdf',
                                  num_pages=1, status='ready', type='pdf')
    version = DocumentVersion.objects.create(document=doc, version_number=1, is_primary=True,
                                             has_pages=True, num_pages=1)
    DocumentPage.objects.create(document_version=version, page_number=1, storage_key='pages/owned_1.jpg')
    return doc


def _url(doc, n=1):
    return f'/api/v1/documents/{doc.id}/pages/{n}/'


@pytest.mark.django_db
class TestDocumentPageImageView:
    def test_owner_script_fetch_gets_the_bytes_with_no_store(self, api_client, paged_document):
        with patch('documents.views.fileserver_client.generate_download_url', return_value='http://core:8080/f/t') as m, \
                patch('documents.views.requests.get', return_value=MagicMock(content=JPEG, raise_for_status=lambda: None)):
            response = api_client.post(_url(paged_document), **PAGE)
        assert response.status_code == status.HTTP_200_OK
        assert response.content == JPEG
        assert response['Content-Type'] == 'image/jpeg'
        assert 'no-store' in response['Cache-Control']
        m.assert_called_once_with('pages/owned_1.jpg', is_internal=True)

    def test_plain_get_and_headerless_post_are_refused(self, api_client, paged_document):
        assert api_client.get(_url(paged_document)).status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        assert api_client.post(_url(paged_document)).status_code == status.HTTP_403_FORBIDDEN
        assert api_client.post(_url(paged_document), **PAGE, HTTP_SEC_FETCH_DEST='image').status_code == status.HTTP_403_FORBIDDEN

    def test_other_users_cannot_load_pages(self, paged_document, user2):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=user2)
        assert client.post(_url(paged_document), **PAGE).status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated_requests_are_rejected(self, public_client, paged_document):
        assert public_client.post(_url(paged_document), **PAGE).status_code in (
            status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_missing_page_is_404(self, api_client, paged_document):
        assert api_client.post(_url(paged_document, 7), **PAGE).status_code == status.HTTP_404_NOT_FOUND
