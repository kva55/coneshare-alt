import logging
from abc import ABC, abstractmethod
from typing import Optional

from django.utils import timezone
from rest_framework.exceptions import APIException

from core import services as core_services
from documents import fileserver
from documents.models import Document, DocumentVersion

logger = logging.getLogger(__name__)



class BasePreviewRenderer(ABC):
    """
    Abstract base strategy for preview rendering, metadata initialization,
    task enqueuing, and page URL construction.

    All subclasses MUST remain strictly stateless:
    no mutable attributes may be stored on `self` across requests to guarantee
    thread safety under multi-threaded WSGI/ASGI workers.
    """

    @classmethod
    @abstractmethod
    def can_handle_file(cls, content_type: str, filename: str) -> bool:
        """Determines if this renderer can handle the file at initial upload."""
        pass

    @classmethod
    @abstractmethod
    def can_handle_version(cls, version: DocumentVersion) -> bool:
        """Determines if this renderer handles the given DocumentVersion."""
        pass

    @abstractmethod
    def initialize_metadata(
        self, document: Document, version: DocumentVersion, file_size: int, content_type: str
    ):
        """Initializes document and version metadata and preview states upon upload."""
        pass

    def is_dynamically_previewable(self, version: DocumentVersion) -> bool:
        """
        Evaluates whether the version is previewable against dynamic system settings
        (e.g., file size limits).
        """
        max_preview_size = core_services.get_dynamic_setting('MAX_PREVIEW_FILE_SIZE_MB')
        file_size = version.file_size or (version.document.file_size if version.document else 0)
        if file_size and file_size > (max_preview_size * 1024 * 1024):
            return False
        return True

    def get_effective_render_status(self, version: DocumentVersion) -> str:
        """
        Returns the effective render status, mapping dynamically ineligible files
        to RENDER_NOT_APPLICABLE.
        """
        if version.has_pages:
            return DocumentVersion.RENDER_READY
        if not self.is_dynamically_previewable(version):
            return DocumentVersion.RENDER_NOT_APPLICABLE
        return version.render_status

    @abstractmethod
    def get_preview_mode(self, version: DocumentVersion) -> str:
        """Returns the viewer mode string ('server_pages' or 'download_only')."""
        pass

    def enqueue_render_task(self, version: DocumentVersion) -> str:
        """
        Template method providing centralized concurrency safety and idempotency.
        Guarantees only one Celery task is dispatched under concurrent first-view requests.
        """
        # 1. Reset RENDER_NOT_APPLICABLE if settings changed to allow previews
        if version.render_status == DocumentVersion.RENDER_NOT_APPLICABLE and not version.has_pages:
            if self.is_dynamically_previewable(version):
                DocumentVersion.objects.filter(
                    pk=version.pk,
                    render_status=DocumentVersion.RENDER_NOT_APPLICABLE,
                ).update(render_status=DocumentVersion.RENDER_NOT_GENERATED)
                version.render_status = DocumentVersion.RENDER_NOT_GENERATED
                self._on_reset_to_previewable(version)

        # 2. Proceed with normal enqueue logic
        render_status = self.get_effective_render_status(version)
        if render_status != DocumentVersion.RENDER_NOT_GENERATED:
            return render_status

        # 3. Atomic claim (idempotency boundary for concurrent first views)
        now = timezone.now()
        updated = DocumentVersion.objects.filter(
            pk=version.pk,
            render_status=DocumentVersion.RENDER_NOT_GENERATED,
        ).update(
            render_status=DocumentVersion.RENDER_QUEUED,
            render_error='',
            updated_at=now,
        )

        if updated:
            version.render_status = DocumentVersion.RENDER_QUEUED
            version.render_error = ''
            version.updated_at = now
            try:
                self._dispatch_task(version)
            except Exception as exc:
                logger.error(
                    f"Failed to dispatch preview render task for version {version.pk}: {exc}",
                    exc_info=True,
                )
                error_msg = f"Failed to dispatch background rendering task: {exc}"
                fail_now = timezone.now()
                DocumentVersion.objects.filter(
                    pk=version.pk,
                    render_status=DocumentVersion.RENDER_QUEUED,
                ).update(
                    render_status=DocumentVersion.RENDER_FAILED,
                    render_error=error_msg,
                    updated_at=fail_now,
                )
                version.render_status = DocumentVersion.RENDER_FAILED
                version.render_error = error_msg
                version.updated_at = fail_now
                raise
            return DocumentVersion.RENDER_QUEUED

        # Another concurrent request claimed the render job; refresh to expose live state
        version.refresh_from_db(fields=['render_status', 'has_pages', 'render_error'])
        return self.get_effective_render_status(version)

    def _on_reset_to_previewable(self, version: DocumentVersion):
        """Optional hook for subclasses (e.g. repairing legacy download_only flags)."""
        pass

    def _dispatch_task(self, version: DocumentVersion):
        """
        Dispatches the format-specific Celery task.

        Must be implemented by subclasses that rely on the default enqueue_render_task
        template method (e.g., PDFRenderer).
        Synchronous renderers that do not dispatch background tasks (e.g., GenericFileRenderer) override enqueue_render_task directly and do not need to implement this.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} inherits the default enqueue_render_task but does not implement _dispatch_task."
        )

    def should_serve_pages(self, version: DocumentVersion, render_status: str) -> bool:
        """Determines whether page data should be served for this version."""
        return render_status == DocumentVersion.RENDER_READY

    def get_page_storage_key(self, version: DocumentVersion, page_number: int) -> Optional[str]:
        """
        Resolves the storage key for a specific page number.
        Returns None if the page is out-of-bounds or does not exist (triggering HTTP 404).
        """
        page = version.pages.filter(page_number=page_number).first()
        return page.storage_key if page else None

    def get_preview_pages_data(
        self,
        document: Document,
        version: DocumentVersion,
        share_link=None,
        dataroom_document_id=None,
        enable_watermark_override=None,
    ) -> list:
        """
        Describes the pages of this version for the viewer.

        Page images are never exposed as URLs: the viewer loads each page through an
        authenticated POST endpoint and paints it into a canvas (see core/fetch_guard.py).
        Share-link viewers do not receive the extracted text layer, so page text cannot be
        selected or copied from the viewer.
        """
        pages_data = []
        if version.has_pages:
            for page in version.pages.order_by('page_number'):
                safe_metadata = (
                    {k: v for k, v in page.metadata.items() if k != 'text_content'}
                    if isinstance(page.metadata, dict)
                    else {}
                )
                text_content = (
                    {"lines": []}
                    if share_link is not None
                    else (page.text_content if isinstance(page.text_content, dict) else {"lines": []})
                )
                pages_data.append({
                    'page_number': page.page_number,
                    'metadata': safe_metadata,
                    'page_links': page.page_links if isinstance(page.page_links, dict) else {'links': []},
                    'text_content': text_content,
                })
        return pages_data

    def get_download_url(
        self,
        document: Document,
        version: DocumentVersion,
        pages_data: list = None
    ) -> Optional[str]:
        """Returns the download URL for the original asset."""
        storage_key = version.original_storage_key or version.storage_key
        if storage_key:
            try:
                return fileserver.fileserver_client.generate_download_url(
                    storage_key, is_internal=False
                )
            except APIException:
                return None
        return None
