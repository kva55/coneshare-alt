from PIL import Image

from sharelinks import protected_pages as pp


def test_source_is_fetched_once_within_the_lifetime(settings):
    settings.PAGE_CACHE_SECONDS = 120
    calls = []
    fetch = lambda: calls.append(1) or b'x' * 10
    assert pp.cached_source('k', fetch) == pp.cached_source('k', fetch)
    assert len(calls) == 1


def test_source_cache_expires(settings, monkeypatch):
    settings.PAGE_CACHE_SECONDS = 120
    calls = []
    fetch = lambda: calls.append(1) or b'x'
    clock = [1000.0]
    monkeypatch.setattr(pp.time, 'monotonic', lambda: clock[0])
    pp.cached_source('k', fetch)
    clock[0] += 121
    pp.cached_source('k', fetch)
    assert len(calls) == 2


def test_source_cache_is_bounded_in_bytes(settings):
    settings.PAGE_SOURCE_CACHE_BYTES = 400
    for i in range(10):
        pp.cached_source(f'k{i}', lambda: b'x' * 100)
    assert sum(len(v[1]) for v in pp._SOURCE_CACHE.values()) <= 400


def test_a_page_too_big_for_the_budget_is_not_kept(settings):
    settings.PAGE_SOURCE_CACHE_BYTES = 400
    pp.cached_source('big', lambda: b'x' * 300)
    assert 'big' not in pp._SOURCE_CACHE


def test_zero_switches_the_caches_off(settings):
    settings.PAGE_SOURCE_CACHE_BYTES = 0
    settings.PAGE_IMAGE_CACHE_ITEMS = 0
    calls = []
    pp.cached_source('k', lambda: calls.append(1) or b'x')
    pp.cached_source('k', lambda: calls.append(1) or b'x')
    pp.cached_image('i', lambda: calls.append(1) or Image.new('RGB', (2, 2)))
    pp.cached_image('i', lambda: calls.append(1) or Image.new('RGB', (2, 2)))
    assert len(calls) == 4


def test_image_cache_is_bounded_and_keyed_per_viewer(settings):
    settings.PAGE_IMAGE_CACHE_ITEMS = 2
    built = []
    def build(tag):
        return lambda: built.append(tag) or Image.new('RGBA', (4, 4))
    pp.cached_image(('c', 1, 'alice@x'), build('a'))
    pp.cached_image(('c', 1, 'alice@x'), build('a2'))     # hit
    pp.cached_image(('c', 1, 'bob@x'), build('b'))         # another viewer: built separately
    pp.cached_image(('c', 2, 'alice@x'), build('c'))       # evicts the oldest
    assert built == ['a', 'b', 'c'] and len(pp._IMAGE_CACHE) == 2


import pytest


@pytest.mark.django_db
def test_print_screen_variants_are_broken_down_per_viewer(share_link):
    from sharelinks.activity import viewer_activity
    from sharelinks.events import EventType, record_event
    for meta in ({'variant': 'plain', 'phase': 'keydown'}, {'variant': 'plain', 'phase': 'keyup'},
                 {'variant': 'alt', 'phase': 'keydown'}, {'variant': 'plain', 'phase': 'keyup'}):
        record_event(share_link, EventType.SCREENSHOT_KEY, email='guest@x.io', metadata=meta)
    row = next(r for r in viewer_activity([share_link]) if r['email'] == 'guest@x.io')
    assert row['counts']['screenshot_key'] == 4
    assert row['screenshot_variants'] == {'plain': 1, 'plain (release only)': 2, 'alt': 1}


@pytest.mark.django_db
def test_devtools_open_is_a_tracked_client_signal():
    from sharelinks.events import CLIENT_EVENT_TYPES
    from security.capture import CAPTURE_EVENTS, DEFAULT_CAPTURE_ACTIONS
    assert 'devtools_open' in CLIENT_EVENT_TYPES and 'devtools_open' in CAPTURE_EVENTS
    assert DEFAULT_CAPTURE_ACTIONS['devtools_open'] == 'log'
