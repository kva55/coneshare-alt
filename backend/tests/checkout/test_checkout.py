"""/checkout: per-guest access, e-mailed sign-in link, optional NDA and approval, timed viewing."""
import re
from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from checkout.events import EventType
from checkout.models import (Checkout, CheckoutEvent, CheckoutFile, CheckoutGuest, CheckoutLoginLink,
                             CheckoutNdaAcceptance, CheckoutPolicy)
from datarooms.models import DataroomDocument
from documents.models import DocumentPage

GUEST = 'guest@lab.local'
OTHER = 'stranger@lab.local'
BASE = '/api/v1/checkout/'
SCRIPT = {'HTTP_X_PAGE_REQUEST': '1'}

pytestmark = pytest.mark.django_db


@pytest.fixture
def policy(dataroom, organization, document, user):
    p = CheckoutPolicy.objects.create(organization=organization, dataroom=dataroom, enabled=True,
                                      checkout_minutes=5, max_attempts=3)
    CheckoutGuest.objects.create(policy=p, email=GUEST, added_by=user)
    version = document.versions.get(is_primary=True)
    version.original_storage_key = 'orig/key.pdf'
    version.content_type = 'application/pdf'
    version.file_size = 11
    version.has_pages = True
    version.num_pages = 1
    version.save()
    document.type = 'pdf'
    document.save()
    DocumentPage.objects.create(document_version=version, page_number=1, storage_key='pages/p1.png',
                                metadata={'width': 300, 'height': 400})
    dd = DataroomDocument.objects.create(dataroom=dataroom, document=document, name='Report.pdf')
    CheckoutFile.objects.create(policy=p, dataroom_document=dd)
    return p


@pytest.fixture
def entry(policy):
    return policy.files.get()


def _link_from_mail():
    return re.search(r'/checkout#token=([\w-]+)', mail.outbox[-1].body).group(1)


def sign_in(client=None, email=GUEST):
    client = client or APIClient()
    mail.outbox.clear()
    client.post(BASE + 'auth/request/', {'email': email}, format='json')
    assert len(mail.outbox) == 1, 'no link was sent'
    response = client.post(BASE + 'auth/consume/', {'token': _link_from_mail()}, format='json')
    assert response.status_code == 200, response.content
    return client


def png():
    buffer = BytesIO()
    Image.new('RGB', (300, 400), 'white').save(buffer, format='PNG')
    return buffer.getvalue()


def storage():
    return (patch('checkout.views.requests.get', return_value=MagicMock(content=png(), raise_for_status=lambda: None)),
            patch('checkout.views.fileserver_client.generate_download_url', return_value='http://core/f'))


class TestSignInLink:
    def test_a_guest_gets_a_link_by_email(self, policy, public_client):
        mail.outbox.clear()
        response = public_client.post(BASE + 'auth/request/', {'email': GUEST.upper()}, format='json')
        assert response.status_code == 200
        assert [m.to for m in mail.outbox] == [[GUEST]]
        assert '/checkout#token=' in mail.outbox[0].body and '/checkout?token=' not in mail.outbox[0].body
        assert '15 minutes' in mail.outbox[0].body

    def test_non_guests_get_the_same_answer_and_nothing_is_sent(self, policy, public_client):
        mail.outbox.clear()
        a = public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        mail.outbox.clear()
        b = public_client.post(BASE + 'auth/request/', {'email': OTHER}, format='json')
        assert a.json() == b.json() and mail.outbox == []
        assert CheckoutEvent.objects.filter(event_type=EventType.AUTH_DENIED, email=OTHER).exists()

    def test_a_guest_of_a_disabled_datastore_gets_no_link_and_the_log_says_why(self, policy, public_client):
        policy.enabled = False
        policy.save()
        mail.outbox.clear()
        response = public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        assert response.status_code == 200 and mail.outbox == []
        event = CheckoutEvent.objects.get(event_type=EventType.AUTH_DENIED)
        assert event.metadata == {'reason': 'datastore_disabled'}

    def test_a_stranger_is_logged_as_not_a_guest(self, policy, public_client):
        public_client.post(BASE + 'auth/request/', {'email': OTHER}, format='json')
        assert CheckoutEvent.objects.get(event_type=EventType.AUTH_DENIED).metadata == {'reason': 'not_a_guest'}

    def test_the_link_is_logged_as_sent(self, policy, public_client):
        public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        assert CheckoutEvent.objects.get(event_type=EventType.LOGIN_LINK_SENT).metadata == {'delivery': 'sent'}

    def test_a_mail_failure_is_logged_not_hidden(self, policy, public_client):
        with patch('checkout.services.send_app_mail', side_effect=OSError('smtp down')):
            assert public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json').status_code == 200
        assert CheckoutEvent.objects.get(event_type=EventType.LOGIN_LINK_SENT).metadata == {'delivery': 'failed'}

    def test_access_is_per_user_not_per_domain(self, policy, public_client):
        mail.outbox.clear()
        public_client.post(BASE + 'auth/request/', {'email': 'someone@lab.local'}, format='json')
        assert mail.outbox == []

    def test_revoked_guests_and_disabled_datastores_get_nothing(self, policy, public_client):
        CheckoutGuest.objects.update(revoked_at=timezone.now())
        mail.outbox.clear()
        public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        assert mail.outbox == []

    def test_the_token_is_stored_hashed(self, policy, public_client):
        public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        token = _link_from_mail()
        assert token not in CheckoutLoginLink.objects.get().token_hash

    def test_peeking_does_not_use_the_link_up_but_signing_in_does(self, policy, public_client):
        public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        token = _link_from_mail()
        for _ in range(3):
            peek = public_client.post(BASE + 'auth/link/', {'token': token}, format='json')
            assert peek.status_code == 200 and peek.json()['email'] == 'g***@lab.local'
        assert public_client.post(BASE + 'auth/consume/', {'token': token}, format='json').status_code == 200
        assert APIClient().post(BASE + 'auth/consume/', {'token': token}, format='json').status_code == 400
        assert APIClient().post(BASE + 'auth/link/', {'token': token}, format='json').status_code == 400

    def test_the_token_is_never_accepted_in_an_address(self, policy, public_client):
        public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        token = _link_from_mail()
        assert public_client.get(BASE + 'auth/link/', {'token': token}).status_code == 405   # GET is not offered

    def test_links_expire_after_fifteen_minutes(self, policy, public_client):
        public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        token = _link_from_mail()
        CheckoutLoginLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        assert public_client.post(BASE + 'auth/consume/', {'token': token}, format='json').status_code == 400

    def test_the_lifetime_is_fifteen_minutes(self, policy, public_client):
        public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        remaining = CheckoutLoginLink.objects.get().expires_at - timezone.now()
        assert timedelta(minutes=14) < remaining <= timedelta(minutes=15)

    def test_a_newer_link_voids_the_older_one(self, policy, public_client):
        public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        first = _link_from_mail()
        public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        assert public_client.post(BASE + 'auth/consume/', {'token': first}, format='json').status_code == 400

    def test_garbage_tokens_are_refused(self, policy, public_client):
        assert public_client.post(BASE + 'auth/consume/', {'token': 'nope'}, format='json').status_code == 400

    def test_requests_are_throttled_by_the_security_settings(self, policy, public_client):
        from security.models import SecuritySettings
        from security.services import clear_cache
        row = SecuritySettings.load()
        row.code_requests_per_email = 1
        row.save()
        clear_cache()
        mail.outbox.clear()
        for _ in range(4):
            public_client.post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        assert len(mail.outbox) == 1
        assert CheckoutEvent.objects.filter(event_type=EventType.AUTH_THROTTLED).exists()

    def test_sign_out_and_expiry(self, policy):
        client = sign_in()
        assert client.get(BASE + 'datarooms/').status_code == 200
        assert client.delete(BASE + 'session/').status_code == 204
        assert client.get(BASE + 'datarooms/').status_code == 401
        client = sign_in()
        from checkout.models import CheckoutSession
        CheckoutSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        assert client.get(BASE + 'datarooms/').status_code == 401

    def test_revoking_the_guest_ends_the_session(self, policy):
        client = sign_in()
        CheckoutGuest.objects.update(revoked_at=timezone.now())
        assert client.get(BASE + 'datarooms/').status_code == 401


class TestWhatAGuestSees:
    def test_only_their_datastores_and_files(self, policy, entry, organization, user):
        from datarooms.models import Dataroom
        other_room = Dataroom.objects.create(name='Secret room', organization=organization, created_by=user)
        CheckoutPolicy.objects.create(organization=organization, dataroom=other_room, enabled=True)
        data = sign_in().get(BASE + 'datarooms/').json()
        assert [r['name'] for r in data['datarooms']] == [policy.dataroom.name]
        file = data['datarooms'][0]['files'][0]
        assert file['name'] == 'Report.pdf' and file['state'] == 'available' and file['requires_approval'] is False

    def test_two_guests_see_different_things(self, policy, entry):
        CheckoutGuest.objects.create(policy=policy, email='second@lab.local')
        other = sign_in(email='second@lab.local')
        mine = sign_in()
        mine.post(f'{BASE}files/{entry.id}/request/')
        state = other.get(BASE + 'datarooms/').json()['datarooms'][0]['files'][0]['state']
        assert state == 'available'

    def test_requires_sign_in(self, policy):
        assert APIClient().get(BASE + 'datarooms/').status_code == 401


class TestRequestAndOpen:
    def test_an_auto_approved_file_can_be_opened_and_starts_a_timed_window(self, policy, entry):
        client = sign_in()
        request = client.post(f'{BASE}files/{entry.id}/request/').json()
        assert request['status'] == 'approved' and request['expires_at'] is None
        opened = client.post(f'{BASE}checkouts/{request["id"]}/open/').json()
        assert opened['status'] == 'open'
        window = Checkout.objects.get().expires_at - timezone.now()
        assert timedelta(minutes=4) < window <= timedelta(minutes=5)

    def test_asking_again_costs_no_attempt(self, policy, entry):
        client = sign_in()
        first = client.post(f'{BASE}files/{entry.id}/request/').json()
        second = client.post(f'{BASE}files/{entry.id}/request/').json()
        assert first['id'] == second['id'] and Checkout.objects.count() == 1

    def test_the_attempt_limit_survives_signing_in_again(self, policy, entry):
        client = sign_in()
        for _ in range(policy.max_attempts):
            Checkout.objects.create(session=_session(), policy=policy, file=entry, email=GUEST,
                                    status=Checkout.ENDED, expires_at=timezone.now())
        denied = client.post(f'{BASE}files/{entry.id}/request/')
        assert denied.status_code == 403 and denied.json()['code'] == 'attempt_limit'
        assert sign_in().post(f'{BASE}files/{entry.id}/request/').status_code == 403

    def test_foreign_and_unknown_files_look_the_same(self, policy, entry):
        client = sign_in()
        assert client.post(f'{BASE}files/nope/request/').status_code == 404
        CheckoutGuest.objects.update(revoked_at=timezone.now())
        assert client.post(f'{BASE}files/{entry.id}/request/').status_code == 401

    def test_returning_ends_the_window(self, policy, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        assert client.post(f'{BASE}checkouts/{cid}/return/').json()['status'] == 'ended'
        assert client.get(f'{BASE}checkouts/{cid}/').status_code == 403

    def test_an_expired_window_cannot_be_reopened(self, policy, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        Checkout.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        assert client.post(f'{BASE}checkouts/{cid}/open/').status_code == 403
        assert Checkout.objects.get().status == Checkout.ENDED

    def test_an_unopened_request_lapses(self, policy, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        Checkout.objects.update(created_at=timezone.now() - timedelta(hours=25))
        assert client.post(f'{BASE}checkouts/{cid}/open/').json()['code'] == 'expired'

    def test_another_guest_cannot_use_my_checkout(self, policy, entry):
        CheckoutGuest.objects.create(policy=policy, email='second@lab.local')
        cid = sign_in().post(f'{BASE}files/{entry.id}/request/').json()['id']
        assert sign_in(email='second@lab.local').post(f'{BASE}checkouts/{cid}/open/').status_code == 404


def _session():
    from checkout.models import CheckoutSession
    return CheckoutSession.objects.first() or CheckoutSession.objects.create(
        email=GUEST, expires_at=timezone.now() + timedelta(minutes=30))


class TestViewing:
    def _open(self, client, entry):
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        return cid

    def test_detail_lists_the_pages_and_time_left(self, policy, entry):
        client = sign_in()
        cid = self._open(client, entry)
        data = client.get(f'{BASE}checkouts/{cid}/').json()
        assert data['pages'] == [{'page_number': 1, 'width': 300, 'height': 400}]
        assert data['expires_at'] and data['allow_download'] is False

    def test_pages_are_post_only_script_only_and_watermarked(self, policy, entry):
        client = sign_in()
        cid = self._open(client, entry)
        url = f'{BASE}checkouts/{cid}/pages/1/'
        assert client.get(url).status_code == 405
        assert client.post(url).status_code == 403
        get, fileserver = storage()
        with get, fileserver:
            response = client.post(url, **SCRIPT)
        assert response.status_code == 200 and 'no-store' in response['Cache-Control']
        sent = Image.open(BytesIO(response.content)).convert('RGB')
        plain = Image.open(BytesIO(png())).convert('RGB')
        assert sent.tobytes() != plain.tobytes()   # the guest's address is burned in

    def test_nothing_is_served_before_opening_or_after_the_window(self, policy, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        get, fileserver = storage()
        with get, fileserver:
            assert client.post(f'{BASE}checkouts/{cid}/pages/1/', **SCRIPT).status_code == 403
            client.post(f'{BASE}checkouts/{cid}/open/')
            assert client.post(f'{BASE}checkouts/{cid}/pages/1/', **SCRIPT).status_code == 200
            Checkout.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
            assert client.post(f'{BASE}checkouts/{cid}/pages/1/', **SCRIPT).status_code == 403

    def test_downloads_are_off_by_default(self, policy, entry):
        client = sign_in()
        cid = self._open(client, entry)
        assert client.post(f'{BASE}checkouts/{cid}/download/', **SCRIPT).status_code == 403

    def test_downloads_when_allowed_are_limited(self, policy, entry):
        policy.allow_download = True
        policy.downloads_per_checkout = 1
        policy.save()
        client = sign_in()
        cid = self._open(client, entry)
        upstream = MagicMock()
        upstream.iter_content.return_value = iter([b'hello ', b'world'])
        upstream.raise_for_status = lambda: None
        with patch('checkout.views.requests.get', return_value=upstream), \
             patch('checkout.views.fileserver_client.generate_download_url', return_value='http://core/f'):
            response = client.post(f'{BASE}checkouts/{cid}/download/', **SCRIPT)
            body = b''.join(response.streaming_content)
        assert response.status_code == 200 and body == b'hello world' and 'attachment' in response['Content-Disposition']
        assert client.post(f'{BASE}checkouts/{cid}/download/', **SCRIPT).status_code == 403


class TestNda:
    def test_it_must_be_accepted_before_requesting_or_viewing(self, policy, entry):
        policy.require_nda = True
        policy.nda_text = 'Keep it secret.'
        policy.save()
        client = sign_in()
        room = client.get(BASE + 'datarooms/').json()['datarooms'][0]
        assert room['nda'] == {'required': True, 'accepted': False, 'text': 'Keep it secret.'}
        refused = client.post(f'{BASE}files/{entry.id}/request/')
        assert refused.status_code == 403 and refused.json()['code'] == 'nda_required'
        assert client.post(f'{BASE}datarooms/{policy.id}/nda/').status_code == 204
        assert client.post(f'{BASE}files/{entry.id}/request/').status_code == 200
        assert CheckoutNdaAcceptance.objects.filter(email=GUEST, nda_version=1).exists()
        assert CheckoutEvent.objects.filter(event_type=EventType.NDA_ACCEPTED).exists()

    def test_changing_the_text_requires_a_new_acceptance(self, policy, entry):
        policy.require_nda, policy.nda_text = True, 'v1'
        policy.save()
        client = sign_in()
        client.post(f'{BASE}datarooms/{policy.id}/nda/')
        policy.refresh_from_db()
        policy.nda_text = 'v2'
        policy.save()
        assert policy.nda_version == 2
        room = client.get(BASE + 'datarooms/').json()['datarooms'][0]
        assert room['nda']['accepted'] is False and room['nda']['text'] == 'v2'

    def test_accepting_for_a_datastore_that_is_not_yours_fails(self, policy, organization, user):
        from datarooms.models import Dataroom
        room = Dataroom.objects.create(name='Other', organization=organization, created_by=user)
        other = CheckoutPolicy.objects.create(organization=organization, dataroom=room, enabled=True, require_nda=True, nda_text='x')
        assert sign_in().post(f'{BASE}datarooms/{other.id}/nda/').status_code == 404


class TestApproval:
    @pytest.fixture(autouse=True)
    def needs_approval(self, entry):
        entry.requires_approval = True
        entry.save()

    def test_the_request_waits_and_the_admin_is_told(self, policy, entry, admin_api_client):
        policy.notify_mode, policy.notify_email = 'group', 'team@lab.local'
        policy.save()
        client = sign_in()
        mail.outbox.clear()
        request = client.post(f'{BASE}files/{entry.id}/request/').json()
        assert request['status'] == 'pending'
        assert len(mail.outbox) == 1 and 'approval' in mail.outbox[0].subject.lower()
        # the group address, the file's owner and (the owner not being one) the organization's administrators
        assert {'team@lab.local', 'test@example.com', 'admin@example.com'} <= set(mail.outbox[0].to)
        assert client.post(f'{BASE}checkouts/{request["id"]}/open/').status_code == 409
        assert CheckoutEvent.objects.filter(event_type=EventType.APPROVAL_REQUIRED).exists()
        assert admin_api_client.get(BASE + 'admin/policies/').json()['pending_approvals'] == 1

    def test_approving_lets_the_guest_open_it_and_tells_them(self, policy, entry, admin_api_client):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        mail.outbox.clear()
        assert admin_api_client.post(f'{BASE}admin/approvals/{cid}/approve/').status_code == 200
        assert [m.to for m in mail.outbox] == [[GUEST]] and 'approved' in mail.outbox[0].subject
        assert client.post(f'{BASE}checkouts/{cid}/open/').json()['status'] == 'open'
        state = client.get(BASE + 'datarooms/').json()['datarooms'][0]['files'][0]['state']
        assert state == 'open'

    def test_denying_blocks_it(self, policy, entry, admin_api_client):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        admin_api_client.post(f'{BASE}admin/approvals/{cid}/deny/')
        assert client.post(f'{BASE}checkouts/{cid}/open/').status_code == 403
        state = client.get(BASE + 'datarooms/').json()['datarooms'][0]['files'][0]['state']
        assert state == 'denied'
        assert client.post(f'{BASE}files/{entry.id}/request/').json()['code'] == 'denied'

    def test_a_decision_can_only_be_made_once(self, policy, entry, admin_api_client):
        cid = sign_in().post(f'{BASE}files/{entry.id}/request/').json()['id']
        assert admin_api_client.post(f'{BASE}admin/approvals/{cid}/approve/').status_code == 200
        assert admin_api_client.post(f'{BASE}admin/approvals/{cid}/deny/').status_code == 409

    def test_only_admins_decide(self, policy, entry, api_client):
        cid = sign_in().post(f'{BASE}files/{entry.id}/request/').json()['id']
        assert api_client.post(f'{BASE}admin/approvals/{cid}/approve/').status_code == 403
        assert APIClient().post(f'{BASE}admin/approvals/{cid}/approve/').status_code in (401, 403)

    def test_the_queue_lists_waiting_and_decided_requests(self, policy, entry, admin_api_client):
        cid = sign_in().post(f'{BASE}files/{entry.id}/request/').json()['id']
        queue = admin_api_client.get(BASE + 'admin/approvals/').json()
        assert [r['email'] for r in queue['pending']] == [GUEST] and queue['pending'][0]['file_name'] == 'Report.pdf'
        admin_api_client.post(f'{BASE}admin/approvals/{cid}/approve/')
        queue = admin_api_client.get(BASE + 'admin/approvals/').json()
        assert queue['pending'] == [] and queue['recent'][0]['status'] == 'approved'

    def test_files_without_the_flag_are_unaffected(self, policy, entry):
        entry.requires_approval = False
        entry.save()
        assert sign_in().post(f'{BASE}files/{entry.id}/request/').json()['status'] == 'approved'


class TestNotifications:
    def _open(self, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        mail.outbox.clear()
        client.post(f'{BASE}checkouts/{cid}/open/')

    def test_log_only_sends_nothing(self, policy, entry):
        self._open(entry)
        assert mail.outbox == []
        assert CheckoutEvent.objects.filter(event_type=EventType.FILE_CHECKED_OUT).exists()

    def test_group_address(self, policy, entry):
        policy.notify_mode, policy.notify_email = 'group', 'team@lab.local'
        policy.save()
        self._open(entry)
        assert [m.to for m in mail.outbox] == [['team@lab.local']]

    def test_document_owner(self, policy, entry, user):
        policy.notify_mode = 'owner'
        policy.save()
        self._open(entry)
        assert [m.to for m in mail.outbox] == [[user.email]]


class TestAdmin:
    def test_members_cannot_use_the_dashboard(self, api_client, policy):
        for path in ('admin/policies/', 'admin/events/', 'admin/approvals/'):
            assert api_client.get(BASE + path).status_code == 403

    def test_create_and_validate_a_policy(self, admin_api_client, dataroom, settings):
        too_long = admin_api_client.post(BASE + 'admin/policies/', {'dataroom': dataroom.id, 'checkout_minutes': settings.CHECKOUT_MAX_MINUTES + 1}, format='json')
        assert too_long.status_code == 400
        nda_without_text = admin_api_client.post(BASE + 'admin/policies/', {'dataroom': dataroom.id, 'require_nda': True}, format='json')
        assert nda_without_text.status_code == 400
        group_without_address = admin_api_client.post(BASE + 'admin/policies/', {'dataroom': dataroom.id, 'notify_mode': 'group'}, format='json')
        assert group_without_address.status_code == 400
        ok = admin_api_client.post(BASE + 'admin/policies/', {'dataroom': dataroom.id, 'enabled': True, 'checkout_minutes': 3,
                                                              'require_nda': True, 'nda_text': 'Secret.'}, format='json')
        assert ok.status_code == 201, ok.content
        assert ok.json()['nda_version'] == 1 and 'allowed_domains' not in ok.json()

    def test_guests_are_added_invited_and_revoked(self, admin_api_client, policy):
        mail.outbox.clear()
        response = admin_api_client.post(f'{BASE}admin/policies/{policy.id}/guests/',
                                         {'emails': ['New@lab.local, bad', 'second@lab.local']}, format='json')
        assert response.status_code == 201
        body = response.json()
        assert sorted(g['email'] for g in body['added']) == ['new@lab.local', 'second@lab.local'] and body['invalid'] == ['bad']
        assert sorted(m.to[0] for m in mail.outbox) == ['new@lab.local', 'second@lab.local']
        guest = CheckoutGuest.objects.get(email='new@lab.local')
        assert admin_api_client.delete(f'{BASE}admin/policies/{policy.id}/guests/{guest.id}/').status_code == 204
        listing = admin_api_client.get(f'{BASE}admin/policies/{policy.id}/guests/').json()['results']
        assert next(g for g in listing if g['email'] == 'new@lab.local')['is_active'] is False

    def test_adding_guests_to_a_disabled_datastore_warns_the_admin(self, admin_api_client, policy):
        policy.enabled = False
        policy.save()
        body = admin_api_client.post(f'{BASE}admin/policies/{policy.id}/guests/', {'emails': ['x@lab.local']}, format='json').json()
        assert body['policy_enabled'] is False and 'not enabled' in body['warning']
        policy.enabled = True
        policy.save()
        body = admin_api_client.post(f'{BASE}admin/policies/{policy.id}/guests/', {'emails': ['y@lab.local']}, format='json').json()
        assert body['policy_enabled'] is True and 'warning' not in body

    def test_revoking_a_guest_ends_an_open_window(self, admin_api_client, policy, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        guest = CheckoutGuest.objects.get(email=GUEST)
        admin_api_client.delete(f'{BASE}admin/policies/{policy.id}/guests/{guest.id}/')
        assert Checkout.objects.get().status == Checkout.ENDED

    def test_publish_files_with_the_approval_flag(self, admin_api_client, policy):
        policy.files.all().delete()
        listing = admin_api_client.get(f'{BASE}admin/policies/{policy.id}/files/').json()['results']
        assert listing[0]['visible'] is False and listing[0]['configured'] is False
        response = admin_api_client.put(f'{BASE}admin/policies/{policy.id}/files/',
                                        {'files': [{'id': listing[0]['id'], 'requires_approval': True}]}, format='json')
        assert response.status_code == 200
        assert policy.files.get().requires_approval is True
        again = admin_api_client.get(f'{BASE}admin/policies/{policy.id}/files/').json()['results'][0]
        assert again['visible'] and again['requires_approval']
        admin_api_client.put(f'{BASE}admin/policies/{policy.id}/files/', {'files': []}, format='json')
        assert policy.files.count() == 0

    def test_cannot_publish_foreign_documents(self, admin_api_client, policy):
        response = admin_api_client.put(f'{BASE}admin/policies/{policy.id}/files/',
                                        {'files': [{'id': '01HZZZZZZZZZZZZZZZZZZZZZZZ'}]}, format='json')
        assert response.status_code == 400

    def test_event_log_filters(self, admin_api_client, policy, entry):
        client = sign_in()
        client.post(f'{BASE}files/{entry.id}/request/')
        data = admin_api_client.get(BASE + 'admin/events/', {'event_type': 'file_requested'}).json()
        assert data['count'] == 1 and data['results'][0]['email'] == GUEST
        assert data['results'][0]['datastore'] == policy.dataroom.name


class TestViewerEvents:
    def _open(self, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        return client, cid

    def test_behaviour_signals_from_the_viewer_are_recorded_with_the_file(self, policy, entry):
        client, cid = self._open(entry)
        for event in ('print_attempt', 'save_attempt', 'screenshot_key', 'context_menu', 'devtools_key', 'drag_attempt'):
            assert client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': event}, format='json').status_code == 204
        logged = CheckoutEvent.objects.filter(event_type='save_attempt').get()
        assert logged.email == GUEST and logged.metadata['file'] == 'Report.pdf' and logged.policy == policy
        assert CheckoutEvent.objects.filter(event_type__in=['print_attempt', 'screenshot_key', 'context_menu']).count() == 3

    def test_only_known_signals_are_accepted(self, policy, entry):
        client, cid = self._open(entry)
        for bad in ('file_checked_out', 'auth_granted', 'nonsense', ''):
            assert client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': bad}, format='json').status_code == 400

    def test_rapid_repeats_are_all_kept(self, policy, entry):
        client, cid = self._open(entry)
        for _ in range(5):
            client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'context_menu'}, format='json')
        assert CheckoutEvent.objects.filter(event_type='context_menu').count() == 5

    def test_a_signal_just_after_the_window_closes_is_still_kept(self, policy, entry):
        client, cid = self._open(entry)
        Checkout.objects.update(expires_at=timezone.now() - timedelta(seconds=30))
        assert client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'print_attempt'}, format='json').status_code == 204
        Checkout.objects.update(expires_at=timezone.now() - timedelta(minutes=5))
        assert client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'print_attempt'}, format='json').status_code == 403

    def test_needs_a_session_and_an_opened_checkout(self, policy, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']     # approved, not opened
        assert client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'print_attempt'}, format='json').status_code == 403
        assert APIClient().post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'print_attempt'}, format='json').status_code == 401

    def test_another_guest_cannot_report_into_my_checkout(self, policy, entry):
        client, cid = self._open(entry)
        CheckoutGuest.objects.create(policy=policy, email='second@lab.local')
        intruder = sign_in(email='second@lab.local')
        assert intruder.post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'print_attempt'}, format='json').status_code == 404

    def test_the_limit_is_per_session(self, policy, entry, monkeypatch):
        monkeypatch.setattr('checkout.services.CLIENT_EVENTS_PER_MINUTE', 3)
        client, cid = self._open(entry)
        for _ in range(6):
            client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'context_menu'}, format='json')
        assert CheckoutEvent.objects.filter(event_type='context_menu').count() == 3

    def test_returning_records_the_seconds_viewed(self, policy, entry):
        client, cid = self._open(entry)
        Checkout.objects.update(opened_at=timezone.now() - timedelta(seconds=90))
        client.post(f'{BASE}checkouts/{cid}/return/')
        event = CheckoutEvent.objects.get(event_type=EventType.CHECKOUT_RETURNED)
        assert 85 <= event.metadata['seconds'] <= 100


class TestPerFileSettings:
    """Access settings live on the file in the datastore: no share link is involved anywhere."""

    def test_a_file_shows_up_for_setup_right_after_upload_before_anything_is_shared(self, admin_api_client, policy, dataroom, organization, user):
        from documents.models import Document
        doc = Document.objects.create(name='New upload.pdf', organization=organization, created_by=user, status='ready')
        DataroomDocument.objects.create(dataroom=dataroom, document=doc, name='New upload.pdf')
        rows = admin_api_client.get(f'{BASE}admin/policies/{policy.id}/files/').json()['results']
        fresh = next(r for r in rows if r['name'] == 'New upload.pdf')
        assert fresh['visible'] is False and fresh['configured'] is False   # nothing is exposed until the admin decides

    def test_visibility_controls_what_guests_see(self, policy, entry):
        client = sign_in()
        assert len(client.get(BASE + 'datarooms/').json()['datarooms'][0]['files']) == 1
        entry.visible = False
        entry.save()
        assert client.get(BASE + 'datarooms/').json()['datarooms'][0]['files'] == []
        assert client.post(f'{BASE}files/{entry.id}/request/').status_code == 404

    def test_hiding_a_file_cuts_off_an_open_view(self, policy, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        entry.visible = False
        entry.save()
        assert client.get(f'{BASE}checkouts/{cid}/').status_code == 403

    def test_nda_can_be_required_for_one_file_only(self, policy, entry, dataroom, document):
        policy.nda_text = 'Secret.'
        policy.save()
        other_doc = DataroomDocument.objects.create(dataroom=dataroom, document=document, name='Other.pdf')
        other = CheckoutFile.objects.create(policy=policy, dataroom_document=other_doc)
        entry.requires_nda = True
        entry.save()
        client = sign_in()
        room = client.get(BASE + 'datarooms/').json()['datarooms'][0]
        assert room['nda']['required'] is True
        by_name = {f['name']: f for f in room['files']}
        assert by_name['Report.pdf']['requires_nda'] is True and by_name['Other.pdf']['requires_nda'] is False
        assert client.post(f'{BASE}files/{entry.id}/request/').json()['code'] == 'nda_required'
        assert client.post(f'{BASE}files/{other.id}/request/').status_code == 200
        client.post(f'{BASE}datarooms/{policy.id}/nda/')
        assert client.post(f'{BASE}files/{entry.id}/request/').status_code == 200

    def test_nda_can_be_waived_for_a_file_when_the_datastore_requires_it(self, policy, entry):
        policy.require_nda, policy.nda_text = True, 'Secret.'
        policy.save()
        entry.requires_nda = False
        entry.save()
        assert sign_in().post(f'{BASE}files/{entry.id}/request/').status_code == 200

    def test_download_can_be_allowed_for_one_file_only(self, policy, entry):
        entry.allow_download = True
        entry.save()
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        assert client.get(f'{BASE}checkouts/{cid}/').json()['allow_download'] is True

    def test_download_can_be_blocked_for_a_file_when_the_datastore_allows_it(self, policy, entry):
        policy.allow_download = True
        policy.save()
        entry.allow_download = False
        entry.save()
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        assert client.get(f'{BASE}checkouts/{cid}/').json()['allow_download'] is False
        assert client.post(f'{BASE}checkouts/{cid}/download/', **SCRIPT).status_code == 403

    def test_settings_are_saved_per_file_including_unset(self, admin_api_client, policy, entry):
        policy.nda_text = 'Secret.'
        policy.save()
        file_id = str(entry.dataroom_document_id)
        response = admin_api_client.put(f'{BASE}admin/policies/{policy.id}/files/', {'files': [{
            'id': file_id, 'visible': True, 'requires_approval': True, 'requires_nda': True, 'allow_download': None}]}, format='json')
        assert response.status_code == 200, response.content
        entry.refresh_from_db()
        assert (entry.requires_approval, entry.requires_nda, entry.allow_download) == (True, True, None)
        row = admin_api_client.get(f'{BASE}admin/policies/{policy.id}/files/').json()['results'][0]
        assert row['requires_nda'] is True and row['allow_download'] is None

    def test_an_nda_for_a_file_needs_the_datastore_nda_text(self, admin_api_client, policy, entry):
        response = admin_api_client.put(f'{BASE}admin/policies/{policy.id}/files/', {'files': [{
            'id': str(entry.dataroom_document_id), 'requires_nda': True}]}, format='json')
        assert response.status_code == 400


class TestPictureFiles:
    def test_psd_and_images_are_viewed_as_one_drawn_page(self, policy, entry, document):
        document.name = 'Design.psd'
        document.type = 'psd'
        document.save()
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        assert client.get(f'{BASE}checkouts/{cid}/').json()['pages'] == [{'page_number': 1, 'width': None, 'height': None}]
        get, fileserver = storage()
        with get, fileserver:
            response = client.post(f'{BASE}checkouts/{cid}/pages/1/', **SCRIPT)
            missing = client.post(f'{BASE}checkouts/{cid}/pages/2/', **SCRIPT)
        assert response.status_code == 200 and response['Content-Type'] == 'image/jpeg'
        assert missing.status_code == 404

    def test_huge_pictures_are_refused(self, policy, entry, document):
        document.name = 'Huge.psd'
        document.save()
        version = document.versions.get(is_primary=True)
        version.file_size = 500 * 1024 * 1024
        version.save()
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        assert client.post(f'{BASE}checkouts/{cid}/pages/1/', **SCRIPT).status_code == 413


class TestPreviewState:
    """A file that cannot be shown yet says why (still preparing / failed / not a viewable type)."""

    def _detail(self, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        return client.get(f'{BASE}checkouts/{cid}/').json()

    def test_a_ready_pdf(self, policy, entry):
        body = self._detail(entry)
        assert body['preview']['state'] == 'ready' and body['pages']

    def test_a_pdf_without_a_document_type_is_still_recognised(self, policy, entry, document):
        document.type = ''
        document.save()
        version = document.versions.get(is_primary=True)
        version.content_type = 'application/pdf'
        version.save()
        assert self._detail(entry)['preview']['state'] == 'ready'

    def test_a_pdf_still_being_prepared(self, policy, entry, document):
        version = document.versions.get(is_primary=True)
        version.has_pages, version.render_status = False, 'processing'
        version.save()
        assert self._detail(entry)['preview']['state'] == 'processing'

    def test_opening_a_pdf_that_was_never_rendered_starts_the_preview_once(self, policy, entry, document):
        """The cause of 'still being prepared' forever: previews start lazily and nothing here started one."""
        version = document.versions.get(is_primary=True)
        version.type, version.has_pages, version.render_status = 'pdf', False, 'not_generated'
        version.save()
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        with patch('documents.tasks.generate_pdf_pages_task.delay') as dispatch:
            first = client.get(f'{BASE}checkouts/{cid}/').json()
            second = client.get(f'{BASE}checkouts/{cid}/').json()
        assert first['preview']['state'] == second['preview']['state'] == 'processing'
        assert dispatch.call_count == 1                      # idempotent: one job, however often it is polled
        version.refresh_from_db()
        assert version.render_status == 'queued'

    def test_a_file_that_cannot_be_previewed_is_not_reported_as_preparing(self, policy, entry, document):
        version = document.versions.get(is_primary=True)
        version.has_pages, version.render_status, version.file_size = False, 'not_applicable', 10 ** 12
        version.save()
        assert self._detail(entry)['preview']['state'] == 'unsupported'

    def test_a_failure_to_start_the_job_is_reported(self, policy, entry, document):
        version = document.versions.get(is_primary=True)
        version.has_pages, version.render_status = False, 'not_generated'
        version.save()
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        with patch('checkout.views.get_renderer', side_effect=RuntimeError('broken')):
            assert client.get(f'{BASE}checkouts/{cid}/').json()['preview']['state'] == 'failed'

    def test_a_pdf_whose_preview_failed(self, policy, entry, document):
        version = document.versions.get(is_primary=True)
        version.has_pages, version.render_status = False, 'failed'
        version.save()
        assert self._detail(entry)['preview']['state'] == 'failed'

    def test_other_file_types_are_unsupported(self, policy, entry, document):
        document.name, document.type = 'Notes.docx', 'docx'
        document.save()
        version = document.versions.get(is_primary=True)
        version.has_pages, version.content_type = False, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        version.save()
        assert self._detail(entry)['preview']['state'] == 'unsupported'


class TestFileProtectionsApplyToCheckout:
    """The access settings of the file (set on the file, not on a link) are enforced when it is checked out."""

    def _open(self, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        return client, cid

    def _policy(self, document, **fields):
        from sharelinks.models import AccessPolicy
        return AccessPolicy.objects.create(document=document, **fields)

    def _page(self, client, cid, suffix='', content=None, **kwargs):
        get, fileserver = storage()
        if content is not None:
            get = patch('checkout.views.requests.get', return_value=MagicMock(content=content, raise_for_status=lambda: None))
        with get, fileserver:
            return client.post(f'{BASE}checkouts/{cid}/pages/1/{suffix}', **SCRIPT, **kwargs)

    @staticmethod
    def _checker():
        image = Image.new('RGB', (300, 400), 'white')
        px = image.load()
        for x in range(300):
            for y in range(400):
                if (x // 3 + y // 3) % 2 == 0:
                    px[x, y] = (0, 0, 0)
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        return buffer.getvalue()

    def _roughness(self, content):
        image = Image.open(BytesIO(content)).convert('L')
        px, (w, h) = image.load(), image.size
        return sum(abs(px[x, y] - px[x + 1, y]) for y in range(0, h, 5) for x in range(w - 1))

    def test_restricted_pane_blurs_the_page_on_the_server(self, policy, entry, document):
        client, cid = self._open(entry)
        checker = self._checker()
        plain = self._roughness(self._page(client, cid, content=checker).content)
        self._policy(document, restricted_viewing_pane=True)
        blurred = self._page(client, cid, content=checker)
        assert blurred.status_code == 200
        assert self._roughness(blurred.content) < plain / 3      # the page is blurred before it leaves the server

    def test_clear_pixels_only_leave_as_rate_limited_tiles(self, policy, entry, document, settings):
        self._policy(document, restricted_viewing_pane=True)
        client, cid = self._open(entry)
        response = self._page(client, cid, 'tiles/', data={'tiles': [[0, 0], [1, 0]]}, format='json')
        assert response.status_code == 200
        body = response.json()
        assert body['tile_size'] > 0 and {(t['c'], t['r']) for t in body['tiles']} == {(0, 0), (1, 0)}
        settings.SPOTLIGHT_TILES_PER_MINUTE = 3
        codes = [self._page(client, cid, 'tiles/', data={'tiles': [[0, 0], [1, 0]]}, format='json').status_code for _ in range(2)]
        assert 429 in codes

    def test_tiles_are_not_available_on_a_file_without_the_restricted_pane(self, policy, entry):
        client, cid = self._open(entry)
        assert self._page(client, cid, 'tiles/', data={'tiles': [[0, 0]]}, format='json').status_code == 404

    def test_blur_when_unfocused_stops_pages_until_the_viewer_reports_focus(self, policy, entry, document):
        self._policy(document, blur_when_unfocused=True)
        client, cid = self._open(entry)
        assert self._page(client, cid).status_code == 423
        assert client.post(f'{BASE}checkouts/{cid}/focus/', {'focused': True}, format='json').status_code == 204
        assert self._page(client, cid).status_code == 200
        client.post(f'{BASE}checkouts/{cid}/focus/', {'focused': False}, format='json')
        assert self._page(client, cid).status_code == 423

    def test_a_stale_focus_lease_expires(self, policy, entry, document, settings):
        import time
        self._policy(document, blur_when_unfocused=True)
        client, cid = self._open(entry)
        client.post(f'{BASE}checkouts/{cid}/focus/', {'focused': True}, format='json')
        with patch('sharelinks.protected_pages.time.time', return_value=time.time() + 600):
            assert self._page(client, cid).status_code == 423

    def test_focus_reports_need_a_boolean_and_an_open_checkout(self, policy, entry):
        client, cid = self._open(entry)
        assert client.post(f'{BASE}checkouts/{cid}/focus/', {'focused': 'yes'}, format='json').status_code == 400
        assert APIClient().post(f'{BASE}checkouts/{cid}/focus/', {'focused': True}, format='json').status_code == 401

    def test_files_without_the_option_ignore_the_lease(self, policy, entry):
        client, cid = self._open(entry)
        assert self._page(client, cid).status_code == 200

    def test_the_files_watermark_text_is_used(self, policy, entry, document):
        client, cid = self._open(entry)
        before = self._page(client, cid).content
        self._policy(document, enable_watermark=True, watermark_text='STRICTLY {{email}}')
        after = self._page(client, cid).content
        assert before != after

    def test_a_datastore_policy_applies_to_every_file_in_it(self, policy, entry, dataroom):
        from sharelinks.models import AccessPolicy
        AccessPolicy.objects.create(dataroom=dataroom, blur_when_unfocused=True)
        client, cid = self._open(entry)
        assert self._page(client, cid).status_code == 423

    def test_the_detail_tells_the_viewer_which_protections_apply(self, policy, entry, document):
        self._policy(document, restricted_viewing_pane=True, blur_when_unfocused=True)
        client, cid = self._open(entry)
        body = client.get(f'{BASE}checkouts/{cid}/').json()
        assert body['protections'] == {'blur_when_unfocused': True, 'restricted_viewing_pane': True}

    def test_a_file_that_forbids_downloads_cannot_be_downloaded_even_if_the_checkout_allows_it(self, policy, entry, document):
        policy.allow_download = True
        policy.save()
        self._policy(document, allow_download=False)
        client, cid = self._open(entry)
        assert client.get(f'{BASE}checkouts/{cid}/').json()['allow_download'] is False
        assert client.post(f'{BASE}checkouts/{cid}/download/', **SCRIPT).status_code == 403


class TestCaptureActionsInCheckout:
    def _open(self, entry):
        client = sign_in()
        cid = client.post(f'{BASE}files/{entry.id}/request/').json()['id']
        client.post(f'{BASE}checkouts/{cid}/open/')
        return client, cid

    def _configure(self, **values):
        from security.models import SecuritySettings
        from security.services import clear_cache
        row = SecuritySettings.load()
        for k, v in values.items():
            setattr(row, k, v)
        row.save()
        clear_cache()

    def test_the_viewer_is_told_what_to_do_for_each_signal(self, policy, entry):
        self._configure(capture_actions={'print_attempt': 'blur', 'save_attempt': 'end'})
        client, cid = self._open(entry)
        actions = client.get(f'{BASE}checkouts/{cid}/').json()['capture_actions']
        assert actions['print_attempt'] == 'blur' and actions['save_attempt'] == 'end' and actions['copy_attempt'] == 'log'

    def test_ignored_signals_are_not_recorded(self, policy, entry):
        self._configure(capture_actions={'context_menu': 'ignore'})
        client, cid = self._open(entry)
        assert client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'context_menu'}, format='json').status_code == 204
        assert not CheckoutEvent.objects.filter(event_type='context_menu').exists()

    def test_end_closes_the_checkout(self, policy, entry):
        self._configure(capture_actions={'save_attempt': 'end'})
        client, cid = self._open(entry)
        client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'save_attempt'}, format='json')
        assert Checkout.objects.get().status == Checkout.ENDED
        assert client.get(f'{BASE}checkouts/{cid}/').status_code == 403
        assert CheckoutEvent.objects.filter(event_type='save_attempt').get().metadata['action'] == 'end'

    def test_notify_e_mails_the_alert_address(self, policy, entry):
        self._configure(capture_actions={'print_attempt': 'notify'}, alert_group_email='sec@lab.local')
        client, cid = self._open(entry)
        mail.outbox.clear()
        client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'print_attempt'}, format='json')
        assert [m.to for m in mail.outbox] == [['sec@lab.local']] and GUEST in mail.outbox[0].body

    def test_log_only_changes_nothing_for_the_guest(self, policy, entry):
        client, cid = self._open(entry)
        client.post(f'{BASE}checkouts/{cid}/events/', {'event_type': 'print_attempt'}, format='json')
        assert Checkout.objects.get().status == Checkout.OPEN


class TestApprovalEmail:
    """A request that needs approval must reach someone who can decide, whatever the notify setting is."""

    def _request(self, policy, entry):
        CheckoutFile.objects.filter(pk=entry.pk).update(requires_approval=True)
        mail.outbox.clear()
        client = sign_in()
        mail.outbox.clear()
        assert client.post(f'{BASE}files/{entry.id}/request/').status_code in (200, 201)

    def test_owner_and_admins_are_told_even_when_notifications_are_log_only(self, policy, entry, admin_user, user):
        assert policy.notify_mode == 'log'
        self._request(policy, entry)
        recipients = {r for m in mail.outbox for r in m.to}
        assert user.email in recipients and admin_user.email in recipients
        assert any('Approval needed' in m.subject for m in mail.outbox)

    def test_an_owner_who_is_an_admin_is_not_joined_by_every_other_admin(self, policy, entry, admin_user, organization, document):
        document.created_by = admin_user
        document.save()
        from django.contrib.auth import get_user_model
        get_user_model().objects.create_user(username='other@example.com', email='other@example.com', password='x',
                                              role='admin', organization=organization)
        self._request(policy, entry)
        recipients = {r for m in mail.outbox for r in m.to}
        assert recipients == {admin_user.email}

    def test_the_group_address_is_included_when_set(self, policy, entry, admin_user):
        CheckoutPolicy.objects.filter(pk=policy.pk).update(notify_mode='group', notify_email='approvers@example.com')
        self._request(policy, entry)
        assert 'approvers@example.com' in {r for m in mail.outbox for r in m.to}


class TestCaching:
    def test_check_out_answers_are_never_cacheable_and_signing_out_clears_the_cache(self, policy, entry):
        client = sign_in()
        r = client.get(BASE + 'datarooms/')
        assert 'no-store' in r['Cache-Control']
        out = client.delete(BASE + 'session/')
        assert out.status_code == 204 and out['Clear-Site-Data'] == '"cache"'

    def test_the_sign_in_link_keeps_its_token_out_of_the_query_string(self, policy):
        mail.outbox.clear()
        APIClient().post(BASE + 'auth/request/', {'email': GUEST}, format='json')
        body = mail.outbox[0].body
        assert '/checkout#token=' in body and '?token=' not in body
