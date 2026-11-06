"""Access rules set on the document itself, inherited by every link, independent of link creation."""
import re

import pytest
from django.core import mail
from rest_framework import status
from rest_framework.test import APIClient

from datarooms.models import DataroomDocument
from sharelinks.models import AccessPolicy, ShareLink

pytestmark = pytest.mark.django_db


def policy_url(kind, ident):
    return f'/api/v1/access-policies/{kind}/{ident}/'


def view(client, link):
    return client.get(f'/api/v1/links/{link.slug}/view-data/')


class TestApi:
    def test_a_new_document_has_no_rules(self, api_client, document):
        body = api_client.get(policy_url('document', document.id)).json()
        assert body['exists'] is False and body['has_password'] is False and body['allowed_emails'] == []

    def test_the_owner_sets_the_rules_without_any_link(self, api_client, document):
        response = api_client.put(policy_url('document', document.id), {
            'requires_email': True, 'password': 'hunter2', 'allowed': ['a@lab.local, @Partner.com'],
            'blur_when_unfocused': True, 'allow_download': False}, format='json')
        assert response.status_code == 200, response.content
        body = response.json()
        assert body['has_password'] is True and 'password' not in body
        assert body['allowed_emails'] == ['a@lab.local'] and body['allowed_domains'] == ['partner.com']
        assert not ShareLink.objects.filter(document=document).exists()

    def test_the_password_is_write_only_blank_keeps_it_and_it_can_be_cleared(self, api_client, document):
        api_client.put(policy_url('document', document.id), {'password': 'one'}, format='json')
        api_client.put(policy_url('document', document.id), {'password': ''}, format='json')
        assert AccessPolicy.objects.get().password == 'one'
        api_client.put(policy_url('document', document.id), {'clear_password': True}, format='json')
        assert not AccessPolicy.objects.get().password

    def test_an_nda_needs_its_text_and_changing_the_text_bumps_the_version(self, api_client, document):
        assert api_client.put(policy_url('document', document.id), {'require_nda': True}, format='json').status_code == 400
        api_client.put(policy_url('document', document.id), {'require_nda': True, 'nda_text': 'v1'}, format='json')
        api_client.put(policy_url('document', document.id), {'nda_text': 'v2'}, format='json')
        assert AccessPolicy.objects.get().nda_version == 2

    def test_bad_entries_are_refused(self, api_client, document):
        response = api_client.put(policy_url('document', document.id), {'allowed': ['ok@lab.local', '*.evil.test']}, format='json')
        assert response.status_code == 400 and not AccessPolicy.objects.exists()

    def test_verifying_an_email_implies_asking_for_it(self, api_client, document):
        api_client.put(policy_url('document', document.id), {'requires_email_verification': True}, format='json')
        assert AccessPolicy.objects.get().requires_email is True

    def test_a_datastore_file_can_be_addressed_by_its_datastore_entry(self, api_client, dataroom, document):
        dd = DataroomDocument.objects.create(dataroom=dataroom, document=document, name='X.pdf')
        api_client.put(policy_url('dataroom-document', dd.id), {'enable_watermark': True}, format='json')
        assert AccessPolicy.objects.get().document == document

    def test_a_datastore_has_its_own_policy(self, api_client, dataroom):
        api_client.put(policy_url('dataroom', dataroom.id), {'requires_email': True}, format='json')
        assert AccessPolicy.objects.get().dataroom == dataroom

    def test_other_people_cannot_read_or_change_it(self, document, user2, public_client):
        other = APIClient()
        other.force_authenticate(user2)
        assert other.get(policy_url('document', document.id)).status_code == 404
        assert other.put(policy_url('document', document.id), {'requires_email': True}, format='json').status_code == 404
        assert public_client.get(policy_url('document', document.id)).status_code in (401, 403)
        assert not AccessPolicy.objects.exists()

    def test_unknown_kinds_are_not_found(self, api_client, document):
        assert api_client.get(policy_url('folder', document.id)).status_code == 404


class TestLinksInheritThePolicy:
    def test_a_link_with_no_settings_of_its_own_is_open_without_a_policy(self, public_client, share_link):
        assert view(public_client, share_link).status_code == 200

    def test_a_password_set_on_the_document_protects_every_link_to_it(self, api_client, public_client, share_link, document):
        api_client.put(policy_url('document', document.id), {'password': 'hunter2'}, format='json')
        response = view(public_client, share_link)
        assert response.status_code == 401 and response.json()['protectionType'] == 'password'
        wrong = public_client.post(f'/api/v1/links/{share_link.slug}/verify-password/', {'password': 'nope'}, format='json')
        assert wrong.status_code in (400, 401, 403)
        ok = public_client.post(f'/api/v1/links/{share_link.slug}/verify-password/', {'password': 'hunter2'}, format='json')
        assert ok.status_code == 200
        assert view(public_client, share_link).status_code == 200

    def test_a_link_created_later_is_covered_too(self, api_client, public_client, document, user):
        api_client.put(policy_url('document', document.id), {'password': 'x'}, format='json')
        later = ShareLink.objects.create(document=document, created_by=user, name='Later')
        assert view(public_client, later).status_code == 401

    def test_the_public_meta_reflects_the_policy(self, api_client, public_client, share_link, document):
        api_client.put(policy_url('document', document.id), {'password': 'x', 'requires_email': True}, format='json')
        meta = public_client.get(f'/api/v1/links/{share_link.slug}/').json()
        assert meta['has_password'] is True and meta['requires_email'] is True and meta['is_restricted'] is True

    def test_an_nda_set_on_the_document_is_required_on_the_link(self, api_client, public_client, share_link, document):
        api_client.put(policy_url('document', document.id), {'require_nda': True, 'nda_text': 'Secret.'}, format='json')
        body = view(public_client, share_link).json()
        assert body['protectionType'] == 'nda' and body['nda_text'] == 'Secret.'

    def test_allowed_addresses_on_the_document_gate_the_link_by_emailed_code(self, api_client, public_client, share_link, document):
        api_client.put(policy_url('document', document.id), {'allowed': ['guest@lab.local']}, format='json')
        assert view(public_client, share_link).json()['protectionType'] == 'allowlist'
        mail.outbox.clear()
        public_client.post(f'/api/v1/links/{share_link.slug}/access/request/', {'email': 'stranger@lab.local'}, format='json')
        assert mail.outbox == []
        public_client.post(f'/api/v1/links/{share_link.slug}/access/request/', {'email': 'guest@lab.local'}, format='json')
        code = re.search(r'\b([A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4})\b', mail.outbox[-1].body).group(1)
        ok = public_client.post(f'/api/v1/links/{share_link.slug}/access/verify/', {'email': 'guest@lab.local', 'code': code}, format='json')
        assert ok.status_code == 200 and view(public_client, share_link).status_code == 200

    def test_a_domain_rule_on_the_document_works_and_removing_it_cuts_sessions_off(self, api_client, public_client, share_link, document):
        api_client.put(policy_url('document', document.id), {'allowed': ['@lab.local']}, format='json')
        mail.outbox.clear()
        public_client.post(f'/api/v1/links/{share_link.slug}/access/request/', {'email': 'anyone@lab.local'}, format='json')
        code = re.search(r'\b([A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4})\b', mail.outbox[-1].body).group(1)
        public_client.post(f'/api/v1/links/{share_link.slug}/access/verify/', {'email': 'anyone@lab.local', 'code': code}, format='json')
        assert view(public_client, share_link).status_code == 200
        api_client.put(policy_url('document', document.id), {'allowed': ['someone.else@other.test']}, format='json')
        assert view(public_client, share_link).status_code == 401

    def test_protections_set_on_the_document_reach_the_viewer(self, api_client, public_client, share_link, document):
        api_client.put(policy_url('document', document.id), {'restricted_viewing_pane': True, 'blur_when_unfocused': True,
                                                             'enable_watermark': True, 'watermark_text': 'MINE {{email}}'}, format='json')
        settings = view(public_client, share_link).json()['link_settings']
        assert settings['restricted_viewing_pane'] is True and settings['blur_when_unfocused'] is True
        assert settings['enable_watermark'] is True

    def test_a_document_can_forbid_downloads_on_every_link(self, api_client, public_client, share_link, document):
        assert view(public_client, share_link).json()['link_settings']['allow_download'] is True
        api_client.put(policy_url('document', document.id), {'allow_download': False}, format='json')
        assert view(public_client, share_link).json()['link_settings']['allow_download'] is False

    def test_the_stricter_of_link_and_document_wins(self, api_client, public_client, share_link, document):
        share_link.requires_email = True
        share_link.save()
        api_client.put(policy_url('document', document.id), {'password': 'x'}, format='json')
        response = view(public_client, share_link)
        assert response.status_code == 401 and response.json()['protectionType'] in ('email', 'password')

    def test_other_documents_are_not_affected(self, api_client, public_client, share_link, document, user, organization):
        from documents.models import Document, DocumentVersion
        other = Document.objects.create(name='Other.pdf', organization=organization, created_by=user, status='ready')
        DocumentVersion.objects.create(document=other, version_number=1, is_primary=True)
        other_link = ShareLink.objects.create(document=other, created_by=user)
        api_client.put(policy_url('document', document.id), {'password': 'x'}, format='json')
        assert view(public_client, other_link).status_code == 200

    def test_a_datastore_policy_covers_its_links(self, api_client, public_client, dataroom, user):
        api_client.put(policy_url('dataroom', dataroom.id), {'password': 'x'}, format='json')
        link = ShareLink.objects.create(dataroom=dataroom, created_by=user)
        assert view(public_client, link).status_code == 401

    def test_the_owner_can_still_preview_and_the_link_row_is_never_rewritten(self, api_client, share_link, document):
        from sharelinks.policy import effective_link
        api_client.put(policy_url('document', document.id), {'password': 'x', 'requires_email': True}, format='json')
        eff = effective_link(ShareLink.objects.get(pk=share_link.pk))
        assert eff.requires_email is True and eff.password == 'x'
        with pytest.raises(RuntimeError):
            eff.save()
        share_link.refresh_from_db()
        assert share_link.requires_email is False and not share_link.password
