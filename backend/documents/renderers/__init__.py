from typing import List, Type

from documents.models import DocumentVersion
from .base import BasePreviewRenderer
from .generic import GenericFileRenderer
from .pdf import PDFRenderer
from .utils import normalize_content_type

# Priority-ordered registry of renderer classes.
# Only PDFs are previewable; everything else falls back to GenericFileRenderer
# (stored, never rendered in the browser).
RENDERER_CLASSES: List[Type[BasePreviewRenderer]] = [
    PDFRenderer,              # PDF files (server-rendered page images)
    GenericFileRenderer,      # Catch-all fallback (no preview)
]


def get_renderer(version: DocumentVersion) -> BasePreviewRenderer:
    """Finds the matching renderer class and instantiates a clean, thread-isolated handler."""
    for renderer_cls in RENDERER_CLASSES:
        if renderer_cls.can_handle_version(version):
            return renderer_cls()
    return GenericFileRenderer()


def get_renderer_for_file(content_type: str, filename: str) -> BasePreviewRenderer:
    """Finds the matching renderer class and instantiates a clean, thread-isolated handler."""
    for renderer_cls in RENDERER_CLASSES:
        if renderer_cls.can_handle_file(content_type, filename):
            return renderer_cls()
    return GenericFileRenderer()


__all__ = [
    'BasePreviewRenderer',
    'GenericFileRenderer',
    'PDFRenderer',
    'RENDERER_CLASSES',
    'get_renderer',
    'get_renderer_for_file',
    'normalize_content_type',
]
