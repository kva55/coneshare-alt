import logging
import os
import tempfile
import requests
from io import BytesIO
from celery import shared_task
from pdf2image import convert_from_path, pdfinfo_from_bytes
from pypdf import PdfReader

from datetime import timedelta
from django.utils import timezone
from django.db.models import Q

from core.services import get_dynamic_setting
from .fileserver import fileserver_client
from .models import Document, DocumentPage, DocumentVersion, Folder
from .pdf_utils import extract_text_layout_for_pdf


logger = logging.getLogger('tasks')



def _resolve_pdf_object(obj):
    # NOTE: We use id() as a best-effort cycle guard. CPython may reuse addresses
    # for collected objects, but the while-loop's resolved-is-obj identity check
    # provides a secondary safety net.
    visited = set()
    while hasattr(obj, "get_object"):
        obj_id = id(obj)
        if obj_id in visited:
            break
        visited.add(obj_id)
        resolved = obj.get_object()
        if resolved is obj:
            break
        obj = resolved
    return obj


def _extract_links_for_page(pdf_page, page_num):
    """
    Helper to extract and normalize link annotations from a single PDF page.
    """
    media_box = pdf_page.mediabox
    page_w = float(media_box.width) if media_box.width else 0.0
    page_h = float(media_box.height) if media_box.height else 0.0

    links = []
    if page_w <= 0 or page_h <= 0:
        return links

    annots_obj = _resolve_pdf_object(pdf_page.get("/Annots"))
    if not annots_obj:
        return links

    try:
        annots_iter = iter(annots_obj)
    except TypeError:
        logger.warning(f"Page {page_num} annotations object is not iterable.")
        return links

    for annot in annots_iter:
        try:
            obj = _resolve_pdf_object(annot)
            if not obj or _resolve_pdf_object(obj.get("/Subtype")) != "/Link":
                continue

            action = _resolve_pdf_object(obj.get("/A"))
            rect = _resolve_pdf_object(obj.get("/Rect"))
            uri = None
            if action and _resolve_pdf_object(action.get("/S")) == "/URI":
                uri_obj = _resolve_pdf_object(action.get("/URI"))
                if isinstance(uri_obj, bytes):
                    uri = uri_obj.decode("utf-8", errors="ignore")
                elif isinstance(uri_obj, str):
                    uri = uri_obj

            if not rect or not uri:
                continue

            # Rect: [v1, v2, v3, v4] (origin at bottom-left).
            # PDF specs allow these coordinates to be written in arbitrary order.
            # We use min/max to normalize coordinates and prevent negative widths/heights.
            rect_values = [float(_resolve_pdf_object(v)) for v in rect]
            v1, v2, v3, v4 = rect_values
            x1 = min(v1, v3)
            y1 = min(v2, v4)
            x2 = max(v1, v3)
            y2 = max(v2, v4)

            links.append({
                "url": uri,
                "bbox": {
                    "left": (x1 / page_w) * 100,
                    "top": ((page_h - y2) / page_h) * 100,
                    "width": ((x2 - x1) / page_w) * 100,
                    "height": ((y2 - y1) / page_h) * 100
                }
            })
        except Exception as annot_err:
            logger.warning(f"Error parsing annot for link on page {page_num}: {annot_err}")

    return links




def _iter_pdf_page_images(pdf_bytes, page_count):
    """
    Yield (page_number, PIL image) for each page, rendering a single page per
    pdftoppm call. The PDF is written to a temporary file once and reused for
    every page.
    """
    with tempfile.NamedTemporaryFile(suffix='.pdf') as pdf_file:
        pdf_file.write(pdf_bytes)
        pdf_file.flush()
        for page_num in range(1, page_count + 1):
            images = convert_from_path(pdf_file.name, fmt='jpeg', first_page=page_num, last_page=page_num)
            if not images:
                raise ValueError(f"Rendering returned no image for page {page_num} of {page_count}.")
            yield page_num, images[0]


def _discard_partial_pages(version, storage_keys):
    """Best-effort removal of page records and files from a failed rendering attempt."""
    version.pages.all().delete()
    for storage_key in storage_keys:
        try:
            fileserver_client.delete_file(storage_key)
        except Exception as e:
            logger.warning(f"Could not delete partial page file {storage_key}: {e}")


@shared_task
def generate_pdf_pages_task(version_id):
    """
    A Celery task to process a PDF file from a DocumentVersion, extract its pages
    as images, and save them to storage.
    """
    try:
        version = DocumentVersion.objects.select_related('document').get(id=version_id)
        document = version.document
    except DocumentVersion.DoesNotExist:
        return  # Or log an error

    try:
        if version.has_pages:
            version.render_status = DocumentVersion.RENDER_READY
            version.render_error = ''
            version.save(update_fields=['render_status', 'render_error', 'updated_at'])
            return

        version.render_status = DocumentVersion.RENDER_PROCESSING
        version.render_error = ''
        version.save(update_fields=['render_status', 'render_error', 'updated_at'])

        # 1. Fetch PDF from storage
        download_url = fileserver_client.generate_download_url(version.storage_key)
        response = requests.get(download_url)
        response.raise_for_status()
        pdf_bytes = response.content

        # Get page count before full conversion
        info = pdfinfo_from_bytes(pdf_bytes, timeout=60)
        page_count = info.get("Pages", 0)

        max_pages = get_dynamic_setting('MAX_PREVIEW_PAGES')
        if page_count > max_pages:
            version.refresh_from_db(fields=['is_primary'])
            version.num_pages = page_count
            version.render_status = DocumentVersion.RENDER_FAILED
            version.render_error = "Document has too many pages to generate a preview."
            version.save(update_fields=['num_pages', 'render_status', 'render_error', 'updated_at'])

            if version.is_primary:
                document.status = 'ready'
                document.num_pages = page_count
                document.status_message = "Document has too many pages to generate a preview."
                document.save(update_fields=['status', 'num_pages', 'status_message', 'updated_at'])

            logger.info(f"Skipping page generation for document {document.id}, page count ({page_count}) > {max_pages}.")
            return

        # Extract links using pypdf (best-effort)
        # We wrap the entire block in a single outer try-except. This catches document-level parsing
        # issues (e.g. invalid PDF header on PdfReader initialization, or metadata index errors)
        # and lets the task fallback gracefully to rendering page JPEGs without crashing.
        page_links_by_num = {}
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            num_pdf_pages = len(reader.pages)
            for idx in range(num_pdf_pages):
                page_num = idx + 1
                try:
                    pdf_page = reader.pages[idx]
                    links = _extract_links_for_page(pdf_page, page_num)
                    if links:
                        page_links_by_num[page_num] = {"links": links}
                except Exception as page_err:
                    logger.warning(f"Error extracting links from page {page_num}: {page_err}")
        except Exception as reader_err:
            logger.warning(f"Failed to parse PDF annotations/links via pypdf: {reader_err}")

        # Extract text layout using pdftotext (best-effort)
        page_text_by_num = extract_text_layout_for_pdf(pdf_bytes)

        # 2. Render each page to PNG, upload it and create its DB record.
        # Pages are rendered one at a time so that memory stays at a single page image
        # instead of growing with the page count (~16 MB per A4 page at 200 dpi).
        base_path, _ = os.path.splitext(version.original_storage_key)
        version.pages.all().delete()
        uploaded_keys = []
        try:
            for page_num, image in _iter_pdf_page_images(pdf_bytes, page_count):
                page_storage_key = f"{base_path}_page_{page_num}.jpg"

                if getattr(image, 'mode', None) in ('RGBA', 'LA', 'P'):
                    image = image.convert('RGB')

                size = getattr(image, 'size', None)
                page_width, page_height = size if isinstance(size, tuple) and len(size) == 2 else (None, None)

                buffer = BytesIO()
                image.save(buffer, 'JPEG')
                image.close()

                # TODO(refactor): Migrate to fileserver_client.upload_file(page_storage_key, buffer.getvalue()) in a separate chore PR
                upload_url = fileserver_client.generate_upload_url(page_storage_key)
                upload_response = requests.put(upload_url, data=buffer.getvalue())
                upload_response.raise_for_status()
                uploaded_keys.append(page_storage_key)

                DocumentPage.objects.create(
                    document_version=version,
                    page_number=page_num,
                    storage_key=page_storage_key,
                    page_links=page_links_by_num.get(page_num, {"links": []}),
                    metadata={
                        "text_content": page_text_by_num.get(page_num, {"lines": []}),
                        **({"width": page_width, "height": page_height} if page_width and page_height else {}),
                    },
                )
        except Exception:
            # Pages are uploaded as they are rendered, so a failure part-way through
            # must not leave the pages of this attempt behind.
            _discard_partial_pages(version, uploaded_keys)
            raise

        # 3. Finalize status and metadata
        num_pages = page_count
        version.refresh_from_db(fields=['is_primary'])
        version.num_pages = num_pages
        version.has_pages = True
        version.render_status = DocumentVersion.RENDER_READY
        version.render_error = ''
        version.save(update_fields=[
            'num_pages', 'has_pages', 'render_status', 'render_error', 'updated_at'
        ])

        if version.is_primary:
            document.num_pages = num_pages
            document.storage_key = version.storage_key
            document.status = 'ready'
            document.status_message = ''
            document.save(update_fields=[
                'num_pages', 'storage_key', 'status', 'status_message', 'updated_at'
            ])

    except Exception as e:
        version.render_status = DocumentVersion.RENDER_FAILED
        version.render_error = str(e)[:1000]
        version.save(update_fields=['render_status', 'render_error', 'updated_at'])
        # Consider more robust logging in a real application
        logger.error(f"Error processing document version {version_id}: {e}")



@shared_task
def purge_expired_trash_documents_task():
    """
    Daily Celery task to permanently purge soft-deleted items that have been
    in the trash for more than 30 days.
    """
    # Inline import required here to prevent circular import loop with documents.services
    from .services import delete_document_and_files, delete_folder_and_contents

    threshold = timezone.now() - timedelta(days=30)

    expired_folders = Folder.objects.deleted().filter(
        deleted_at__lt=threshold
    ).filter(
        Q(parent__isnull=True) | Q(parent__deleted_at__isnull=True)
    )
    for folder in list(expired_folders):
        try:
            delete_folder_and_contents(folder)
        except Exception as e:
            logger.error(f"Failed to auto-purge expired folder {folder.id}: {e}")

    expired_docs = Document.objects.deleted().filter(
        deleted_at__lt=threshold
    ).filter(
        Q(folder__isnull=True) | Q(folder__deleted_at__isnull=True)
    )
    for doc in list(expired_docs):
        try:
            delete_document_and_files(doc)
        except Exception as e:
            logger.error(f"Failed to auto-purge expired document {doc.id}: {e}")


