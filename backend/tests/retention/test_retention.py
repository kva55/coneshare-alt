from datetime import datetime, timedelta, timezone as tz
from unittest.mock import patch

import pytest
from django.utils import timezone

from documents.models import Document
from retention import services
from retention.models import RetentionLog, RetentionSettings

pytestmark = pytest.mark.django_db
URL = '/api/v1/retention/'


def at(y, m, d):
    return datetime(y, m, d, 12, 0, tzinfo=tz.utc)


def make_doc(user, organization, name, created=None, delete_after=None):
    doc = Document.objects.create(name=name, organization=organization, created_by=user, status='ready', delete_after=delete_after)
    if created:
        Document.objects.filter(pk=doc.pk).update(created_at=created)
    return doc


class TestCutoff:
    def test_off(self):
        assert services.policy_cutoff(RetentionSettings(mode='off'), at(2026, 6, 1)) is None

    @pytest.mark.parametrize('value,unit,expected', [
        (1, 'weeks', at(2026, 5, 25)), (1, 'months', at(2026, 5, 1)), (1, 'years', at(2025, 6, 1)), (10, 'days', at(2026, 5, 22)),
    ])
    def test_after_upload(self, value, unit, expected):
        cfg = RetentionSettings(mode='after_upload', after_value=value, after_unit=unit)
        assert services.policy_cutoff(cfg, at(2026, 6, 1)) == expected

    def test_month_end_is_clamped(self):
        cfg = RetentionSettings(mode='after_upload', after_value=1, after_unit='months')
        assert services.policy_cutoff(cfg, at(2026, 3, 31)) == at(2026, 2, 28)

    def test_yearly_uses_the_most_recent_occurrence(self):
        cfg = RetentionSettings(mode='yearly', yearly_month=1, yearly_day=1)
        assert services.policy_cutoff(cfg, at(2026, 6, 1)).date().isoformat() == '2026-01-01'
        cfg = RetentionSettings(mode='yearly', yearly_month=12, yearly_day=31)
        assert services.policy_cutoff(cfg, at(2026, 6, 1)).date().isoformat() == '2025-12-31'


class TestDue:
    def test_per_file_date_beats_the_policy(self, user, organization):
        now = at(2026, 6, 1)
        cfg = RetentionSettings(mode='after_upload', after_value=1, after_unit='weeks')
        old_with_later_date = make_doc(user, organization, 'keep', at(2026, 1, 1), delete_after=now + timedelta(days=30))
        old_plain = make_doc(user, organization, 'old', at(2026, 1, 1))
        fresh = make_doc(user, organization, 'fresh', now - timedelta(days=1))
        past = make_doc(user, organization, 'past', now - timedelta(days=1), delete_after=now - timedelta(hours=1))
        due = {d.name: reason for d, reason in services.due_documents(now, cfg)}
        assert due == {'old': 'policy_after_upload', 'past': 'file_date'}
        assert old_with_later_date.name not in due and fresh.name not in due

    def test_nothing_is_due_with_no_policy_and_no_dates(self, user, organization):
        make_doc(user, organization, 'a', at(2020, 1, 1))
        assert services.due_documents(at(2026, 6, 1), RetentionSettings(mode='off')) == []

    def test_yearly_deletes_what_was_uploaded_before_the_date(self, user, organization):
        make_doc(user, organization, 'before', at(2025, 12, 30))
        make_doc(user, organization, 'after', at(2026, 1, 2))
        cfg = RetentionSettings(mode='yearly', yearly_month=1, yearly_day=1)
        assert [d.name for d, _ in services.due_documents(at(2026, 6, 1), cfg)] == ['before']

    def test_trashed_documents_are_included(self, user, organization):
        doc = make_doc(user, organization, 'trash', at(2020, 1, 1))
        Document.objects.filter(pk=doc.pk).update(deleted_at=timezone.now())
        cfg = RetentionSettings(mode='after_upload', after_value=1, after_unit='days')
        assert [d.name for d, _ in services.due_documents(at(2026, 6, 1), cfg)] == ['trash']


class TestRun:
    def test_hard_deletes_with_overwrite_and_logs_without_content(self, user, organization):
        RetentionSettings.objects.create(mode='after_upload', after_value=1, after_unit='days', overwrite=True)
        doc = make_doc(user, organization, 'Secret plan.pdf', at(2020, 1, 1))
        with patch('documents.services.fileserver_client') as fs:
            deleted, failed = services.run()
        assert (deleted, failed) == (1, 0)
        assert not Document.objects.filter(pk=doc.pk).exists()
        log = RetentionLog.objects.get()
        assert log.document_name == 'Secret plan.pdf' and log.overwritten and log.ok
        assert fs.delete_file.call_count >= 0

    def test_secure_flag_reaches_the_file_server(self, user, organization):
        from documents.models import DocumentVersion
        RetentionSettings.objects.create(mode='after_upload', after_value=1, after_unit='days', overwrite=True)
        doc = make_doc(user, organization, 'a.pdf', at(2020, 1, 1))
        DocumentVersion.objects.create(document=doc, version_number=1, is_primary=True,
                                       original_storage_key='o/a.pdf', storage_key='o/a.pdf')
        with patch('documents.services.fileserver_client') as fs:
            services.run()
        fs.delete_file.assert_called_with('o/a.pdf', secure=True)

    def test_a_failed_delete_keeps_the_document_and_is_logged(self, user, organization):
        from documents.models import DocumentVersion
        from rest_framework.exceptions import APIException
        RetentionSettings.objects.create(mode='after_upload', after_value=1, after_unit='days')
        doc = make_doc(user, organization, 'a.pdf', at(2020, 1, 1))
        DocumentVersion.objects.create(document=doc, version_number=1, is_primary=True, original_storage_key='o/a.pdf', storage_key='o/a.pdf')
        with patch('documents.services.fileserver_client') as fs:
            fs.delete_file.side_effect = APIException('down')
            deleted, failed = services.run()
        assert (deleted, failed) == (0, 1)
        assert Document.objects.filter(pk=doc.pk).exists()
        assert RetentionLog.objects.get().ok is False


class TestApi:
    def test_admin_only(self, api_client):
        assert api_client.get(URL + 'settings/').status_code == 403

    def test_save_and_validate(self, admin_api_client):
        r = admin_api_client.put(URL + 'settings/', {'mode': 'yearly', 'yearly_month': 2, 'yearly_day': 29}, format='json')
        assert r.status_code == 400
        r = admin_api_client.put(URL + 'settings/', {'mode': 'yearly', 'yearly_month': 1, 'yearly_day': 15}, format='json')
        assert r.status_code == 200 and r.json()['settings']['mode'] == 'yearly'

    def test_default_is_off_with_overwrite_on(self, admin_api_client):
        s = admin_api_client.get(URL + 'settings/').json()['settings']
        assert s['mode'] == 'off' and s['overwrite'] is True

    def test_per_file_date_must_be_in_the_future(self, api_client, document):
        past = (timezone.now() - timedelta(days=1)).isoformat()
        assert api_client.patch(f'/api/v1/documents/{document.id}/', {'delete_after': past}, format='json').status_code == 400
        future = (timezone.now() + timedelta(days=5)).isoformat()
        r = api_client.patch(f'/api/v1/documents/{document.id}/', {'delete_after': future}, format='json')
        assert r.status_code == 200, r.content
