import logging
import re
from security.throttling import ViewerThrottle
from io import BytesIO

import requests
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from core.fetch_guard import is_script_fetch, no_store_headers, sniff_image_content_type
from core.pagination import StandardResultsSetPagination
from core.permissions import IsAdmin
from datarooms.models import Dataroom, DataroomDocument
from documents.fileserver import fileserver_client
from documents.renderers import get_renderer

from sharelinks.events import CLIENT_EVENT_TYPES

from . import services
from .events import EventType, record_event
from .models import (Checkout, CheckoutEvent, CheckoutFile, CheckoutGuest, CheckoutPolicy)

logger = logging.getLogger(__name__)
STREAM_CHUNK = 64 * 1024
EMAIL_SPLIT = re.compile(r'[\s,;]+')


def _refused(refusal):
    body = {"detail": refusal.message}
    if refusal.code:
        body['code'] = refusal.code
    return Response(body, status=refusal.status_code)


def _session(request):
    session = services.get_session(request)
    if session is None:
        return None, Response({"detail": "Sign in to continue."}, status=status.HTTP_401_UNAUTHORIZED)
    return session, None


# --------------------------------------------------------------------------- guest: sign-in

class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200)


@extend_schema(tags=['checkout'])
class AuthRequestView(APIView):
    """Ask for a sign-in link. The answer never depends on the address."""
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=EmailSerializer, responses={200: dict, 400: dict})
    def post(self, request):
        serializer = EmailSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        services.request_login_link(serializer.validated_data['email'], request)
        return Response({"message": services.GENERIC_REQUEST_MESSAGE})


@extend_schema(tags=['checkout'])
class AuthLinkView(APIView):
    """Check a link without using it up (so mail scanners that open links cannot burn it)."""
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=TokenSerializer, responses={200: dict, 400: dict})
    def post(self, request):
        # POST, token in the body: an address would be written to access logs.
        serializer = TokenSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        masked = services.peek_login_link(serializer.validated_data['token'], request)
        if masked is None:
            return Response({"message": services.INVALID_LINK_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        return no_store_headers(Response({"email": masked}))


@extend_schema(tags=['checkout'])
class AuthConsumeView(APIView):
    """Sign in with a link. Single use."""
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=TokenSerializer, responses={200: dict, 400: dict})
    def post(self, request):
        serializer = TokenSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        session = services.consume_login_link(serializer.validated_data['token'], request)
        if session is None:
            return Response({"message": services.INVALID_LINK_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"email": session.email, "expires_at": session.expires_at})


@extend_schema(tags=['checkout'])
class SessionView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(responses={200: dict, 401: dict})
    def get(self, request):
        session, error = _session(request)
        if error:
            return error
        return Response({"email": session.email, "expires_at": session.expires_at})

    @extend_schema(responses={204: None})
    def delete(self, request):
        services.end_session(request)
        from core.nocache import clear_site_cache
        return clear_site_cache(Response(status=status.HTTP_204_NO_CONTENT))


# --------------------------------------------------------------------------- guest: files

@extend_schema(tags=['checkout'])
class DataroomsView(APIView):
    """The datastores the signed-in guest is assigned to, with their files."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(responses={200: dict, 401: dict})
    def get(self, request):
        session, error = _session(request)
        if error:
            return error
        return no_store_headers(Response({
            "email": session.email, "expires_at": session.expires_at,
            "datarooms": services.available_datarooms(session),
        }))


@extend_schema(tags=['checkout'])
class AcceptNdaView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(request=None, responses={204: None, 401: dict, 404: dict})
    def post(self, request, policy_id):
        session, error = _session(request)
        if error:
            return error
        if not services.accept_nda(session, policy_id, request):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _checkout_body(checkout):
    return {"id": str(checkout.id), "status": checkout.status, "expires_at": checkout.expires_at}


@extend_schema(tags=['checkout'])
class RequestFileView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(request=None, responses={200: dict, 401: dict, 403: dict, 404: dict})
    def post(self, request, file_id):
        session, error = _session(request)
        if error:
            return error
        try:
            checkout = services.request_file(session, file_id, request)
        except services.CheckoutRefused as refusal:
            return _refused(refusal)
        return no_store_headers(Response(_checkout_body(checkout)))


@extend_schema(tags=['checkout'])
class OpenCheckoutView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(request=None, responses={200: dict, 401: dict, 403: dict, 404: dict, 409: dict})
    def post(self, request, checkout_id):
        session, error = _session(request)
        if error:
            return error
        try:
            checkout = services.open_checkout(session, checkout_id, request)
        except services.CheckoutRefused as refusal:
            return _refused(refusal)
        return no_store_headers(Response(_checkout_body(checkout)))


@extend_schema(tags=['checkout'])
class ReturnCheckoutView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(request=None, responses={200: dict, 401: dict, 404: dict})
    def post(self, request, checkout_id):
        session, error = _session(request)
        if error:
            return error
        try:
            checkout = services.return_checkout(session, checkout_id, request)
        except services.CheckoutRefused as refusal:
            return _refused(refusal)
        return Response(_checkout_body(checkout))


def _open_checkout_or_error(request, checkout_id):
    """The signed-in guest's open check-out, or an error response."""
    session, error = _session(request)
    if error:
        return None, None, error
    try:
        checkout = services.get_guest_checkout(session, checkout_id)
    except services.CheckoutRefused as refusal:
        return None, None, _refused(refusal)
    if not checkout.window_open() or not checkout.file.visible or not services.nda_accepted(checkout.policy, session.email, checkout.file):
        return None, None, Response({"detail": "This check-out is not open."}, status=status.HTTP_403_FORBIDDEN)
    return session, checkout, None


IMAGE_EXTENSIONS = ('.psd', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff')
MAX_IMAGE_BYTES = 100 * 1024 * 1024
MAX_IMAGE_EDGE = 2400


def _is_image_file(document, version) -> bool:
    """Pictures (including Photoshop PSD) have no stored pages: they are drawn on the fly as one page."""
    return bool(version) and (document.name or '').lower().endswith(IMAGE_EXTENSIONS)


def _is_pdf(document, version) -> bool:
    return bool(version) and (document.type == 'pdf' or (version.content_type or '') == 'application/pdf'
                              or (document.name or '').lower().endswith('.pdf'))


def _preview_state(document, version, pages):
    """
    Why a file can or cannot be shown, so the viewer can say so instead of a vague "cannot be viewed".
    Previews are generated lazily, on first view: opening a PDF here starts the job (idempotent: only
    one job is ever queued), exactly as opening a share link does.
    """
    if pages:
        return 'ready'
    if not _is_pdf(document, version):
        return 'unsupported'
    try:
        status_now = get_renderer(version).enqueue_render_task(version)
    except Exception:
        logger.exception("Could not start the preview of document %s", document.id)
        return 'failed'
    if status_now in ('queued', 'processing', 'not_generated'):
        return 'processing'
    if status_now == 'not_applicable':   # too large, or a download-only file
        return 'unsupported'
    return 'failed'


def _primary_version(checkout):
    document = checkout.file.dataroom_document.document
    return document, document.versions.filter(is_primary=True).first()


@extend_schema(tags=['checkout'])
class CheckoutDetailView(APIView):
    """What the viewer needs for an open check-out: page sizes, time left, whether download is allowed."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(responses={200: dict, 401: dict, 403: dict, 404: dict})
    def get(self, request, checkout_id):
        session, checkout, error = _open_checkout_or_error(request, checkout_id)
        if error:
            return error
        document, version = _primary_version(checkout)
        protections = _protections(checkout)
        pages = []
        if _is_image_file(document, version):
            pages = [{'page_number': 1, 'width': None, 'height': None}]
        elif version and version.has_pages and _is_pdf(document, version):
            pages = [{'page_number': p.page_number,
                      'width': (p.metadata or {}).get('width'), 'height': (p.metadata or {}).get('height')}
                     for p in version.pages.order_by('page_number')]
        return no_store_headers(Response({
            "id": str(checkout.id), "name": checkout.file.dataroom_document.name or document.name,
            "expires_at": checkout.expires_at, "pages": pages,
            "preview": {"state": _preview_state(document, version, pages)},
            "allow_download": checkout.file.download_allowed() and checkout.downloads_left() > 0 and not protections['forbid_download'],
            "capture_actions": _capture_actions(),
            "trace": _tracing_enabled(),
            "protections": {"blur_when_unfocused": protections['blur_when_unfocused'],
                            "restricted_viewing_pane": protections['restricted_viewing_pane']},
        }))


@extend_schema(tags=['checkout'])
class CheckoutViewerEventView(APIView):
    """Behaviour signals from the guest's viewer (same set as share links; see sharelinks/events.py)."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(request=dict, responses={204: None, 400: dict, 401: dict, 403: dict, 404: dict})
    def post(self, request, checkout_id):
        session, error = _session(request)
        if error:
            return error
        event_type = request.data.get('event_type')
        if event_type not in CLIENT_EVENT_TYPES:
            return Response({"event_type": ["Unsupported event type."]}, status=status.HTTP_400_BAD_REQUEST)
        from sharelinks.events import sanitize_metadata
        try:
            services.record_viewer_event(session, checkout_id, event_type, sanitize_metadata(request.data.get('metadata')), request)
        except services.CheckoutRefused as refusal:
            return _refused(refusal)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _tracing_enabled() -> bool:
    from security.services import snapshot
    cfg = snapshot()
    return bool(cfg['monitoring_enabled'] and cfg['checkout_tracing_enabled'])


@extend_schema(tags=['checkout'])
class CheckoutTraceView(APIView):
    """Cursor and page-in-view samples from the guest's viewer, for the session report."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]

    @extend_schema(request=dict, responses={204: None, 401: dict, 403: dict, 404: dict})
    def post(self, request, checkout_id):
        session, error = _session(request)
        if error:
            return error
        try:
            services.record_trace(session, checkout_id, request.data.get('moves'), request.data.get('views'), request,
                                  clicks=request.data.get('clicks'), now=request.data.get('now'))
        except services.CheckoutRefused as refusal:
            return _refused(refusal)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _capture_actions() -> dict:
    from security.capture import effective_actions
    from security.services import snapshot
    return effective_actions(snapshot()['capture_actions'])


def _protections(checkout):
    """The viewing protections set on the file and on its datastore (see sharelinks.AccessPolicy)."""
    from django.db.models import Q
    from sharelinks.models import AccessPolicy
    document = checkout.file.dataroom_document.document
    out = {'blur_when_unfocused': False, 'restricted_viewing_pane': False, 'watermark_text': '', 'forbid_download': False}
    for p in AccessPolicy.objects.filter(Q(document=document) | Q(dataroom=checkout.policy.dataroom)):
        out['blur_when_unfocused'] |= p.blur_when_unfocused
        out['restricted_viewing_pane'] |= p.restricted_viewing_pane
        if p.enable_watermark and p.watermark_text and not out['watermark_text']:
            out['watermark_text'] = p.watermark_text
        if p.allow_download is False:
            out['forbid_download'] = True
    return out


class _PageSource:
    """A resolved page request. The picture is decoded on first use, so a request that is answered from the
    tile cache never pays for decoding."""
    def __init__(self, session, checkout, source_bytes, image_file, protections, document, page_number):
        self.session, self.checkout, self.source_bytes = session, checkout, source_bytes
        self.image_file, self.protections, self.document, self.page_number = image_file, protections, document, page_number
        self._picture = None

    @property
    def picture(self):
        if self._picture is None:
            from sharelinks.views import Image
            picture = Image.open(BytesIO(self.source_bytes))
            if self.image_file:   # PSD and friends: flatten to RGB and keep the size sensible
                picture.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
            self._picture = picture.convert("RGBA")
        return self._picture


def _note_non_browser(request):
    """A page request that did not come from the viewer's script, from someone who is signed in."""
    from django.core.cache import cache
    from sharelinks.integrity import monitoring_on
    if not monitoring_on():
        return
    session = services.get_session(request)
    if session is None or not cache.add(f"nonbrowser:{request.session.session_key}", 1, 60):
        return
    record_event('non_browser_request', request, session.email, None,
                 {'agent': (request.META.get('HTTP_USER_AGENT', '') or '')[:80]})


def _note_automation(request, session, checkout):
    """A user agent that announces a script or a headless browser, once per sign-in and check-out."""
    from django.core.cache import cache
    from sharelinks.integrity import automation_marker, monitoring_on
    marker = automation_marker(request)
    if marker and monitoring_on() and cache.add(f"automation:{request.session.session_key}:{checkout.id}", 1, 3600):
        record_event('automation_detected', request, session.email, checkout.policy,
                     {'via': 'user_agent', 'marker': marker, 'file': services._file_name(checkout.file)})


def _note_tiles_without_cursor(src):
    """Many tiles fetched, a minute into the viewing, and no cursor movement ever reported: a script, not a hand."""
    from django.core.cache import cache
    from sharelinks.integrity import monitoring_on
    from security.services import snapshot
    cfg = snapshot()
    if not (monitoring_on() and cfg['checkout_tracing_enabled']):
        return
    key = f"tilecount:{src.checkout.id}"
    cache.add(key, 0, 3600)
    try:
        count = cache.incr(key)
    except ValueError:
        return
    if count != 40 or not src.checkout.opened_at or (timezone.now() - src.checkout.opened_at).total_seconds() < 60:
        return
    if src.checkout.trace_chunks.exists():
        return
    record_event('tiles_without_cursor', request=None, email=src.session.email, policy=src.checkout.policy,
                 metadata={'file': services._file_name(src.checkout.file), 'requests': count})


def _focus_key(checkout) -> str:
    return f"checkout:{checkout.id}"


def _load_checkout_page(request, checkout_id, page_number):
    """
    Resolve a page request of an open check-out: script only, session and window checked, the focus
    lease honoured when the file asks for it, source fetched and decoded. Returns (_PageSource, None)
    or (None, Response).
    """
    from sharelinks.protected_pages import focus_lease_key_ok
    if not is_script_fetch(request):
        _note_non_browser(request)
        return None, Response({"detail": "Pages can only be loaded by the viewer."}, status=status.HTTP_403_FORBIDDEN)
    session, checkout, error = _open_checkout_or_error(request, checkout_id)
    if error:
        return None, error
    _note_automation(request, session, checkout)
    protections = _protections(checkout)
    if protections['blur_when_unfocused'] and not focus_lease_key_ok(request, _focus_key(checkout)):
        return None, Response({"detail": "The viewer is not in focus."}, status=status.HTTP_423_LOCKED)
    document, version = _primary_version(checkout)
    image_file = _is_image_file(document, version)
    if image_file:
        key = (version.original_storage_key or version.storage_key) if page_number == 1 else None
        if key and version.file_size and version.file_size > MAX_IMAGE_BYTES:
            return None, Response({"detail": "This picture is too large to view."}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    else:
        key = version and get_renderer(version).get_page_storage_key(version, page_number)
    if not key:
        return None, Response({"detail": "Page not found."}, status=status.HTTP_404_NOT_FOUND)
    from sharelinks.protected_pages import cached_source

    def _fetch():
        response = requests.get(fileserver_client.generate_download_url(key, is_internal=True), timeout=30)
        response.raise_for_status()
        return response.content
    try:
        source_bytes = cached_source(key, _fetch)
    except (APIException, requests.RequestException) as exc:
        logger.error("Checkout page failed to reach the file server: %s", type(exc).__name__)
        return None, Response({"detail": "Could not retrieve file. Please try again."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    from sharelinks.views import Image
    if Image is None:   # never hand out an unmarked page because the marker is missing
        return None, Response({"detail": "Page service unavailable."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return _PageSource(session, checkout, source_bytes, image_file, protections, document, page_number), None


def _watermarked(src, request, image):
    """The guest's mark (the file's own watermark text when it has one), dense on restricted files."""
    from sharelinks.views import DEFAULT_WATERMARK_TEMPLATE, _apply_watermark_to_image, _render_watermark_text
    template = src.protections['watermark_text'] or DEFAULT_WATERMARK_TEMPLATE
    text = _render_watermark_text(template, request, viewer_email=src.session.email)
    return _apply_watermark_to_image(image, text, dense=src.protections['restricted_viewing_pane'])


@extend_schema(tags=['checkout'])
class CheckoutPageView(APIView):
    """
    One page of an open check-out, watermarked with the guest's address and IP. POST only and
    script only (like every page image in this app): there is no page URL to open or share. When the
    file has the restricted viewing pane the page is blurred here on the server (and the clear
    pixels only leave through the tiles endpoint); when it has "blur when not in focus" pages are
    refused (423) while the viewer reports that it is not in focus.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(request=None, responses={200: None, 401: dict, 403: dict, 404: dict, 423: dict, 503: dict})
    def post(self, request, checkout_id, page_number):
        src, error = _load_checkout_page(request, checkout_id, page_number)
        if error:
            return error
        from sharelinks.protected_pages import blur_page
        from sharelinks.views import WATERMARK_JPEG_QUALITY
        try:
            image = blur_page(src.picture) if src.protections['restricted_viewing_pane'] else src.picture
            buffer = BytesIO()
            _watermarked(src, request, image).convert("RGB").save(buffer, format="JPEG", quality=WATERMARK_JPEG_QUALITY)
        except Exception as exc:
            logger.error("Checkout page processing failed: %s", type(exc).__name__)
            return Response({"detail": "An error occurred while generating the page."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        response = HttpResponse(buffer.getvalue(), content_type="image/jpeg")
        response["Content-Length"] = str(len(buffer.getvalue()))
        return no_store_headers(response)


@extend_schema(tags=['checkout'])
class CheckoutTilesView(APIView):
    """Clear (watermarked) tiles of a page, for files with the restricted viewing pane; rate limited per session."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(request=dict, responses={200: dict, 400: dict, 403: dict, 404: dict, 423: dict, 429: dict})
    def post(self, request, checkout_id, page_number):
        from django.conf import settings
        from django.core.cache import cache
        from django.http import JsonResponse
        from sharelinks.protected_pages import MAX_TILES_PER_REQUEST, render_tiles, tile_size
        src, error = _load_checkout_page(request, checkout_id, page_number)
        if error:
            return error
        if not src.protections['restricted_viewing_pane']:
            return Response({"detail": "Not available."}, status=status.HTTP_404_NOT_FOUND)
        raw = request.data.get('tiles')
        if not isinstance(raw, list) or not raw or len(raw) > MAX_TILES_PER_REQUEST:
            return Response({"detail": f"'tiles' must list 1 to {MAX_TILES_PER_REQUEST} cells."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            cells = {(int(c), int(r)) for c, r in raw}
        except (TypeError, ValueError):
            return Response({"detail": "Each tile is [column, row]."}, status=status.HTTP_400_BAD_REQUEST)
        bucket = f"checkout-tiles:{request.session.session_key}:{src.checkout.id}"
        cache.add(bucket, 0, 60)
        try:
            count = cache.incr(bucket, len(cells))
        except ValueError:
            count = len(cells)
        if count > settings.SPOTLIGHT_TILES_PER_MINUTE:
            if cache.add(f"{bucket}:logged", 1, 60):   # once a minute, not once per refused request
                record_event('spotlight_limit', request, src.session.email, src.checkout.policy, {'file': src.document.name})
            return Response({"detail": "Too many requests."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        try:
            from sharelinks.protected_pages import cached_image
            from sharelinks.views import DEFAULT_WATERMARK_TEMPLATE, _render_watermark_text
            text = _render_watermark_text(src.protections['watermark_text'] or DEFAULT_WATERMARK_TEMPLATE, request,
                                          viewer_email=src.session.email)
            # per check-out, page and watermark text: another guest's image is never reused
            image = cached_image(('checkout', str(src.checkout.id), src.page_number, text),
                                 lambda: _watermarked(src, request, src.picture))
            tiles = render_tiles(image, sorted(cells))
            size = tile_size(image.width)
        except Exception as exc:
            logger.error("Checkout tiles failed: %s", type(exc).__name__)
            return Response({"detail": "An error occurred while generating the page."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        _note_tiles_without_cursor(src)
        return no_store_headers(JsonResponse({"tile_size": size, "tiles": tiles}))


@extend_schema(tags=['checkout'])
class CheckoutFocusView(APIView):
    """The viewer reports whether its page is in focus (the focus lease, as for share links)."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(request=dict, responses={204: None, 400: dict, 401: dict, 403: dict, 404: dict})
    def post(self, request, checkout_id):
        from sharelinks.protected_pages import set_focus_key
        session, checkout, error = _open_checkout_or_error(request, checkout_id)
        if error:
            return error
        focused = request.data.get('focused')
        if not isinstance(focused, bool):
            return Response({"detail": "'focused' must be true or false."}, status=status.HTTP_400_BAD_REQUEST)
        set_focus_key(request, _focus_key(checkout), focused)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9._ -]+', '_', name or 'file').strip(' .') or 'file'
    return cleaned[:150]


@extend_schema(tags=['checkout'])
class CheckoutDownloadView(APIView):
    """Only when the datastore allows downloads; limited per check-out and to its time window."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ViewerThrottle]   # viewing makes many small requests: its own, visible limit

    @extend_schema(request=None, responses={200: None, 401: dict, 403: dict, 404: dict, 503: dict})
    def post(self, request, checkout_id):
        if not is_script_fetch(request):
            return Response({"detail": "Files can only be fetched by the check-out page."}, status=status.HTTP_403_FORBIDDEN)
        session, checkout, error = _open_checkout_or_error(request, checkout_id)
        if error:
            return error
        if not checkout.file.download_allowed() or _protections(checkout)['forbid_download']:
            return Response({"detail": "Downloads are not allowed for this datastore."}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            checkout = Checkout.objects.select_for_update().get(pk=checkout.pk)
            if not checkout.window_open() or checkout.downloads_left() < 1:
                record_event(EventType.DOWNLOAD_DENIED, request, session.email, checkout.policy,
                             {'reason': 'window_over_or_used'})
                return Response({"detail": "No downloads left on this check-out."}, status=status.HTTP_403_FORBIDDEN)
            document, version = _primary_version(checkout)
            key = version and (version.original_storage_key or version.storage_key)
            if not key:
                return Response({"detail": "File not found."}, status=status.HTTP_404_NOT_FOUND)
            checkout.downloads += 1  # counted before streaming: an interrupted transfer still uses it
            checkout.last_download_at = timezone.now()
            checkout.save(update_fields=['downloads', 'last_download_at', 'updated_at'])

        name = checkout.file.dataroom_document.name or document.name
        try:
            upstream = requests.get(fileserver_client.generate_download_url(key, is_internal=True), stream=True, timeout=30)
            upstream.raise_for_status()
        except (APIException, requests.RequestException) as exc:
            logger.error("Checkout download failed to reach the file server: %s", type(exc).__name__)
            Checkout.objects.filter(pk=checkout.pk, downloads__gt=0).update(downloads=checkout.downloads - 1)
            return Response({"detail": "Could not retrieve file. Please try again."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        record_event(EventType.FILE_DOWNLOADED, request, session.email, checkout.policy, {'file': name})
        response = StreamingHttpResponse(upstream.iter_content(STREAM_CHUNK),
                                         content_type=(version.content_type or 'application/octet-stream'))
        response['Content-Disposition'] = f'attachment; filename="{_safe_filename(name)}"'
        if version.file_size:
            response['Content-Length'] = str(version.file_size)
        return no_store_headers(response)


# --------------------------------------------------------------------------- admin

class PolicySerializer(serializers.ModelSerializer):
    dataroom_name = serializers.CharField(source='dataroom.name', read_only=True)
    file_count = serializers.SerializerMethodField()
    guest_count = serializers.SerializerMethodField()

    class Meta:
        model = CheckoutPolicy
        fields = ['id', 'dataroom', 'dataroom_name', 'enabled', 'checkout_minutes', 'max_attempts',
                  'allow_download', 'downloads_per_checkout', 'require_nda', 'nda_text', 'nda_version',
                  'notify_mode', 'notify_email', 'file_count', 'guest_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at', 'dataroom_name', 'file_count', 'guest_count', 'nda_version']

    def get_file_count(self, obj) -> int:
        return obj.files.filter(visible=True).count()

    def get_guest_count(self, obj) -> int:
        return obj.guests.filter(revoked_at__isnull=True).count()

    def validate_dataroom(self, value):
        if value.organization_id != self.context['request'].user.organization_id:
            raise serializers.ValidationError("Datastore not found.")
        return value

    def validate(self, attrs):
        fields = ('checkout_minutes', 'max_attempts', 'notify_mode', 'notify_email', 'require_nda', 'nda_text')
        current = {f: getattr(self.instance, f) for f in fields} if self.instance else {}
        merged = {**current, **{k: v for k, v in attrs.items() if k in fields}}
        if merged.get('require_nda') and not (merged.get('nda_text') or '').strip():
            raise serializers.ValidationError({'nda_text': 'Write the agreement text, or switch the NDA off.'})
        probe = CheckoutPolicy(**{k: v for k, v in merged.items() if k in fields and k not in ('require_nda', 'nda_text')})
        try:
            probe.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        return attrs


def _policy(request, policy_id):
    return get_object_or_404(CheckoutPolicy, id=policy_id, organization=request.user.organization)


class AdminPolicyListView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(tags=['checkout'], responses={200: dict})
    def get(self, request):
        from django.conf import settings
        policies = CheckoutPolicy.objects.filter(organization=request.user.organization).select_related('dataroom')
        datastores = Dataroom.objects.filter(organization=request.user.organization).values('id', 'name')
        return Response({
            'results': PolicySerializer(policies, many=True, context={'request': request}).data,
            'datastores': list(datastores),
            'limits': {'max_minutes': settings.CHECKOUT_MAX_MINUTES, 'max_attempts': settings.CHECKOUT_MAX_ATTEMPTS_CAP},
            'pending_approvals': Checkout.objects.filter(
                policy__organization=request.user.organization, status=Checkout.PENDING).count(),
        })

    @extend_schema(tags=['checkout'], request=PolicySerializer, responses={201: PolicySerializer, 400: dict})
    def post(self, request):
        serializer = PolicySerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        try:
            policy = serializer.save(organization=request.user.organization, created_by=request.user)
        except IntegrityError:
            return Response({"dataroom": ["This datastore already has a check-out policy."]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PolicySerializer(policy, context={'request': request}).data, status=status.HTTP_201_CREATED)


class AdminPolicyDetailView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(tags=['checkout'], responses={200: PolicySerializer})
    def get(self, request, policy_id):
        return Response(PolicySerializer(_policy(request, policy_id), context={'request': request}).data)

    @extend_schema(tags=['checkout'], request=PolicySerializer, responses={200: PolicySerializer, 400: dict})
    def patch(self, request, policy_id):
        policy = _policy(request, policy_id)
        data = {k: v for k, v in request.data.items() if k != 'dataroom'}  # the datastore is fixed
        serializer = PolicySerializer(policy, data=data, partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(tags=['checkout'], responses={204: None})
    def delete(self, request, policy_id):
        _policy(request, policy_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminPolicyFilesView(APIView):
    """
    Every document of the datastore with its check-out settings, so a file that was just uploaded
    can be set up at once (no share link involved). PUT replaces the settings.
    """
    permission_classes = [IsAdmin]

    @extend_schema(tags=['checkout'], responses={200: dict})
    def get(self, request, policy_id):
        policy = _policy(request, policy_id)
        configured = {f.dataroom_document_id: f for f in policy.files.all()}
        documents = DataroomDocument.objects.filter(dataroom=policy.dataroom).select_related('document').order_by('-created_at')
        rows = []
        for d in documents:
            f = configured.get(d.id)
            rows.append({
                'id': str(d.id), 'name': d.name or d.document.name, 'added': d.created_at,
                'visible': bool(f and f.visible), 'configured': f is not None,
                'requires_approval': bool(f and f.requires_approval),
                'requires_nda': None if f is None else f.requires_nda,
                'allow_download': None if f is None else f.allow_download,
            })
        return Response({'results': rows})

    @extend_schema(tags=['checkout'], request=dict, responses={200: dict, 400: dict})
    def put(self, request, policy_id):
        """Body: ``{"files": [{"id", "visible", "requires_approval", "requires_nda", "allow_download"}, ...]}``."""
        policy = _policy(request, policy_id)
        files = request.data.get('files')
        if not isinstance(files, list) or not all(isinstance(f, dict) and isinstance(f.get('id'), str) for f in files):
            return Response({"detail": "'files' must be a list of file settings."}, status=status.HTTP_400_BAD_REQUEST)
        wanted = {f['id']: f for f in files}
        valid = set(DataroomDocument.objects.filter(dataroom=policy.dataroom, id__in=wanted).values_list('id', flat=True))
        if len(valid) != len(wanted):
            return Response({"detail": "Some documents do not belong to this datastore."}, status=status.HTTP_400_BAD_REQUEST)

        def tri(value):
            return value if value in (True, False) else None   # null: follow the datastore

        if not (policy.nda_text or '').strip() and any(tri(f.get('requires_nda')) for f in files):
            return Response({"detail": "Write the datastore's NDA text before requiring it for a file."},
                            status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            policy.files.exclude(dataroom_document_id__in=valid).delete()
            for doc_id in valid:
                f = wanted[doc_id]
                CheckoutFile.objects.update_or_create(policy=policy, dataroom_document_id=doc_id, defaults={
                    'visible': bool(f.get('visible', True)), 'requires_approval': bool(f.get('requires_approval')),
                    'requires_nda': tri(f.get('requires_nda')), 'allow_download': tri(f.get('allow_download'))})
        return Response({'configured': len(valid), 'visible': sum(1 for f in wanted.values() if f.get('visible', True))})


class DocumentAccessView(APIView):
    """
    The check-out access settings of ONE file, readable and editable by whoever manages its datastore
    (not only organization admins), so they can be set the moment a file is uploaded. They belong to
    the file in the datastore and have nothing to do with share links: a file nobody has a link to is
    still available to the datastore's guests once it is visible. Saving creates the datastore's
    check-out policy (switched off) if it has none yet, so nothing is lost; enabling it and adding
    guests happens in the Check Out Center.
    """
    permission_classes = [permissions.IsAuthenticated]

    def _find(self, request, dataroom_document_id):
        from datarooms.views import get_dataroom_queryset_for_user
        return DataroomDocument.objects.select_related('dataroom', 'document').filter(
            id=dataroom_document_id, dataroom__in=get_dataroom_queryset_for_user(request.user)).first()

    def _body(self, dd, policy, entry):
        return {
            'id': str(dd.id), 'name': dd.name or dd.document.name, 'datastore': dd.dataroom.name,
            'visible': bool(entry and entry.visible), 'requires_approval': bool(entry and entry.requires_approval),
            'requires_nda': None if entry is None else entry.requires_nda,
            'allow_download': None if entry is None else entry.allow_download,
            'datastore_settings': {
                'enabled': bool(policy and policy.enabled),
                'guest_count': policy.guests.filter(revoked_at__isnull=True).count() if policy else 0,
                'nda_text_set': bool(policy and (policy.nda_text or '').strip()),
                'default_nda': bool(policy and policy.require_nda),
                'default_download': bool(policy and policy.allow_download),
            },
        }

    @extend_schema(tags=['checkout'], responses={200: dict, 404: dict})
    def get(self, request, dataroom_document_id):
        dd = self._find(request, dataroom_document_id)
        if dd is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        policy = CheckoutPolicy.objects.filter(dataroom=dd.dataroom).first()
        entry = policy.files.filter(dataroom_document=dd).first() if policy else None
        return Response(self._body(dd, policy, entry))

    @extend_schema(tags=['checkout'], request=dict, responses={200: dict, 400: dict, 404: dict})
    def put(self, request, dataroom_document_id):
        dd = self._find(request, dataroom_document_id)
        if dd is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        def tri(value):
            return value if value in (True, False) else None

        policy, _ = CheckoutPolicy.objects.get_or_create(
            dataroom=dd.dataroom, defaults={'organization': dd.dataroom.organization, 'created_by': request.user})
        if request.data.get('requires_nda') is True and not (policy.nda_text or '').strip():
            return Response({"detail": "Write the datastore's NDA text (Check Out Center) before requiring it for a file."},
                            status=status.HTTP_400_BAD_REQUEST)
        entry, _ = CheckoutFile.objects.update_or_create(policy=policy, dataroom_document=dd, defaults={
            'visible': bool(request.data.get('visible')), 'requires_approval': bool(request.data.get('requires_approval')),
            'requires_nda': tri(request.data.get('requires_nda')), 'allow_download': tri(request.data.get('allow_download'))})
        return Response(self._body(dd, policy, entry))


class GuestSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = CheckoutGuest
        fields = ['id', 'email', 'created_at', 'revoked_at', 'is_active']
        read_only_fields = fields


class AdminGuestsView(APIView):
    """Assign guests (by e-mail address) to a datastore. New guests are told by e-mail."""
    permission_classes = [IsAdmin]

    @extend_schema(tags=['checkout'], responses={200: dict})
    def get(self, request, policy_id):
        policy = _policy(request, policy_id)
        return Response({'results': GuestSerializer(policy.guests.all(), many=True).data, 'policy_enabled': policy.enabled})

    @extend_schema(tags=['checkout'], request=dict, responses={201: dict, 400: dict})
    def post(self, request, policy_id):
        policy = _policy(request, policy_id)
        raw = request.data.get('emails')
        if not isinstance(raw, list) or not raw or len(raw) > 200:
            return Response({"detail": "'emails' must list 1 to 200 addresses."}, status=status.HTTP_400_BAD_REQUEST)
        from django.core.validators import validate_email
        valid, invalid = [], []
        for chunk in raw:
            for piece in EMAIL_SPLIT.split(str(chunk).strip()):
                if not piece:
                    continue
                email = piece.lower()
                try:
                    validate_email(email)
                except DjangoValidationError:
                    invalid.append(piece)
                    continue
                if email not in valid:
                    valid.append(email)
        if not valid:
            return Response({"detail": "No valid email addresses.", "invalid": invalid}, status=status.HTTP_400_BAD_REQUEST)
        added = []
        for email in valid:
            guest, created = CheckoutGuest.objects.get_or_create(policy=policy, email=email, defaults={'added_by': request.user})
            if not created and guest.revoked_at is not None:
                guest.revoked_at = None
                guest.save(update_fields=['revoked_at', 'updated_at'])
                created = True
            if created:
                added.append(guest)
                services.invite_guest(guest, request.user)
        body = {'added': GuestSerializer(added, many=True).data, 'invalid': invalid, 'policy_enabled': policy.enabled}
        if not policy.enabled:
            body['warning'] = 'This datastore is not enabled, so guests cannot sign in yet. Tick Enabled and save.'
        return Response(body, status=status.HTTP_201_CREATED)


class AdminGuestDetailView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(tags=['checkout'], responses={204: None, 404: dict})
    def delete(self, request, policy_id, guest_id):
        policy = _policy(request, policy_id)
        guest = get_object_or_404(CheckoutGuest, id=guest_id, policy=policy)
        if guest.revoked_at is None:
            guest.revoked_at = timezone.now()
            guest.save(update_fields=['revoked_at', 'updated_at'])
            # An open window ends with the access.
            Checkout.objects.filter(policy=policy, email=guest.email, status=Checkout.OPEN).update(
                status=Checkout.ENDED, expires_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)


class ApprovalSerializer(serializers.ModelSerializer):
    file_name = serializers.SerializerMethodField()
    datastore = serializers.CharField(source='policy.dataroom.name', read_only=True)
    decided_by_email = serializers.CharField(source='decided_by.email', read_only=True, default='')

    class Meta:
        model = Checkout
        fields = ['id', 'created_at', 'email', 'status', 'file_name', 'datastore', 'decided_at', 'decided_by_email']
        read_only_fields = fields

    def get_file_name(self, obj) -> str:
        return obj.file.dataroom_document.name or obj.file.dataroom_document.document.name


class AdminApprovalsView(APIView):
    """Requests for files that need approval: the waiting ones first, then recent decisions."""
    permission_classes = [IsAdmin]

    @extend_schema(tags=['checkout'], responses={200: dict})
    def get(self, request):
        base = Checkout.objects.filter(policy__organization=request.user.organization, file__requires_approval=True) \
            .select_related('policy__dataroom', 'file__dataroom_document__document', 'decided_by')
        return Response({
            'pending': ApprovalSerializer(base.filter(status=Checkout.PENDING).order_by('created_at'), many=True).data,
            'recent': ApprovalSerializer(base.exclude(status=Checkout.PENDING).order_by('-created_at')[:25], many=True).data,
        })


class AdminApprovalDecisionView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(tags=['checkout'], request=None, responses={200: dict, 404: dict, 409: dict})
    def post(self, request, checkout_id, decision):
        checkout = get_object_or_404(Checkout, id=checkout_id, policy__organization=request.user.organization)
        if decision not in ('approve', 'deny'):
            return Response({"detail": "Unknown decision."}, status=status.HTTP_404_NOT_FOUND)
        if not services.decide(checkout, decision == 'approve', request.user, request):
            return Response({"detail": "This request was already decided."}, status=status.HTTP_409_CONFLICT)
        return Response({'status': 'approved' if decision == 'approve' else 'denied'})


class EventSerializer(serializers.ModelSerializer):
    datastore = serializers.SerializerMethodField()

    class Meta:
        model = CheckoutEvent
        fields = ['id', 'created_at', 'event_type', 'email', 'ip_address', 'user_agent', 'metadata', 'datastore']
        read_only_fields = fields

    def get_datastore(self, obj):
        return obj.policy.dataroom.name if obj.policy_id else None


class AdminEventsView(APIView):
    """Check-out log. Filters: event_type (comma separated), email (contains), policy, since, until."""
    permission_classes = [IsAdmin]

    @extend_schema(tags=['checkout'], responses={200: EventSerializer(many=True)})
    def get(self, request):
        params = request.query_params
        qs = CheckoutEvent.objects.select_related('policy__dataroom').filter(
            Q(policy__isnull=True) | Q(policy__organization=request.user.organization))
        if params.get('event_type'):
            qs = qs.filter(event_type__in=[t.strip() for t in params['event_type'].split(',') if t.strip()])
        if params.get('email'):
            qs = qs.filter(email__icontains=params['email'].strip())
        if params.get('policy'):
            qs = qs.filter(policy_id=params['policy'])
        since = parse_datetime(params.get('since', '') or '')
        if since:
            qs = qs.filter(created_at__gte=since)
        until = parse_datetime(params.get('until', '') or '')
        if until:
            qs = qs.filter(created_at__lte=until)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(EventSerializer(page, many=True).data)


class AdminCheckoutListView(APIView):
    """Check-outs that have been opened, newest first, with whether a session report exists."""
    permission_classes = [IsAdmin]

    @extend_schema(tags=['checkout'], responses={200: dict})
    def get(self, request):
        from django.db.models import Count
        qs = Checkout.objects.filter(policy__organization=request.user.organization,
                                     status__in=[Checkout.OPEN, Checkout.ENDED], opened_at__isnull=False) \
            .select_related('policy__dataroom', 'file__dataroom_document__document') \
            .annotate(chunks=Count('trace_chunks')).order_by('-opened_at')
        if request.query_params.get('email'):
            qs = qs.filter(email__icontains=request.query_params['email'].strip())
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        rows = [{'id': str(c.id), 'email': c.email, 'file': services._file_name(c.file),
                 'datastore': c.policy.dataroom.name, 'status': c.status, 'opened_at': c.opened_at,
                 'expires_at': c.expires_at, 'has_report': c.chunks > 0} for c in page]
        return paginator.get_paginated_response(rows)


class AdminCheckoutReportView(APIView):
    """
    Report for one check-out: who, what, when, every logged event in the window, and the cursor/page analysis.
    It never includes the document's pages: it is drawn on blank pages of the right shape, so the report
    is not a way around the viewing protections.
    """
    permission_classes = [IsAdmin]

    @extend_schema(tags=['checkout'], responses={200: dict, 404: dict})
    def get(self, request, checkout_id):
        from datetime import timedelta
        from .trace_report import build_report
        checkout = get_object_or_404(
            Checkout.objects.select_related('policy__dataroom', 'file__dataroom_document__document'),
            id=checkout_id, policy__organization=request.user.organization)
        moves, views, clicks = [], [], []
        for chunk in checkout.trace_chunks.all():
            # Put the samples on the server's clock: the browser told us its clock when it sent the batch.
            m, v, c = chunk.moves, chunk.views, chunk.clicks
            received = int(chunk.created_at.timestamp() * 1000)
            newest = max([row[0] for row in m + v + c] or [0])
            base = received - (chunk.client_now if chunk.client_now is not None else newest)
            moves.extend([[row[0] + base] + row[1:] for row in m])
            views.extend([[row[0] + base] + row[1:] for row in v])
            clicks.extend([[row[0] + base] + row[1:] for row in c])
        document, version = _primary_version(checkout)
        sizes = {}
        if version and version.has_pages:
            for p in version.pages.order_by('page_number'):
                w, h = (p.metadata or {}).get('width'), (p.metadata or {}).get('height')
                if w and h:
                    sizes[p.page_number] = (w, h)
        start = checkout.opened_at or checkout.created_at
        end = (checkout.expires_at or timezone.now()) + timedelta(minutes=2)
        name = services._file_name(checkout.file)
        events = CheckoutEvent.objects.filter(policy=checkout.policy, email=checkout.email,
                                              created_at__gte=start - timedelta(minutes=1), created_at__lte=end) \
            .order_by('created_at')
        timeline = [{'at': e.created_at, 'seconds': max(0, int((e.created_at - start).total_seconds())),
                     'type': e.event_type, 'detail': {k: v for k, v in (e.metadata or {}).items() if k != 'file'}}
                    for e in events if (e.metadata or {}).get('file') in (None, name)]
        return Response({
            'checkout': {'id': str(checkout.id), 'email': checkout.email, 'file': name,
                         'datastore': checkout.policy.dataroom.name, 'status': checkout.status,
                         'opened_at': checkout.opened_at, 'expires_at': checkout.expires_at,
                         'ip_address': checkout.ip_address, 'downloads': checkout.downloads},
            'tracing_enabled': _tracing_enabled(),
            'timeline': timeline,
            'analysis': build_report(moves, views, sizes, clicks, [
                {'type': e['type'], 'ms': int(e['at'].timestamp() * 1000), 'seconds': e['seconds']} for e in timeline]),
        })
