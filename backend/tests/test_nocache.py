"""Nothing dynamic is cacheable; sign-out asks the browser to drop its cache; no address leaks as a Referer."""
import re

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from core.nocache import NO_STORE, NoStoreMiddleware


def _through(path, response):
    return NoStoreMiddleware(lambda request: response)(RequestFactory().get(path))


def test_every_dynamic_response_is_no_store():
    r = _through('/api/v1/anything/', HttpResponse('{}'))
    assert r['Cache-Control'] == NO_STORE and r['Pragma'] == 'no-cache' and r['Expires'] == '0'


def test_errors_are_not_cached_either():
    assert _through('/api/v1/nope/', HttpResponse(status=404))['Cache-Control'] == NO_STORE
    assert _through('/api/v1/boom/', HttpResponse(status=500))['Cache-Control'] == NO_STORE


def test_a_view_that_chose_its_own_policy_keeps_it():
    own = HttpResponse('x')
    own['Cache-Control'] = 'no-store, private, max-age=0, no-transform'
    assert _through('/api/v1/page/', own)['Cache-Control'].endswith('no-transform')


def test_static_files_and_media_are_left_to_their_server():
    assert 'Cache-Control' not in _through('/static/app.js', HttpResponse('x'))
    assert 'Cache-Control' not in _through('/media/logo.png', HttpResponse('x'))


@pytest.mark.django_db
def test_a_real_api_answer_and_the_referrer_policy(public_client):
    r = public_client.get('/api/v1/languages/')
    assert 'no-store' in r['Cache-Control'] and 'private' in r['Cache-Control']
    assert r['Referrer-Policy'] == 'no-referrer'


@pytest.mark.django_db
def test_signing_out_asks_the_browser_to_drop_its_cache(api_client):
    r = api_client.post('/api/v1/token/logout/')
    assert r.status_code == 204 and r['Clear-Site-Data'] == '"cache"'


def test_secrets_in_a_query_string_are_not_stored_by_probe_detection():
    # the same expression security.services.record_probe applies
    from security import services
    src = open(services.__file__).read()
    pattern = re.search(r"re\.sub\(r'(\(\?i\)[^']+)', r'\\1\[redacted\]', query\)", src).group(1)
    q = 'token=abc&view_session_id=01HXYZ&code=123456&key=k&page=2&otp=999&session=s'
    safe = re.sub(pattern, r'\1[redacted]', q)
    assert safe == 'token=[redacted]&view_session_id=[redacted]&code=[redacted]&key=[redacted]&page=2&otp=[redacted]&session=[redacted]'
