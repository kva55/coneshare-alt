"""Access settings set on the file itself, right after upload, without any share link."""
import pytest
from rest_framework.test import APIClient

from checkout.models import CheckoutFile, CheckoutPolicy
from datarooms.models import DataroomDocument

pytestmark = pytest.mark.django_db


@pytest.fixture
def uploaded(dataroom, document):
    return DataroomDocument.objects.create(dataroom=dataroom, document=document, name='Fresh upload.pdf')


def url(dd):
    return f'/api/v1/checkout/dataroom-documents/{dd.id}/access/'


class TestDocumentAccess:
    def test_a_fresh_upload_reports_hidden_defaults_and_the_state_of_its_datastore(self, api_client, uploaded):
        body = api_client.get(url(uploaded)).json()
        assert body['visible'] is False and body['requires_approval'] is False
        assert body['requires_nda'] is None and body['allow_download'] is None
        assert body['datastore_settings'] == {'enabled': False, 'guest_count': 0, 'nda_text_set': False,
                                              'default_nda': False, 'default_download': False}
        assert body['datastore'] == 'Test Dataroom'

    def test_the_owner_sets_it_without_any_link_and_without_being_an_org_admin(self, api_client, uploaded):
        response = api_client.put(url(uploaded), {'visible': True, 'requires_approval': True, 'allow_download': False}, format='json')
        assert response.status_code == 200, response.content
        entry = CheckoutFile.objects.get(dataroom_document=uploaded)
        assert (entry.visible, entry.requires_approval, entry.allow_download) == (True, True, False)
        assert not uploaded.dataroom.share_links.exists()           # no link was ever created

    def test_saving_creates_the_datastore_policy_switched_off(self, api_client, uploaded):
        assert not CheckoutPolicy.objects.exists()
        api_client.put(url(uploaded), {'visible': True}, format='json')
        policy = CheckoutPolicy.objects.get()
        assert policy.enabled is False
        assert api_client.get(url(uploaded)).json()['datastore_settings']['enabled'] is False

    def test_an_nda_for_the_file_needs_the_datastore_text(self, api_client, uploaded):
        assert api_client.put(url(uploaded), {'visible': True, 'requires_nda': True}, format='json').status_code == 400
        policy = CheckoutPolicy.objects.get(dataroom=uploaded.dataroom)
        policy.nda_text = 'Secret.'
        policy.save()
        assert api_client.put(url(uploaded), {'visible': True, 'requires_nda': True}, format='json').status_code == 200

    def test_settings_can_be_changed_again(self, api_client, uploaded):
        api_client.put(url(uploaded), {'visible': True}, format='json')
        api_client.put(url(uploaded), {'visible': False}, format='json')
        assert CheckoutFile.objects.get().visible is False
        assert CheckoutFile.objects.count() == 1

    def test_people_who_cannot_see_the_datastore_get_nothing(self, uploaded, user2):
        other = APIClient()
        other.force_authenticate(user2)
        assert other.get(url(uploaded)).status_code == 404
        assert other.put(url(uploaded), {'visible': True}, format='json').status_code == 404
        assert not CheckoutFile.objects.exists()

    def test_requires_sign_in(self, uploaded):
        assert APIClient().get(url(uploaded)).status_code in (401, 403)

    def test_a_visible_file_reaches_a_guest_even_though_it_was_never_shared(self, api_client, uploaded):
        from checkout.models import CheckoutGuest
        api_client.put(url(uploaded), {'visible': True}, format='json')
        policy = CheckoutPolicy.objects.get()
        policy.enabled = True
        policy.save()
        CheckoutGuest.objects.create(policy=policy, email='guest@lab.local')
        from django.core import mail
        import re
        client = APIClient()
        mail.outbox.clear()
        client.post('/api/v1/checkout/auth/request/', {'email': 'guest@lab.local'}, format='json')
        token = re.search(r'token=([\w-]+)', mail.outbox[-1].body).group(1)
        client.post('/api/v1/checkout/auth/consume/', {'token': token}, format='json')
        files = client.get('/api/v1/checkout/datarooms/').json()['datarooms'][0]['files']
        assert [f['name'] for f in files] == ['Fresh upload.pdf']
