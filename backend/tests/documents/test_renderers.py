from unittest.mock import MagicMock, patch
import pytest
from django.test import override_settings

from core.models import Organization, User
from documents.models import Document, DocumentPage, DocumentVersion
from documents.renderers import (
    GenericFileRenderer,
    PDFRenderer,
    get_renderer,
    get_renderer_for_file,
)


@pytest.fixture
def user(db):
    org = Organization.objects.create(name="Test Org")
    return User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="password123",
        organization=org,
    )


class TestRendererRegistryPriority:
    """Validates priority resolution order and security filtering in get_renderer_for_file."""


    def test_svg_strictly_excluded_from_direct_image_renderer(self):
        """SVG must fall back to GenericFileRenderer to prevent Stored XSS attacks."""
        renderer_by_mime = get_renderer_for_file("image/svg+xml", "logo.svg")
        assert isinstance(renderer_by_mime, GenericFileRenderer)

        renderer_by_ext = get_renderer_for_file("application/octet-stream", "logo.svg")
        assert isinstance(renderer_by_ext, GenericFileRenderer)

    def test_pdf_resolves_to_pdf_renderer(self):
        renderer = get_renderer_for_file("application/pdf", "document.pdf")
        assert isinstance(renderer, PDFRenderer)


    def test_unsupported_files_fallback_to_generic_renderer(self):
        for ct, fn in [
            ("application/zip", "archive.zip"),
            ("application/x-tar", "data.tar.gz"),
            ("application/octet-stream", "unknown.xyz"),
            ("text/html", "index.html"),
            ("text/markdown", "sample.html"),
            ("application/pdf", "sample.html"),
            ("text/markdown", "sample.htm"),
            ("image/svg+xml", "sample.html"),
        ]:
            renderer = get_renderer_for_file(ct, fn)
            assert isinstance(renderer, GenericFileRenderer), f"Failed for {fn} with {ct}"


@pytest.mark.django_db
class TestVersionResolutionAndOfficeDivergence:
    """Validates get_renderer(version) behavior including Office documents converted to PDF."""


    def test_version_resolution_pdf_with_benign_bin_storage_key(self, user):
        doc = Document.objects.create(name="report.pdf", type="pdf", created_by=user, organization=user.organization)
        ver = DocumentVersion.objects.create(
            document=doc, version_number=1, original_storage_key="report.bin", content_type="application/pdf"
        )
        assert isinstance(get_renderer(ver), PDFRenderer)

    def test_version_resolution_dangerous_storage_key_falls_back_to_generic(self, user):
        doc = Document.objects.create(name="readme.md", type="markdown", created_by=user, organization=user.organization)
        ver = DocumentVersion.objects.create(
            document=doc, version_number=1, original_storage_key="index.html", content_type="text/markdown"
        )
        assert isinstance(get_renderer(ver), GenericFileRenderer)


@pytest.mark.django_db
class TestPageStorageKeyResolution:
    """Validates get_page_storage_key behavior across renderers."""


    def test_pdf_paginated_storage_key(self, user):
        renderer = PDFRenderer()
        doc = Document.objects.create(name="doc.pdf", type="pdf", created_by=user, organization=user.organization)
        ver = DocumentVersion.objects.create(
            document=doc, version_number=1, original_storage_key="doc.pdf", is_primary=True
        )
        DocumentPage.objects.create(document_version=ver, page_number=1, storage_key="pages/doc_p1.png")
        DocumentPage.objects.create(document_version=ver, page_number=2, storage_key="pages/doc_p2.png")

        assert renderer.get_page_storage_key(ver, 1) == "pages/doc_p1.png"
        assert renderer.get_page_storage_key(ver, 2) == "pages/doc_p2.png"
        assert renderer.get_page_storage_key(ver, 3) is None


@pytest.mark.django_db
class TestDownloadUrlParity:
    """Validates get_download_url optimization and safety across renderers."""


    @patch('documents.fileserver.fileserver_client')
    def test_get_download_url_fallback_to_storage_key(self, mock_fs, user):
        mock_fs.generate_download_url.return_value = "http://fileserver/download/doc.pdf"

        renderer = PDFRenderer()
        doc = Document.objects.create(name="doc.pdf", type="pdf", created_by=user, organization=user.organization)
        ver = DocumentVersion.objects.create(
            document=doc,
            version_number=1,
            original_storage_key="",
            storage_key="doc.pdf",
            is_primary=True,
        )

        download_url = renderer.get_download_url(doc, ver)
        assert download_url == "http://fileserver/download/doc.pdf"
        mock_fs.generate_download_url.assert_called_once_with("doc.pdf", is_internal=False)



@pytest.mark.django_db
class TestInitializeMetadata:
    """Validates metadata initialization and database persistence across all renderers."""

    def test_generic_file_renderer_persists_document_and_version(self, user):
        doc = Document.objects.create(name="archive.zip", created_by=user, organization=user.organization)
        ver = DocumentVersion.objects.create(document=doc, version_number=1, is_primary=True)

        renderer = GenericFileRenderer()
        renderer.initialize_metadata(doc, ver, file_size=1024, content_type="application/zip")

        # Reload from DB to confirm persistence
        doc.refresh_from_db()
        ver.refresh_from_db()

        assert doc.download_only is True
        assert doc.type == 'file'
        assert doc.content_type == 'application/zip'
        assert doc.file_size == 1024
        assert doc.status == 'ready'

        assert ver.type == 'file'
        assert ver.has_pages is False
        assert ver.render_status == DocumentVersion.RENDER_NOT_APPLICABLE


    def test_pdf_renderer_persists_metadata(self, user):
        doc = Document.objects.create(name="manual.pdf", created_by=user, organization=user.organization)
        ver = DocumentVersion.objects.create(document=doc, version_number=1, is_primary=True)

        renderer = PDFRenderer()
        renderer.initialize_metadata(doc, ver, file_size=8192, content_type="application/pdf")

        doc.refresh_from_db()
        ver.refresh_from_db()

        assert doc.download_only is False
        assert doc.type == 'pdf'
        assert ver.type == 'pdf'
        assert ver.has_pages is False
        assert ver.render_status == DocumentVersion.RENDER_NOT_GENERATED


@pytest.mark.django_db
class TestEnqueueRenderTaskNonServerRenderable:
    """Validates that non-server-renderable files (direct images, generic files) do not corrupt DB state."""


    def test_generic_file_enqueue_returns_not_applicable_without_db_change(self, user):
        doc = Document.objects.create(name="archive.zip", created_by=user, organization=user.organization)
        ver = DocumentVersion.objects.create(document=doc, version_number=1, is_primary=True)

        renderer = GenericFileRenderer()
        renderer.initialize_metadata(doc, ver, file_size=1024, content_type="application/zip")

        ver.refresh_from_db()
        assert ver.render_status == DocumentVersion.RENDER_NOT_APPLICABLE

        result = renderer.enqueue_render_task(ver)
        assert result == DocumentVersion.RENDER_NOT_APPLICABLE

        ver.refresh_from_db()
        assert ver.render_status == DocumentVersion.RENDER_NOT_APPLICABLE



class TestDispatchTaskContract:
    """Validates the _dispatch_task template method semantics."""


    def test_subclass_can_instantiate_without_dispatch_task_but_enqueue_fails_fast(self, user):
        from documents.renderers.base import BasePreviewRenderer

        class IncompleteAsyncRenderer(BasePreviewRenderer):
            @classmethod
            def can_handle_file(cls, content_type: str, filename: str) -> bool:
                return True

            @classmethod
            def can_handle_version(cls, version: DocumentVersion) -> bool:
                return True

            def initialize_metadata(self, document: Document, version: DocumentVersion, file_size: int, content_type: str):
                pass

            def get_preview_mode(self, version: DocumentVersion) -> str:
                return 'server_pages'

        # Instantiation succeeds because _dispatch_task is no longer an @abstractmethod
        incomplete_renderer = IncompleteAsyncRenderer()

        doc = Document.objects.create(name="dummy.txt", created_by=user, organization=user.organization)
        ver = DocumentVersion.objects.create(
            document=doc,
            version_number=1,
            render_status=DocumentVersion.RENDER_NOT_GENERATED,
            is_primary=True,
        )

        # But enqueuing fails fast instead of silently hanging in RENDER_QUEUED
        with pytest.raises(NotImplementedError) as exc_info:
            incomplete_renderer.enqueue_render_task(ver)
        assert "IncompleteAsyncRenderer inherits the default enqueue_render_task but does not implement _dispatch_task" in str(exc_info.value)


