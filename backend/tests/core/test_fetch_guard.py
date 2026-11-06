import pytest
from django.test import RequestFactory

from core.fetch_guard import is_script_fetch, no_store_headers, sniff_image_content_type
from django.http import HttpResponse


def _request(**headers):
    return RequestFactory().post('/x/', **headers)


def test_requires_the_custom_header():
    assert not is_script_fetch(_request())
    assert not is_script_fetch(_request(HTTP_X_PAGE_REQUEST='0'))
    assert is_script_fetch(_request(HTTP_X_PAGE_REQUEST='1'))


@pytest.mark.parametrize('dest', ['document', 'iframe', 'embed', 'object', 'image', 'script', 'frame'])
def test_non_fetch_destinations_are_refused(dest):
    assert not is_script_fetch(_request(HTTP_X_PAGE_REQUEST='1', HTTP_SEC_FETCH_DEST=dest))


@pytest.mark.parametrize('mode', ['navigate', 'no-cors', 'websocket'])
def test_navigations_and_no_cors_are_refused(mode):
    assert not is_script_fetch(_request(HTTP_X_PAGE_REQUEST='1', HTTP_SEC_FETCH_MODE=mode))


@pytest.mark.parametrize('site', ['cross-site', 'same-site', 'none'])
def test_non_same_origin_sites_are_refused(site):
    assert not is_script_fetch(_request(HTTP_X_PAGE_REQUEST='1', HTTP_SEC_FETCH_SITE=site))


def test_a_normal_same_origin_fetch_is_accepted():
    assert is_script_fetch(_request(
        HTTP_X_PAGE_REQUEST='1', HTTP_SEC_FETCH_DEST='empty',
        HTTP_SEC_FETCH_MODE='cors', HTTP_SEC_FETCH_SITE='same-origin',
    ))


def test_no_store_headers():
    response = no_store_headers(HttpResponse(b'x'))
    assert 'no-store' in response['Cache-Control']
    assert response['Pragma'] == 'no-cache'
    assert response['X-Content-Type-Options'] == 'nosniff'
    assert response['Cross-Origin-Resource-Policy'] == 'same-origin'
    assert 'sandbox' in response['Content-Security-Policy']


@pytest.mark.parametrize('data,expected', [
    (b'\xff\xd8\xff\xe0abc', 'image/jpeg'),
    (b'\x89PNG\r\n\x1a\nabc', 'image/png'),
    (b'RIFF\x00\x00\x00\x00WEBPVP8 ', 'image/webp'),
    (b'<html>not an image</html>', 'application/octet-stream'),
    (b'', 'application/octet-stream'),
])
def test_sniff_image_content_type(data, expected):
    assert sniff_image_content_type(data) == expected
