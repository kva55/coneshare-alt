"""Share-link page images are delivered only to the viewer's own script (POST, no URL)."""
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from documents.models import DocumentPage
from sharelinks.models import ShareLink

JPEG = b'\xff\xd8\xff\xe0' + b'jpeg-bytes' * 10
PAGE = {'HTTP_X_PAGE_REQUEST': '1'}


@pytest.fixture
def linked_document(document, share_link):
    version = document.versions.get(is_primary=True)
    version.has_pages = True
    version.num_pages = 1
    version.save()
    DocumentPage.objects.create(document_version=version, page_number=1, storage_key='pages/page_1.jpg')
    document.num_pages = 1
    document.save()
    share_link.document = document
    share_link.save()
    return share_link


def _page_url(link, n=1):
    return f'/api/v1/links/{link.slug}/pages/{n}/'


def _authorize(client, link):
    assert client.get(f'/api/v1/links/{link.slug}/view-data/').status_code == status.HTTP_200_OK


def _storage_ok(content=JPEG):
    get = patch('sharelinks.views.requests.get',
                return_value=MagicMock(content=content, raise_for_status=lambda: None))
    url = patch('sharelinks.views.fileserver_client.generate_download_url', return_value='http://core:8080/f/t')
    return get, url


@pytest.mark.django_db
class TestSharePageDelivery:
    def test_old_get_urls_are_gone(self, public_client, linked_document):
        slug = linked_document.slug
        assert public_client.get(f'/api/v1/links/{slug}/page/1/').status_code == status.HTTP_404_NOT_FOUND
        assert public_client.get(f'/api/v1/links/{slug}/render-page/1/').status_code == status.HTTP_404_NOT_FOUND

    def test_plain_get_of_the_page_url_is_not_allowed(self, public_client, linked_document):
        _authorize(public_client, linked_document)
        assert public_client.get(_page_url(linked_document)).status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_post_without_the_viewer_header_is_refused(self, public_client, linked_document):
        _authorize(public_client, linked_document)
        response = public_client.post(_page_url(linked_document))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.parametrize('extra', [
        {'HTTP_SEC_FETCH_DEST': 'image'},
        {'HTTP_SEC_FETCH_DEST': 'iframe'},
        {'HTTP_SEC_FETCH_MODE': 'navigate'},
        {'HTTP_SEC_FETCH_SITE': 'cross-site'},
    ])
    def test_image_iframe_navigation_and_cross_site_loads_are_refused(self, public_client, linked_document, extra):
        _authorize(public_client, linked_document)
        response = public_client.post(_page_url(linked_document), **PAGE, **extra)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_script_fetch_streams_the_page_bytes_without_caching_or_redirect(self, public_client, linked_document):
        _authorize(public_client, linked_document)
        get, url = _storage_ok()
        with get, url as mock_url:
            response = public_client.post(
                _page_url(linked_document), **PAGE,
                HTTP_SEC_FETCH_DEST='empty', HTTP_SEC_FETCH_MODE='cors', HTTP_SEC_FETCH_SITE='same-origin',
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.content == JPEG
        assert response['Content-Type'] == 'image/jpeg'
        assert 'no-store' in response['Cache-Control']
        assert 'ETag' not in response
        assert not response.has_header('Location')
        # fetched through the internal core address, never exposed
        mock_url.assert_called_once_with('pages/page_1.jpg', is_internal=True)

    def test_unauthorized_session_is_rejected_even_with_the_header(self, public_client, document, user):
        link = ShareLink.objects.create(document=document, created_by=user, password='letmein-123')
        response = public_client.post(_page_url(link), **PAGE)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_missing_page_is_404(self, public_client, linked_document):
        _authorize(public_client, linked_document)
        response = public_client.post(_page_url(linked_document, 99), **PAGE)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_storage_failure_is_a_generic_503(self, public_client, linked_document):
        import requests
        _authorize(public_client, linked_document)
        with patch('sharelinks.views.fileserver_client.generate_download_url', return_value='http://core:8080/f/t'), \
                patch('sharelinks.views.requests.get', side_effect=requests.ConnectionError('boom http://core:8080/secret')):
            response = public_client.post(_page_url(linked_document), **PAGE)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert 'core:8080' not in response.content.decode()

    def test_view_data_lists_pages_without_urls_or_text(self, public_client, linked_document):
        response = public_client.get(f'/api/v1/links/{linked_document.slug}/view-data/')
        assert response.status_code == status.HTTP_200_OK
        pages = response.json()['pages']
        assert [p['page_number'] for p in pages] == [1]
        assert 'url' not in pages[0]
        assert pages[0]['text_content'] == {'lines': []}

    def test_watermarked_pages_are_rendered_server_side(self, public_client, linked_document):
        pytest.importorskip('PIL.Image')
        from io import BytesIO
        from PIL import Image
        linked_document.enable_watermark = True
        linked_document.watermark_text = 'CONFIDENTIAL'
        linked_document.save()
        _authorize(public_client, linked_document)
        buffer = BytesIO()
        Image.new('RGB', (200, 200), 'white').save(buffer, 'JPEG')
        get, url = _storage_ok(buffer.getvalue())
        with get, url:
            response = public_client.post(_page_url(linked_document), **PAGE, REMOTE_ADDR='192.168.1.1')
        assert response.status_code == status.HTTP_200_OK
        assert response['Content-Type'] == 'image/jpeg'
        assert response.content != buffer.getvalue()  # watermark burned into the pixels
        assert 'no-store' in response['Cache-Control']
