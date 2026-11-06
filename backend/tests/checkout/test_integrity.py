"""Findings about how pages were requested: not from the viewer's script, automation, tiles without a cursor."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from checkout.models import Checkout, CheckoutEvent
from checkout.views import _note_tiles_without_cursor
from security.models import SecuritySettings
from security.services import clear_cache

from .test_checkout import BASE, SCRIPT, entry, policy, sign_in  # noqa: F401

pytestmark = pytest.mark.django_db


def _open(client, entry):
    cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
    client.post(f'{BASE}checkouts/{cid}/open/')
    return cid


def _types():
    return set(CheckoutEvent.objects.values_list('event_type', flat=True))


def test_a_page_request_that_is_not_the_viewer_script_is_recorded(policy, entry):
    client = sign_in()
    cid = _open(client, entry)
    r = client.post(f'{BASE}checkouts/{cid}/pages/1/')            # no X-Page-Request header
    assert r.status_code == 403
    assert 'non_browser_request' in _types()


def test_it_is_recorded_once_a_minute_not_per_request(policy, entry):
    client = sign_in()
    cid = _open(client, entry)
    for _ in range(5):
        client.post(f'{BASE}checkouts/{cid}/pages/1/')
    assert CheckoutEvent.objects.filter(event_type='non_browser_request').count() == 1


def test_a_script_user_agent_is_recorded_a_browser_is_not(policy, entry):
    client = sign_in()
    cid = _open(client, entry)
    client.post(f'{BASE}checkouts/{cid}/pages/1/', HTTP_USER_AGENT='Mozilla/5.0 (X11; Linux x86_64) Chrome/126 Safari/537.36', **SCRIPT)
    assert 'automation_detected' not in _types()
    client.post(f'{BASE}checkouts/{cid}/pages/1/', HTTP_USER_AGENT='python-requests/2.32', **SCRIPT)
    assert 'automation_detected' in _types()


def test_nothing_is_recorded_when_monitoring_is_off(policy, entry):
    row = SecuritySettings.load()
    row.monitoring_enabled = False
    row.save()
    clear_cache()
    client = sign_in()
    cid = _open(client, entry)
    client.post(f'{BASE}checkouts/{cid}/pages/1/')
    client.post(f'{BASE}checkouts/{cid}/pages/1/', HTTP_USER_AGENT='curl/8.5', **SCRIPT)
    assert not ({'non_browser_request', 'automation_detected'} & _types())


def _stub(checkout):
    return SimpleNamespace(checkout=checkout, session=SimpleNamespace(email=checkout.email))


def test_many_tiles_with_no_cursor_trace_is_a_finding(policy, entry):
    client = sign_in()
    cid = _open(client, entry)
    checkout = Checkout.objects.get(pk=cid)
    Checkout.objects.filter(pk=cid).update(opened_at=timezone.now() - timedelta(minutes=3))
    checkout.refresh_from_db()
    for _ in range(45):
        _note_tiles_without_cursor(_stub(checkout))
    assert CheckoutEvent.objects.filter(event_type='tiles_without_cursor').count() == 1


def test_a_cursor_trace_means_no_finding(policy, entry):
    client = sign_in()
    cid = _open(client, entry)
    client.post(f'{BASE}checkouts/{cid}/trace/', {'moves': [[100, 1, 10, 10]]}, format='json')
    Checkout.objects.filter(pk=cid).update(opened_at=timezone.now() - timedelta(minutes=3))
    checkout = Checkout.objects.get(pk=cid)
    for _ in range(45):
        _note_tiles_without_cursor(_stub(checkout))
    assert not CheckoutEvent.objects.filter(event_type='tiles_without_cursor').exists()
