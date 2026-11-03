from unittest.mock import patch, MagicMock
import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from datetime import timedelta
from django.utils import timezone

from documents.models import Document, DocumentVersion, DocumentPage, Folder
from documents.renderers import get_renderer
from documents.services import (
    create_document_from_upload,
    delete_document_and_files,
    copy_document,
    QuotaExceededError,
    check_user_quota_on_upload,
    touch_folder_ancestors,
    soft_delete_document,
    soft_delete_folder,
    restore_item,
    is_dataroom_vault_document,
    ensure_storage_key_not_claimed,
    safe_delete_storage_file,
    filter_unreferenced_storage_keys,
    recalculate_user_document_size,
)
from rest_framework import serializers
from core.services import get_dynamic_setting



@pytest.mark.django_db
class TestCreateDocumentFromUpload:
    @patch('documents.services.generate_pdf_pages_task.delay')
    def test_service_creates_records_and_dispatches_task(self, mock_task_delay, user):
        # Call the service function
        document = create_document_from_upload(
            requesting_user=user,
            folder=None,
            storage_key=f"{user.organization.id}/mock_path.pdf",
            unique_name="test.pdf",
            file_size=123,
            content_type="application/pdf"
        )

        # 1. Assert one Document object was created with correct fields
        assert Document.objects.count() == 1
        created_document = Document.objects.first()
        assert created_document == document
        assert created_document.status == 'ready'
        assert created_document.name == 'test.pdf'
        assert created_document.created_by == user

        # 2. Assert one DocumentVersion object was created correctly
        assert DocumentVersion.objects.count() == 1
        version = DocumentVersion.objects.first()
        assert version.document == created_document
        assert version.version_number == 1
        assert version.original_storage_key == f"{user.organization.id}/mock_path.pdf"
        assert version.file_size == 123
        assert version.render_status == DocumentVersion.RENDER_NOT_GENERATED
        assert version.has_pages is False

        # 3. Assert that preview rendering is deferred until first preview access.
        mock_task_delay.assert_not_called()


@pytest.mark.django_db
class TestEnqueueServerPreviewRender:
    @patch('documents.tasks.generate_pdf_pages_task.delay')
    def test_enqueue_updates_in_memory_render_state(self, mock_task_delay, user):
        document = Document.objects.create(
            organization=user.organization,
            created_by=user,
            name="queued.pdf",
            status='ready',
            type='pdf',
            content_type='application/pdf',
            download_only=False,
        )
        version = DocumentVersion.objects.create(
            document=document,
            version_number=1,
            original_storage_key="queued.pdf",
            storage_key="queued.pdf",
            type='pdf',
            is_primary=True,
            render_status=DocumentVersion.RENDER_NOT_GENERATED,
            render_error="stale error",
        )

        with override_settings():
            render_status = get_renderer(version).enqueue_render_task(version)
     
            assert render_status == DocumentVersion.RENDER_QUEUED
            assert version.render_status == DocumentVersion.RENDER_QUEUED
            assert version.render_error == ''
            mock_task_delay.assert_called_once_with(version.id)

    def test_enqueue_refreshes_render_error_when_concurrent_update_wins(self, user):
        document = Document.objects.create(
            organization=user.organization,
            created_by=user,
            name="failed.pdf",
            status='ready',
            type='pdf',
            content_type='application/pdf',
            download_only=False,
        )
        version = DocumentVersion.objects.create(
            document=document,
            version_number=1,
            original_storage_key="failed.pdf",
            storage_key="failed.pdf",
            type='pdf',
            is_primary=True,
            render_status=DocumentVersion.RENDER_NOT_GENERATED,
            render_error='',
        )

        DocumentVersion.objects.filter(pk=version.pk).update(
            render_status=DocumentVersion.RENDER_FAILED,
            render_error="Conversion failed.",
        )

        with override_settings():
            render_status = get_renderer(version).enqueue_render_task(version)
     
            assert render_status == DocumentVersion.RENDER_FAILED
            assert version.render_status == DocumentVersion.RENDER_FAILED
            assert version.render_error == "Conversion failed."

    @patch('documents.tasks.generate_pdf_pages_task.delay')
    def test_enqueue_re_evaluates_not_applicable_to_queued(self, mock_task_delay, user):
        document = Document.objects.create(
            organization=user.organization,
            created_by=user,
            name="queued.pdf",
            status='ready',
            type='pdf',
            content_type='application/pdf',
            download_only=False,
        )
        version = DocumentVersion.objects.create(
            document=document,
            version_number=1,
            original_storage_key="queued.pdf",
            storage_key="queued.pdf",
            type='pdf',
            is_primary=True,
            render_status=DocumentVersion.RENDER_NOT_APPLICABLE,
            render_error='',
        )

        with override_settings():
            render_status = get_renderer(version).enqueue_render_task(version)
            
            assert render_status == DocumentVersion.RENDER_QUEUED
            assert version.render_status == DocumentVersion.RENDER_QUEUED
            mock_task_delay.assert_called_once_with(version.id)




@pytest.mark.django_db
@patch('documents.services.fileserver_client.copy_file')
@patch('documents.services._route_document_for_processing')
class TestCopyDocumentService:

    def test_copy_document_success(self, mock_route_for_processing, mock_copy_file, user, document):
        """Test that copy_document service successfully creates a copy."""
        # Arrange
        original_doc = document
        original_doc.file_size = 1000
        original_doc.save()
        user.total_document_size = 1000
        user.save()

        # Act
        new_doc = copy_document(original_doc, user)

        # Assert
        assert Document.objects.count() == 2
        assert DocumentVersion.objects.count() == 2

        assert new_doc.id != original_doc.id
        assert new_doc.name == "Test Document (2).pdf"
        assert new_doc.created_by == user
        assert new_doc.file_size == original_doc.file_size

        # Check fileserver was called
        mock_copy_file.assert_called_once()

        # Check user quota was updated
        user.refresh_from_db()
        assert user.total_document_size == 2000

        # Check processing was triggered
        mock_route_for_processing.assert_called_once()

    @patch('core.services.get_dynamic_setting', return_value=1)
    def test_copy_document_respects_quota(self, mock_get_setting, mock_route_for_processing, mock_copy_file, user, document):
        """Test that copy_document fails if user quota is exceeded."""
        # Arrange
        original_doc = document
        original_doc.file_size = 2 * 1024 * 1024  # 2MB
        original_doc.save()

        user.total_document_size = 0
        user.save()

        with override_settings(FILE_SIZE_QUOTA_MB=1):
            with pytest.raises(QuotaExceededError):
                copy_document(original_doc, user)

        assert Document.objects.count() == 1  # No copy was created
        mock_copy_file.assert_not_called()
        user.refresh_from_db()
        assert user.total_document_size == 0  # User size wasn't updated

    def test_copy_document_removes_uploader_info(self, mock_route_for_processing, mock_copy_file, user, document):
        """Test that uploader_info is removed from metadata on copy, but other metadata is kept."""
        # Arrange
        original_doc = document
        original_doc.file_size = 1000
        original_doc.metadata = {'uploader_info': {'name': 'test', 'email': 'test@test.com'}, 'other_key': 'value'}
        original_doc.save()

        # Act
        new_doc = copy_document(original_doc, user)

        # Assert
        assert 'uploader_info' in original_doc.metadata
        assert 'uploader_info' not in new_doc.metadata
        assert 'other_key' in new_doc.metadata
        assert new_doc.metadata['other_key'] == 'value'



@pytest.mark.django_db
class TestPreviewModeServices:

    @patch('documents.tasks.generate_pdf_pages_task.delay')
    @patch('core.services.get_dynamic_setting')
    @patch('documents.models.get_dynamic_setting')
    def test_get_effective_render_status_re_evaluates_not_applicable_pdf(self, mock_models_setting, mock_core_setting, mock_task_delay, user):
        def side_effect(key, default=None):
            if key == 'MAX_PREVIEW_FILE_SIZE_MB':
                return 100
            return default
        mock_models_setting.side_effect = side_effect
        mock_core_setting.side_effect = side_effect

        doc = Document.objects.create(
            organization=user.organization,
            created_by=user,
            type='pdf',
            file_size=200 * 1024 * 1024, # 200MB
            download_only=True,
        )
        version = DocumentVersion.objects.create(
            document=doc,
            version_number=1,
            type='pdf',
            is_primary=True,
            file_size=200 * 1024 * 1024,
            render_status=DocumentVersion.RENDER_NOT_APPLICABLE,
        )

        with override_settings():
            assert get_renderer(version).get_effective_render_status(version) == DocumentVersion.RENDER_NOT_APPLICABLE

        # Increase limit to 300MB
        def side_effect_large(key, default=None):
            if key == 'MAX_PREVIEW_FILE_SIZE_MB':
                return 300
            return default
        mock_models_setting.side_effect = side_effect_large
        mock_core_setting.side_effect = side_effect_large

        with override_settings():
            assert get_renderer(version).enqueue_render_task(version) == DocumentVersion.RENDER_QUEUED
            assert version.render_status == DocumentVersion.RENDER_QUEUED
            mock_task_delay.assert_called_once_with(version.id)




@pytest.mark.django_db
class TestUserQuotaChecks:

    def test_check_user_quota_on_upload_respects_custom_quota(self, user):
        """Test that check_user_quota_on_upload respects custom user quota if set, else falls back to global."""
        user.total_document_size = 50 * 1024 * 1024  # 50MB
        user.save()

        # 1. Custom quota set to 40MB (exceeded)
        user.custom_file_size_quota_mb = 40
        user.save()
        with pytest.raises(QuotaExceededError, match="Uploading this file would exceed your storage quota of 40 MB"):
            check_user_quota_on_upload(user, 10)

        # 2. Custom quota set to 60MB (allowed)
        user.custom_file_size_quota_mb = 60
        user.save()
        check_user_quota_on_upload(user, 10 * 1024 * 1024)  # 10MB upload

        # 3. Custom quota set to None (fallback to dynamic setting of e.g. 100MB)
        user.custom_file_size_quota_mb = None
        user.save()
        with patch('core.services.get_dynamic_setting', return_value=100):
            check_user_quota_on_upload(user, 10 * 1024 * 1024)  # 10MB upload allowed under 100MB fallback

        # 4. Custom quota set to None, but fallback exceeded
        user.custom_file_size_quota_mb = None
        user.save()
        with patch('core.services.get_dynamic_setting', return_value=45):
            with pytest.raises(QuotaExceededError, match="Uploading this file would exceed your storage quota of 45 MB"):
                check_user_quota_on_upload(user, 10)


@pytest.mark.django_db
class TestFolderMtimeUpdates:
    def test_touch_folder_ancestors_updates_tree(self, user):
        """Test that touch_folder_ancestors updates the timestamp of the folder and all its ancestors."""
        past_time = timezone.now() - timedelta(days=2)
        root = Folder.objects.create(name="Root", organization=user.organization, created_by=user)
        sub1 = Folder.objects.create(name="Sub1", parent=root, organization=user.organization, created_by=user)
        sub2 = Folder.objects.create(name="Sub2", parent=sub1, organization=user.organization, created_by=user)

        Folder.objects.filter(id__in=[root.id, sub1.id, sub2.id]).update(updated_at=past_time)
        root.refresh_from_db()
        sub1.refresh_from_db()
        sub2.refresh_from_db()
        assert root.updated_at == past_time
        assert sub1.updated_at == past_time
        assert sub2.updated_at == past_time

        touch_folder_ancestors(sub2)

        root.refresh_from_db()
        sub1.refresh_from_db()
        sub2.refresh_from_db()
        assert root.updated_at > past_time
        assert sub1.updated_at > past_time
        assert sub2.updated_at > past_time

    def test_create_document_touches_parent_folder(self, user):
        """Test that uploading/creating a document touches parent folder updated_at."""
        past_time = timezone.now() - timedelta(days=1)
        folder = Folder.objects.create(name="Folder", organization=user.organization, created_by=user)
        Folder.objects.filter(id=folder.id).update(updated_at=past_time)
        folder.refresh_from_db()
        assert folder.updated_at == past_time

        create_document_from_upload(
            requesting_user=user,
            folder=folder,
            storage_key=f"{user.organization.id}/test.pdf",
            unique_name="test.pdf",
            file_size=100,
            content_type="application/pdf"
        )

        folder.refresh_from_db()
        assert folder.updated_at > past_time

    def test_soft_delete_and_restore_document_touches_parent_folder(self, user):
        """Test that soft deleting and restoring a document touches parent folder updated_at."""
        past_time = timezone.now() - timedelta(days=1)
        folder = Folder.objects.create(name="Folder", organization=user.organization, created_by=user)
        doc = Document.objects.create(
            name="file.pdf",
            organization=user.organization,
            folder=folder,
            created_by=user,
            type="pdf",
            content_type="application/pdf"
        )
        Folder.objects.filter(id=folder.id).update(updated_at=past_time)
        folder.refresh_from_db()
        assert folder.updated_at == past_time

        # 1. Soft delete touches parent folder
        soft_delete_document(doc, user)
        folder.refresh_from_db()
        assert folder.updated_at > past_time

        # 2. Reset and restore touches parent folder
        Folder.objects.filter(id=folder.id).update(updated_at=past_time)
        folder.refresh_from_db()
        assert folder.updated_at == past_time

        restore_item(doc, 'document', user)
        folder.refresh_from_db()
        assert folder.updated_at > past_time

    def test_soft_delete_folder_touches_parent_folder(self, user):
        """Test that soft deleting a subfolder touches its parent folder updated_at."""
        past_time = timezone.now() - timedelta(days=1)
        parent = Folder.objects.create(name="Parent", organization=user.organization, created_by=user)
        child = Folder.objects.create(name="Child", parent=parent, organization=user.organization, created_by=user)
        Folder.objects.filter(id=parent.id).update(updated_at=past_time)
        parent.refresh_from_db()
        assert parent.updated_at == past_time

        soft_delete_folder(child, user)
        parent.refresh_from_db()
        assert parent.updated_at > past_time


    def test_touch_folder_ancestors_with_cycle_safely_terminates(self, user):
        """Test that touch_folder_ancestors does not infinite-loop if there is a cycle."""
        f1 = Folder.objects.create(name="F1", organization=user.organization, created_by=user)
        f2 = Folder.objects.create(name="F2", parent=f1, organization=user.organization, created_by=user)
        # Mock a cycle in memory
        f1.parent = f2
        touch_folder_ancestors(f2)  # Should terminate cleanly without hanging


class TestStorageKeyIsolationAndSafeDeletion:
    def test_ensure_storage_key_not_claimed_raises_on_existing_document(self, user):
        """Verifies that an existing document's storage key is rejected on re-registration."""
        key = f"{user.organization.id}/existing/file.pdf"
        Document.objects.create(
            name="Existing.pdf",
            organization=user.organization,
            created_by=user,
            original_storage_key=key,
            storage_key=key,
        )
        with pytest.raises(serializers.ValidationError) as exc:
            ensure_storage_key_not_claimed(key, user.organization.id)
        assert "This storage key has already been registered." in str(exc.value)

    def test_ensure_storage_key_not_claimed_raises_on_existing_version(self, user):
        """Verifies that an existing document version's storage key is rejected on re-registration."""
        key = f"{user.organization.id}/version/file.pdf"
        doc = Document.objects.create(
            name="Doc.pdf",
            organization=user.organization,
            created_by=user,
        )
        DocumentVersion.objects.create(
            document=doc,
            version_number=1,
            original_storage_key=key,
            storage_key=key,
        )
        with pytest.raises(serializers.ValidationError) as exc:
            ensure_storage_key_not_claimed(key, user.organization.id)
        assert "This storage key has already been registered." in str(exc.value)

    def test_ensure_storage_key_not_claimed_passes_for_fresh_key(self, user):
        """Verifies that an unclaimed storage key passes validation without errors."""
        fresh_key = f"{user.organization.id}/fresh/unclaimed.pdf"
        ensure_storage_key_not_claimed(fresh_key, user.organization.id)

    @patch('documents.services.fileserver_client.delete_file')
    def test_safe_delete_storage_file_deletes_when_no_other_references(self, mock_delete, user):
        """Verifies that physical deletion occurs when no other document records reference the key."""
        key = f"{user.organization.id}/single/doc.pdf"
        result = safe_delete_storage_file(key, user.organization.id)
        assert result is True
        mock_delete.assert_called_once_with(key)

    @patch('documents.services.fileserver_client.delete_file')
    def test_safe_delete_storage_file_skips_when_another_document_references_key(self, mock_delete, user):
        """Verifies that physical deletion is skipped when another document references the same storage key."""
        key = f"{user.organization.id}/shared/doc.pdf"
        Document.objects.create(
            name="Retained.pdf",
            organization=user.organization,
            created_by=user,
            original_storage_key=key,
            storage_key=key,
        )
        result = safe_delete_storage_file(key, user.organization.id)
        assert result is False
        mock_delete.assert_not_called()

    def test_ensure_storage_key_not_claimed_raises_on_soft_deleted_document(self, user):
        """Verifies that a soft-deleted document's storage key is still treated as claimed."""
        key = f"{user.organization.id}/soft_deleted/file.pdf"
        Document.objects.create(
            name="SoftDeleted.pdf",
            organization=user.organization,
            created_by=user,
            original_storage_key=key,
            storage_key=key,
            deleted_at=timezone.now(),
        )
        with pytest.raises(serializers.ValidationError) as exc:
            ensure_storage_key_not_claimed(key, user.organization.id)
        assert "This storage key has already been registered." in str(exc.value)

    def test_filter_unreferenced_storage_keys_batches_correctly(self, user):
        """Verifies that filter_unreferenced_storage_keys accurately filters out referenced keys."""
        k1 = f"{user.organization.id}/doc1.pdf"
        k2 = f"{user.organization.id}/doc2.pdf"
        k3 = f"{user.organization.id}/doc3.pdf"

        Document.objects.create(name="Doc1", organization=user.organization, created_by=user, original_storage_key=k1)
        Document.objects.create(name="Doc2", organization=user.organization, created_by=user, original_storage_key=k2)

        # k1 and k2 are referenced, k3 is unreferenced
        unreferenced = filter_unreferenced_storage_keys({k1, k2, k3}, user.organization.id)
        assert unreferenced == {k3}

        # If we exclude Doc1, k1 becomes unreferenced as well
        doc1 = Document.objects.get(name="Doc1")
        unreferenced_with_exclusion = filter_unreferenced_storage_keys(
            {k1, k2, k3}, user.organization.id, exclude_document_ids=[doc1.pk]
        )
        assert unreferenced_with_exclusion == {k1, k3}


@pytest.mark.django_db
class TestDataConsistencyBatch1:
    @patch('documents.services.safe_delete_storage_file')
    def test_quota_recalculation_includes_trashed_documents_and_no_double_subtract(self, mock_delete, user):
        """
        Bug 1: Recalculate quota must include trashed documents because they still
        consume storage until purged. Purging from trash must not subtract twice
        or make total_document_size negative.
        """
        doc = Document.objects.create(
            organization=user.organization,
            created_by=user,
            name="test.pdf",
            status="ready",
            file_size=1000,
        )
        user.total_document_size = 1000
        user.save()

        # 1. Soft-delete document into Trash
        soft_delete_document(doc, user)
        assert doc.deleted_at is not None
        user.refresh_from_db()
        assert user.total_document_size == 1000

        # 2. Recalculate quota while document is in Trash
        recalculated = recalculate_user_document_size(user)
        # MUST count trashed documents (still taking up physical disk space)
        assert recalculated == 1000
        user.refresh_from_db()
        assert user.total_document_size == 1000

        # 3. Permanently purge the document from Trash
        delete_document_and_files(doc)
        user.refresh_from_db()
        assert user.total_document_size == 0

    @patch('documents.services.safe_delete_storage_file')
    def test_delete_document_and_files_prevents_negative_quota(self, mock_delete, user):
        """
        Bug 1: Defensive guard ensures total_document_size never goes below 0.
        """
        doc = Document.objects.create(
            organization=user.organization,
            created_by=user,
            name="test.pdf",
            status="ready",
            file_size=5000,
        )
        user.total_document_size = 1000  # Smaller than file_size
        user.save()

        delete_document_and_files(doc)
        user.refresh_from_db()
        assert user.total_document_size == 0


@pytest.mark.django_db
class TestDeleteDocument:
    @patch('documents.services.fileserver_client.delete_file')
    def test_service_deletes_db_records_and_storage_files(self, mock_fs_delete, user):
        # Setup
        doc = Document.objects.create(organization=user.organization, created_by=user)
        version = DocumentVersion.objects.create(
            document=doc,
            version_number=1,
            original_storage_key="original.pdf"
        )
        page1 = DocumentPage.objects.create(
            document_version=version, page_number=1, storage_key="page_1.png"
        )
        page2 = DocumentPage.objects.create(
            document_version=version, page_number=2, storage_key="page_2.png"
        )
        
        doc_id = doc.id
        version_id = version.id
        page1_id = page1.id

        # Action
        delete_document_and_files(doc)

        # Assertions
        # 1. DB records are deleted
        assert not Document.objects.filter(id=doc_id).exists()
        assert not DocumentVersion.objects.filter(id=version_id).exists()
        assert not DocumentPage.objects.filter(id=page1_id).exists()

        # 2. Storage deletion was called for all files
        assert mock_fs_delete.call_count == 3
        mock_fs_delete.assert_any_call("original.pdf")
        mock_fs_delete.assert_any_call("page_1.png")
        mock_fs_delete.assert_any_call("page_2.png")
