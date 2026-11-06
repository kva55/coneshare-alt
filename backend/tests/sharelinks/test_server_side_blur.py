"""Blur and spotlight happen on the server: a restricted link never sends the clear page."""
import base64
import time
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from rest_framework import status

from documents.models import DocumentPage

PAGE = {'HTTP_X_PAGE_REQUEST': '1'}


def _checker_png(size=(500, 650)):
    image = Image.new('RGB', size, 'white')
    px = image.load()
    for x in range(size[0]):
        for y in range(size[1]):
            if (x // 3 + y // 3) % 2 == 0:
                px[x, y] = (0, 0, 0)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def _roughness(image):
    """Mean absolute difference between neighbouring pixels: high for sharp, low for blurred."""
    gray = image.convert('L')
    px, (w, h) = gray.load(), gray.size
    total = sum(abs(px[x, y] - px[x + 1, y]) for y in range(0, h, 7) for x in range(w - 1))
    return total / (len(range(0, h, 7)) * (w - 1))


@pytest.fixture
def source():
    return _checker_png()


@pytest.fixture
def link(document, share_link):
    version = document.versions.get(is_primary=True)
    version.has_pages = True
    version.num_pages = 1
    version.save()
    DocumentPage.objects.create(document_version=version, page_number=1, storage_key='pages/page_1.png')
    document.num_pages = 1
    document.save()
    share_link.document = document
    share_link.save()
    return share_link


def _storage(content):
    get = patch('sharelinks.views.requests.get',
                return_value=MagicMock(content=content, raise_for_status=lambda: None))
    url = patch('sharelinks.views.fileserver_client.generate_download_url', return_value='http://core:8080/f/t')
    return get, url


def _open(client, link):
    assert client.get(f'/api/v1/links/{link.slug}/view-data/').status_code == status.HTTP_200_OK


def _post(client, link, suffix='', **kwargs):
    return client.post(f'/api/v1/links/{link.slug}/pages/1/{suffix}', **PAGE, **kwargs)


def _image(response):
    return Image.open(BytesIO(response.content))


@pytest.mark.django_db
class TestServerSideBlur:
    def test_plain_link_is_sent_clear(self, public_client, link, source):
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            response = _post(public_client, link)
        assert response.content == source

    def test_restricted_link_never_receives_the_clear_page(self, public_client, link, source):
        link.restricted_viewing_pane = True
        link.save()
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            response = _post(public_client, link)
        assert response.status_code == 200
        assert response.content != source
        sent = _image(response)
        assert sent.size == (500, 650)
        assert _roughness(sent) < _roughness(Image.open(BytesIO(source))) / 5

    def test_watermark_stays_sharp_over_the_blur(self, public_client, link, source):
        link.restricted_viewing_pane = True
        link.enable_watermark = True
        link.watermark_text = 'CONFIDENTIAL'
        link.save()
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            plain = _post(public_client, link)
        link.enable_watermark = False
        link.save()
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            without = _post(public_client, link)
        assert plain.content != without.content


@pytest.mark.django_db
class TestTiles:
    def _enable(self, link):
        link.restricted_viewing_pane = True
        link.save()

    def _tiles(self, client, link, cells, **kw):
        return _post(client, link, 'tiles/', data={'tiles': cells}, format='json', **kw)

    def test_returns_the_requested_tiles_on_a_fixed_grid(self, public_client, link, source):
        self._enable(link)
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            response = self._tiles(public_client, link, [[0, 0], [1, 0], [1, 1]])
        assert response.status_code == 200
        body = response.json()
        size = body['tile_size']
        assert size == int(500 * 0.08)
        assert {(t['c'], t['r']) for t in body['tiles']} == {(0, 0), (1, 0), (1, 1)}
        tile = next(t for t in body['tiles'] if (t['c'], t['r']) == (1, 1))
        assert (tile['x'], tile['y'], tile['w'], tile['h']) == (size, size, size, size)
        image = Image.open(BytesIO(base64.b64decode(tile['data'])))
        assert image.size == (size, size)

    def test_tiles_are_clear_not_blurred(self, public_client, link, source):
        self._enable(link)
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            body = self._tiles(public_client, link, [[2, 2]]).json()
        image = Image.open(BytesIO(base64.b64decode(body['tiles'][0]['data'])))
        assert _roughness(image) > 20

    def test_edge_tiles_are_cropped_and_cells_outside_the_page_are_skipped(self, public_client, link, source):
        self._enable(link)
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            body = self._tiles(public_client, link, [[12, 16], [500, 500]]).json()
        assert len(body['tiles']) == 1
        tile = body['tiles'][0]
        assert tile['x'] + tile['w'] <= 500 and tile['y'] + tile['h'] <= 650

    @pytest.mark.parametrize('body', [{}, {'tiles': []}, {'tiles': 'a'}, {'tiles': [['x', 1]]},
                                       {'tiles': [[i, 0] for i in range(17)]}])
    def test_bad_requests(self, public_client, link, source, body):
        self._enable(link)
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            response = _post(public_client, link, 'tiles/', data=body, format='json')
        assert response.status_code == 400

    def test_not_available_without_the_option(self, public_client, link, source):
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            assert self._tiles(public_client, link, [[0, 0]]).status_code == 404

    def test_script_only_and_post_only(self, public_client, link):
        self._enable(link)
        _open(public_client, link)
        url = f'/api/v1/links/{link.slug}/pages/1/tiles/'
        assert public_client.post(url, {'tiles': [[0, 0]]}, format='json').status_code == 403
        assert public_client.get(url).status_code == 405

    def test_requires_an_authorised_session(self, public_client, link):
        self._enable(link)
        assert self._tiles(public_client, link, [[0, 0]]).status_code in (401, 403)

    def test_the_limit_counts_tiles_and_is_logged(self, public_client, link, source, settings):
        from sharelinks.events import EventType
        from sharelinks.models import ShareLinkEvent
        settings.SPOTLIGHT_TILES_PER_MINUTE = 5
        self._enable(link)
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            codes = [self._tiles(public_client, link, [[0, 0], [1, 0], [2, 0]]).status_code for _ in range(3)]
        assert codes == [200, 429, 429]
        assert ShareLinkEvent.objects.filter(share_link=link, event_type=EventType.SPOTLIGHT_LIMIT).count() == 1

    def test_the_old_patch_endpoint_is_gone(self, public_client, link):
        self._enable(link)
        _open(public_client, link)
        response = _post(public_client, link, 'spotlight/', data={'x': 0.5, 'y': 0.5}, format='json')
        assert response.status_code == 404


@pytest.mark.django_db
class TestDenseWatermark:
    def _page(self, client, link, source):
        _open(client, link)
        get, url = _storage(source)
        with get, url:
            return _post(client, link)

    def test_restricted_links_are_always_watermarked_even_if_the_owner_did_not_enable_it(self, public_client, link, source):
        link.restricted_viewing_pane = True
        link.enable_watermark = False
        link.save()
        with_forced = self._page(public_client, link, source)
        # same blur without any watermark would differ: compare against a plain blur
        from sharelinks.protected_pages import blur_page
        plain = blur_page(Image.open(BytesIO(source)).convert('RGBA')).convert('RGB')
        sent = Image.open(BytesIO(with_forced.content)).convert('RGB')
        diff = sum(abs(a - b) for pa, pb in zip(sent.getdata(), plain.getdata()) for a, b in zip(pa, pb))
        assert diff > 0

    def test_tiles_carry_the_watermark_too(self, public_client, link, source):
        link.restricted_viewing_pane = True
        link.save()
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            body = _post(public_client, link, 'tiles/', data={'tiles': [[0, 0]]}, format='json').json()
        clear = Image.open(BytesIO(source)).convert('RGB').crop((0, 0, body['tile_size'], body['tile_size']))
        tile = Image.open(BytesIO(base64.b64decode(body['tiles'][0]['data']))).convert('RGB')
        diff = sum(abs(a - b) for pa, pb in zip(tile.getdata(), clear.getdata()) for a, b in zip(pa, pb))
        assert diff > 0

    def test_dense_marks_are_closer_together(self):
        from sharelinks.views import _calculate_watermark_grid_params as grid
        sparse = grid(1000, 1400, 200, 80)
        dense = grid(1000, 1400, 200, 80, dense=True)
        assert len(dense['x_range']) * len(dense['y_range']) > 3 * len(sparse['x_range']) * len(sparse['y_range'])


@pytest.mark.django_db
class TestFocusLease:
    def _link(self, link):
        link.blur_when_unfocused = True
        link.save()

    def _focus(self, client, link, focused):
        return client.post(f'/api/v1/links/{link.slug}/focus/', {'focused': focused}, format='json')

    def test_pages_are_refused_without_a_lease(self, public_client, link, source):
        self._link(link)
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            assert _post(public_client, link).status_code == status.HTTP_423_LOCKED

    def test_pages_flow_while_focused_and_stop_when_not(self, public_client, link, source):
        self._link(link)
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            assert self._focus(public_client, link, True).status_code == 204
            assert _post(public_client, link).status_code == 200
            assert self._focus(public_client, link, False).status_code == 204
            assert _post(public_client, link).status_code == status.HTTP_423_LOCKED
            self._focus(public_client, link, True)
            assert _post(public_client, link).status_code == 200

    def test_a_stale_lease_expires(self, public_client, link, source, settings):
        self._link(link)
        settings.FOCUS_LEASE_SECONDS = 15
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            self._focus(public_client, link, True)
            with patch('sharelinks.protected_pages.time.time', return_value=time.time() + 60):
                assert _post(public_client, link).status_code == status.HTTP_423_LOCKED

    def test_links_without_the_option_ignore_the_lease(self, public_client, link, source):
        _open(public_client, link)
        get, url = _storage(source)
        with get, url:
            assert _post(public_client, link).status_code == 200

    def test_focus_endpoint_needs_a_session_and_a_boolean(self, public_client, link):
        self._link(link)
        assert self._focus(public_client, link, True).status_code in (401, 403)
        _open(public_client, link)
        bad = public_client.post(f'/api/v1/links/{link.slug}/focus/', {'focused': 'yes'}, format='json')
        assert bad.status_code == 400

    def test_a_lease_is_per_link(self, public_client, link, source, document, user):
        from sharelinks.models import ShareLink
        self._link(link)
        other = ShareLink.objects.create(document=document, created_by=user, blur_when_unfocused=True)
        _open(public_client, link)
        _open(public_client, other)
        self._focus(public_client, other, True)
        get, url = _storage(source)
        with get, url:
            assert _post(public_client, link).status_code == status.HTTP_423_LOCKED
