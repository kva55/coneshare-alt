"""Session report: bounded trace collection, the setting that switches it off, and the analysis."""
import pytest
from rest_framework.test import APIClient

from checkout.models import Checkout, CheckoutTraceChunk
from checkout.trace_report import build_report
from security.models import SecuritySettings
from security.services import clear_cache

from .test_checkout import BASE, entry, policy, sign_in  # noqa: F401  (fixtures)

pytestmark = pytest.mark.django_db


def _open(client, entry):
    cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
    client.post(f'{BASE}checkouts/{cid}/open/')
    return cid


def _moves(n=30):
    return [[i * 100, 1, 100 + i * 20, 100 + i * 20] for i in range(n)]


class TestCollection:
    def test_samples_are_stored_for_the_open_checkout(self, policy, entry):
        client = sign_in()
        cid = _open(client, entry)
        r = client.post(f'{BASE}checkouts/{cid}/trace/', {'moves': _moves(), 'views': [[0, 1, 500]]}, format='json')
        assert r.status_code == 204
        chunk = CheckoutTraceChunk.objects.get()
        assert len(chunk.moves) == 30 and chunk.views == [[0, 1, 500]]

    def test_bad_rows_are_dropped_and_values_clamped(self, policy, entry):
        client = sign_in()
        cid = _open(client, entry)
        client.post(f'{BASE}checkouts/{cid}/trace/', {'moves': [[1, 1, 5000, -4], ['x', 1, 1, 1], [1, 1, 1], [1, 1, True, 2]], 'views': 'no'}, format='json')
        assert CheckoutTraceChunk.objects.get().moves == [[1, 1, 1000, 0]]

    def test_nothing_is_stored_when_tracing_is_off(self, policy, entry):
        row = SecuritySettings.load()
        row.checkout_tracing_enabled = False
        row.save()
        clear_cache()
        client = sign_in()
        cid = _open(client, entry)
        assert client.post(f'{BASE}checkouts/{cid}/trace/', {'moves': _moves()}, format='json').status_code == 204
        assert CheckoutTraceChunk.objects.count() == 0
        assert client.get(f'{BASE}checkouts/{cid}/').json()['trace'] is False

    def test_another_guest_cannot_write_to_it(self, policy, entry):
        from checkout.models import CheckoutGuest
        CheckoutGuest.objects.create(policy=policy, email='second@lab.local')
        cid = _open(sign_in(), entry)
        r = sign_in(email='second@lab.local').post(f'{BASE}checkouts/{cid}/trace/', {'moves': _moves()}, format='json')
        assert r.status_code == 404

    def test_anonymous_is_refused(self, policy, entry):
        assert APIClient().post(f'{BASE}checkouts/x/trace/', {}, format='json').status_code == 401


class TestAdminReport:
    def test_report_has_timeline_and_analysis_but_no_page_content(self, policy, entry, admin_api_client):
        client = sign_in()
        cid = _open(client, entry)
        client.post(f'{BASE}checkouts/{cid}/trace/', {'moves': _moves(), 'views': [[0, 1, 500], [1000, 1, 500]]}, format='json')
        r = admin_api_client.get(f'{BASE}admin/checkouts/{cid}/report/')
        assert r.status_code == 200
        body = r.json()
        assert body['checkout']['email'] == 'guest@lab.local'
        assert any(e['type'] == 'file_checked_out' for e in body['timeline'])
        page = body['analysis']['pages'][0]
        assert page['width'] == 300 and page['height'] == 400
        assert 'storage_key' not in str(body)

    def test_list_marks_checkouts_with_a_report(self, policy, entry, admin_api_client):
        client = sign_in()
        cid = _open(client, entry)
        client.post(f'{BASE}checkouts/{cid}/trace/', {'moves': _moves()}, format='json')
        rows = admin_api_client.get(f'{BASE}admin/checkouts/').json()['results']
        assert rows[0]['id'] == cid and rows[0]['has_report'] is True

    def test_other_organizations_and_non_admins_are_refused(self, policy, entry, api_client):
        cid = _open(sign_in(), entry)
        assert api_client.get(f'{BASE}admin/checkouts/{cid}/report/').status_code == 403


class TestAnalysis:
    def test_empty_trace(self):
        r = build_report([], [])
        assert r['pages'] == [] and r['basis']['confidence'] == 'none'

    def test_diagonal_horizontal_and_vertical_are_told_apart(self):
        diag = [[i * 100, 1, 100 + i * 40, 100 + i * 40] for i in range(10)]
        horiz = [[2000 + i * 100, 1, 100 + i * 40, 600] for i in range(10)]
        vert = [[4000 + i * 100, 1, 800, 100 + i * 40] for i in range(10)]
        r = build_report(diag + horiz + vert, [])
        m = r['movement']
        assert m['diagonal'] >= 8 and m['horizontal'] >= 8 and m['vertical'] >= 8
        assert r['pages'][0]['diagonal']

    def test_jumps_across_a_pause_are_not_movements(self):
        r = build_report([[0, 1, 0, 0], [5000, 1, 900, 900]], [])
        assert r['movement']['diagonal'] == 0

    def test_dwell_lands_in_the_cell_the_cursor_sat_in(self):
        r = build_report([[i * 100, 1, 505, 505] for i in range(10)], [])
        heat = r['pages'][0]['heat']
        assert heat[14][10] > 0.5 and sum(map(sum, heat)) == pytest.approx(r['pages'][0]['seconds_cursor_over'], abs=0.1)

    def test_time_in_view_is_capped_so_an_idle_tab_is_not_counted(self):
        r = build_report([], [[0, 1, 500], [600000, 2, 500]])
        assert r['pages'][0]['seconds_in_view'] <= 3.0


class TestEncryptedAtRest:
    def test_samples_are_not_readable_in_the_database(self, policy, entry):
        from django.db import connection
        client = sign_in()
        cid = _open(client, entry)
        client.post(f'{BASE}checkouts/{cid}/trace/', {'moves': [[111, 1, 123, 456]], 'views': [[222, 1, 789]]}, format='json')
        with connection.cursor() as cur:
            cur.execute('SELECT moves_data, views_data FROM checkout_checkouttracechunk')
            raw = b''.join(bytes(v) if not isinstance(v, str) else v.encode() for v in cur.fetchone())
        assert b'123, 456' not in raw and b'111' not in raw[:8] and b'[[' not in raw
        assert CheckoutTraceChunk.objects.get().moves == [[111, 1, 123, 456]]


class TestSpotlightLimitLogging:
    def test_going_over_the_limit_is_logged_once_a_minute(self, policy, entry, settings):
        from checkout.models import CheckoutEvent
        from sharelinks.models import AccessPolicy
        settings.SPOTLIGHT_TILES_PER_MINUTE = 1
        AccessPolicy.objects.create(document=entry.dataroom_document.document, restricted_viewing_pane=True)
        client = sign_in()
        cid = _open(client, entry)
        for _ in range(6):
            client.post(f'{BASE}checkouts/{cid}/pages/1/tiles/', {'tiles': [[0, 0], [1, 0]]}, format='json',
                        HTTP_X_PAGE_REQUEST='1')
        assert CheckoutEvent.objects.filter(event_type='spotlight_limit').count() == 1


class TestStraightAndCaptureWindows:
    def test_straight_runs_are_found_and_zigzags_are_not(self):
        from checkout.trace_report import straight_runs
        diagonal = [(i * 100, 100 + i * 60, 100 + i * 60) for i in range(10)]
        zigzag = [(i * 100, 100 + i * 60, 100 + (i % 2) * 80) for i in range(10)]
        horizontal = [(i * 100, 100 + i * 60, 500) for i in range(8)]
        assert [r[4] for r in straight_runs(diagonal)] == ['diagonal']
        assert straight_runs(zigzag) == []
        assert [r[4] for r in straight_runs(horizontal)] == ['horizontal']

    def test_a_pause_splits_a_run(self):
        from checkout.trace_report import straight_runs
        pts = [(i * 100, 100 + i * 60, 500) for i in range(3)] + [(5000 + i * 100, 300 + i * 60, 500) for i in range(3)]
        assert straight_runs(pts) == []          # neither half is long enough on its own

    def test_a_capture_key_shows_drags_and_a_lost_focus_hint(self):
        from checkout.trace_report import capture_windows
        base = 100000
        moves = [[base - 3000 + i * 100, 1, 100 + i * 50, 100 + i * 50] for i in range(10)]
        clicks = [[base - 2000, 1, 100, 100, 0], [base - 1500, 1, 400, 400, 1]]
        views = [[base - 4000, 1, 500], [base - 3000, 1, 500], [base - 1000, 1, 500]]
        out = capture_windows([{'type': 'screenshot_key', 'ms': base, 'seconds': 9}, {'type': 'copy_attempt', 'ms': base, 'seconds': 9}],
                              moves, views, clicks)
        assert len(out) == 1                     # only print, save and screenshot keys
        w = out[0]
        assert w['page'] == 1 and w['focus_left_likely'] is True
        assert w['before']['clicks'] == 1 and w['before']['drags'][0]['kind'] == 'diagonal'
        assert w['before']['straight']['diagonal'] == 1 and w['after']['moves'] == 0

    def test_focus_is_not_lost_when_screen_samples_keep_arriving(self):
        from checkout.trace_report import capture_windows
        base = 100000
        views = [[base - 1000, 1, 500], [base + 800, 1, 500], [base + 1800, 1, 500]]
        assert capture_windows([{'type': 'print_attempt', 'ms': base, 'seconds': 1}], [], views, [])[0]['focus_left_likely'] is False

    def test_clicks_are_stored_and_reach_the_report(self, policy, entry, admin_api_client):
        client = sign_in()
        cid = _open(client, entry)
        client.post(f'{BASE}checkouts/{cid}/trace/',
                    {'moves': [[500, 1, 10, 10]], 'clicks': [[400, 1, 100, 100, 0], [900, 1, 300, 300, 1], [1, 1, 1, 1, 7]], 'now': 1000},
                    format='json')
        chunk = CheckoutTraceChunk.objects.get()
        assert chunk.clicks == [[400, 1, 100, 100, 0], [900, 1, 300, 300, 1]] and chunk.client_now == 1000
        assert admin_api_client.get(f'{BASE}admin/checkouts/{cid}/report/').status_code == 200
