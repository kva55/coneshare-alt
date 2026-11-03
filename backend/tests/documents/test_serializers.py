import pytest
from unittest.mock import patch
from django.utils import timezone
from django.test import override_settings
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Organization
from documents.models import Document, Folder
from documents.serializers import (
    DocumentSerializer, FolderSerializer,
    DocumentVersionSerializer, DocumentVersionListSerializer
)
from sharelinks.models import ShareLink, ViewSession

pytestmark = pytest.mark.django_db


@pytest.fixture
def serializer_context(user):
    """Creates a mock request context for serializers."""
    factory = APIRequestFactory()
    request = factory.post("/")  # Method doesn't matter for this context
    force_authenticate(request, user=user)
    return {"request": Request(request)}




class TestFolderSerializer:
    def test_create_with_duplicate_name_is_renamed(self, user, organization, serializer_context):
        """
        Test that creating a folder with a duplicate name in the same parent
        results in an appended counter.
        """
        root = Folder.objects.get(organization=organization, name="__root__")

        # Create first folder
        Folder.objects.create(
            organization=organization, name="My Test Folder", parent=root, created_by=user
        )

        # Create second folder with the same name
        serializer = FolderSerializer(
            data={"name": "My Test Folder"},
            context=serializer_context,
        )
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save(organization=user.organization, created_by=user)
        assert instance.name == "My Test Folder (2)"

        # Create a third, which should become (3)
        serializer_3 = FolderSerializer(
            data={"name": "My Test Folder"},
            context=serializer_context,
        )
        assert serializer_3.is_valid(), serializer_3.errors
        instance_3 = serializer_3.save(organization=user.organization, created_by=user)
        assert instance_3.name == "My Test Folder (3)"

    def test_update_with_duplicate_name_fails(self, user, organization, serializer_context):
        """
        Test that renaming a folder to an existing name fails validation.
        """
        root = Folder.objects.get(organization=organization, name="__root__")
        folder1 = Folder.objects.create(
            organization=organization, name="Folder 1", parent=root, created_by=user
        )
        Folder.objects.create(
            organization=organization, name="Folder 2", parent=root, created_by=user
        )

        serializer = FolderSerializer(
            instance=folder1,
            data={"name": "Folder 2"},
            context=serializer_context,
            partial=True
        )
        assert not serializer.is_valid()
        assert 'name' in serializer.errors

    @pytest.mark.parametrize("reserved_name", ["__root__", "__datarooms__"])
    def test_cannot_create_or_rename_to_reserved_names(self, user, organization, serializer_context, reserved_name):
        """Test that attempting to create or rename a folder to reserved system names is blocked."""
        serializer = FolderSerializer(
            data={"name": reserved_name},
            context=serializer_context,
        )
        assert not serializer.is_valid()
        assert 'name' in serializer.errors
        assert "reserved" in str(serializer.errors['name'][0])

    def test_get_ancestors(self, user, organization):
        root = Folder.objects.get(organization=organization, name="__root__")
        folder1 = Folder.objects.create(
            organization=organization, name="Folder 1", parent=root, created_by=user
        )
        folder2 = Folder.objects.create(
            organization=organization, name="Folder 2", parent=folder1, created_by=user
        )
        folder3 = Folder.objects.create(
            organization=organization, name="Folder 3", parent=folder2, created_by=user
        )

        serializer = FolderSerializer(instance=folder3)
        ancestors = serializer.data["ancestors"]

        assert len(ancestors) == 2
        assert ancestors[0]["id"] == str(folder1.id)
        assert ancestors[0]["name"] == "Folder 1"
        assert ancestors[1]["id"] == str(folder2.id)
        assert ancestors[1]["name"] == "Folder 2"

    def test_get_ancestors_for_root_level_folder(self, user, organization):
        root = Folder.objects.get(organization=organization, name="__root__")
        folder1 = Folder.objects.create(
            organization=organization, name="Folder 1", parent=root, created_by=user
        )

        serializer = FolderSerializer(instance=folder1)
        ancestors = serializer.data["ancestors"]

        assert len(ancestors) == 0


class TestDocumentSerializer:
    def test_folder_is_nested_on_read_succeeds_without_prefetch(self, user, organization, serializer_context):
        """
        Tests that the serializer correctly handles a Document whose 'folder'
        relation has not been prefetched, preventing an AttributeError.
        """
        root = Folder.objects.get(organization=organization, name="__root__")
        parent_folder = Folder.objects.create(
            organization=organization, name="Parent", parent=root, created_by=user
        )
        doc = Document.objects.create(
            organization=organization,
            name="My Doc",
            folder=parent_folder,
            created_by=user,
            file_size=12345,
        )

        # Fetch the document instance *without* prefetching the folder relation.
        doc_from_db = Document.objects.get(pk=doc.pk)
        serializer = DocumentSerializer(instance=doc_from_db, context=serializer_context)

        # Accessing .data should now succeed without raising an error.
        data = serializer.data

        assert data['folder'] is not None
        assert data['folder']['id'] == str(parent_folder.id)
        assert data['folder']['name'] == "Parent"

    def test_share_link_view_count_falls_back_for_unannotated_document(self, user, organization, serializer_context):
        doc = Document.objects.create(
            organization=organization,
            name="Unannotated Doc",
            created_by=user,
        )
        link = ShareLink.objects.create(document=doc, created_by=user)
        ViewSession.objects.create(share_link=link)
        ViewSession.objects.create(share_link=link)

        serializer = DocumentSerializer(instance=doc, context=serializer_context)

        assert serializer.data['share_link_view_count'] == 2

    def test_share_link_view_count_fallback_queries_database(self, user, organization, serializer_context, django_assert_num_queries):
        doc = Document.objects.create(
            organization=organization,
            name="Fallback Count Doc",
            created_by=user,
        )
        link = ShareLink.objects.create(document=doc, created_by=user)
        ViewSession.objects.create(share_link=link)

        doc_from_db = Document.objects.get(pk=doc.pk)

        serializer = DocumentSerializer(instance=doc_from_db, context=serializer_context)
        with django_assert_num_queries(1):
            assert serializer.get_share_link_view_count(doc_from_db) == 1


class TestDocumentVersionSerializers:
    def test_version_serializers_hide_metadata(self, user, organization):
        from documents.models import DocumentVersion
        doc = Document.objects.create(
            organization=organization,
            name="Version Test.pdf",
            created_by=user,
        )
        v1 = DocumentVersion.objects.create(
            document=doc,
            version_number=1,
            is_primary=True,
            file_size=100,
            content_type="application/pdf",
            original_storage_key="v1.pdf",
            storage_key="v1.pdf",
            type="pdf",
        )

        for serializer_class in [DocumentVersionSerializer, DocumentVersionListSerializer]:
            data = serializer_class(instance=v1).data
            assert "metadata" not in data
            assert "cloud_import" not in data


class TestDocumentSerializerReadOnlyFields:
    """Tests for DocumentSerializer read-only field invariants."""

    def test_document_serializer_read_only_fields_ignore_tampering(self, user, organization, serializer_context):
        """Ensure system fields cannot be modified via document serializer."""
        doc = Document.objects.create(
            organization=organization,
            name="Safe Doc.pdf",
            created_by=user,
            file_size=5000,
            storage_key="orig/storage.pdf",
            original_storage_key="orig/storage.pdf",
            status="ready",
            type="pdf",
            num_pages=5,
        )

        serializer = DocumentSerializer(
            instance=doc,
            data={
                "file_size": 999999999,
                "storage_key": "custom/storage.pdf",
                "original_storage_key": "custom/storage.pdf",
                "status": "processing",
                "type": "video",
                "num_pages": 999,
            },
            partial=True,
            context=serializer_context,
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        doc.refresh_from_db()
        assert doc.file_size == 5000
        assert doc.storage_key == "orig/storage.pdf"
        assert doc.original_storage_key == "orig/storage.pdf"
        assert doc.status == "ready"
        assert doc.type == "pdf"
        assert doc.num_pages == 5

    def test_document_serializer_folder_is_read_only_and_ignored_on_write(self, user, user2, organization, serializer_context):
        """Folder is strictly read-only; attempts to change folder via DocumentSerializer are ignored."""
        root = Folder.objects.get_root_for_org(organization)
        user2_folder = Folder.objects.create(
            name="User2 Folder", organization=organization, created_by=user2, parent=root
        )
        doc = Document.objects.create(
            organization=organization, name="Doc.pdf", created_by=user, folder=root
        )

        serializer = DocumentSerializer(
            instance=doc,
            data={"folder": user2_folder.id},
            partial=True,
            context=serializer_context,
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        doc.refresh_from_db()
        # Destination folder was not mutated; remains root
        assert doc.folder == root


