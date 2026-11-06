"""A document's viewer activity includes its check-outs, labelled as such, even with no share link."""
import pytest

from checkout.models import CheckoutEvent
from checkout.events import record_event

from .test_checkout import BASE, entry, policy, sign_in  # noqa: F401

pytestmark = pytest.mark.django_db


def _open(client, entry):
    cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
    client.post(f'{BASE}checkouts/{cid}/open/')
    return cid


def _activity(api_client, document):
    return api_client.get(f'/api/v1/documents/{document.id}/viewer-activity/')


def test_a_document_with_no_share_link_still_shows_check_out_activity(policy, entry, document, api_client):
    client = sign_in()
    cid = _open(client, entry)
    for etype, meta in (('screenshot_key', {'variant': 'plain', 'phase': 'keyup'}), ('devtools_key', {}), ('print_attempt', {})):
        r = client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': etype, 'metadata': meta}, format='json')
        assert r.status_code == 204
    resp = _activity(api_client, document)
    assert resp.status_code == 200, resp.content
    rows = [r for r in resp.json()['results'] if r['source'] == 'checkout']
    assert len(rows) == 1
    row = rows[0]
    assert row['email'] == 'guest@lab.local' and row['sessions'] == 1
    assert row['counts'] == {'screenshot_key': 1, 'devtools_key': 1, 'print_attempt': 1}
    assert row['attempts'] == 3 and row['screenshot_variants'] == {'plain (release only)': 1}


def test_events_recorded_before_the_document_id_was_kept_are_matched_by_file_name(policy, entry, document, api_client):
    client = sign_in()
    _open(client, entry)
    record_event('print_attempt', None, 'guest@lab.local', policy, {'file': entry.dataroom_document.name})   # old shape
    rows = [r for r in _activity(api_client, document).json()['results'] if r['source'] == 'checkout']
    assert rows and rows[0]['counts'].get('print_attempt') == 1


def test_another_documents_events_are_not_mixed_in(policy, entry, document, api_client):
    client = sign_in()
    _open(client, entry)
    record_event('print_attempt', None, 'guest@lab.local', policy, {'file': 'Other.pdf', 'document': 'someone-elses-id'})
    rows = [r for r in _activity(api_client, document).json()['results'] if r['source'] == 'checkout']
    assert not rows or 'print_attempt' not in rows[0]['counts']


def test_only_people_who_manage_the_file_see_check_out_activity(policy, entry, document, user2):
    from rest_framework.test import APIClient
    sign_in()
    other = APIClient()
    other.force_authenticate(user=user2)
    resp = other.get(f'/api/v1/documents/{document.id}/viewer-activity/')
    assert resp.status_code in (403, 404) or not [r for r in resp.json().get('results', []) if r['source'] == 'checkout']


def test_share_link_rows_are_labelled_too(policy, entry, document, api_client, share_link):
    from sharelinks.events import EventType, record_event as record_link_event
    record_link_event(share_link, EventType.PRINT_ATTEMPT, email='viewer@x.io')
    rows = _activity(api_client, document).json()['results']
    assert {r['source'] for r in rows} == {'link'}
