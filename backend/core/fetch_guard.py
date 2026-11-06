"""Guards for endpoints that must only be loaded by the application's own JavaScript.

Document pages are delivered through POST endpoints that the viewer calls with ``fetch``
and paints into a ``<canvas>``. They are deliberately unusable as a plain URL: pasting
the address into the browser, using it as ``<img src>``, ``<iframe>`` or ``<object>``,
or following a link to it is refused.

How it works:

* the request must carry ``X-Page-Request: 1``. A custom header is never attached by
  navigations, ``<img>``/``<iframe>``/``<object>`` loads or cross-site form posts, and a
  cross-origin script cannot add it without passing a CORS preflight (which this
  application does not allow);
* browsers also send *Fetch Metadata* headers that page scripts cannot forge. When they are
  present, only ``Sec-Fetch-Dest: empty`` (fetch / XHR) from ``Sec-Fetch-Site: same-origin``
  is accepted, and ``Sec-Fetch-Mode: navigate`` is always refused.

Limits (be honest about them): a viewer who has legitimately passed every gate can still
capture what is displayed (screenshot, camera, or by replaying the request from their own
browser session). This guard removes the casual routes (save-as, open in tab, copy image
address, hot-linking); it is not DRM.
"""
PAGE_REQUEST_HEADER = 'X-Page-Request'


def is_script_fetch(request) -> bool:
    """Return True only for same-origin requests issued by page JavaScript."""
    if request.headers.get(PAGE_REQUEST_HEADER) != '1':
        return False

    mode = request.headers.get('Sec-Fetch-Mode')
    if mode is not None and mode in ('navigate', 'no-cors', 'websocket'):
        return False

    dest = request.headers.get('Sec-Fetch-Dest')
    if dest is not None and dest != 'empty':
        return False

    site = request.headers.get('Sec-Fetch-Site')
    if site is not None and site != 'same-origin':
        return False

    return True


def no_store_headers(response):
    """Mark a response as private, uncacheable and not sniffable; returns the response."""
    response['Cache-Control'] = 'no-store, private, max-age=0'
    response['Pragma'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cross-Origin-Resource-Policy'] = 'same-origin'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


def sniff_image_content_type(data: bytes) -> str:
    """Return the image MIME type for the leading bytes (JPEG/PNG/WEBP), else octet-stream."""
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return 'application/octet-stream'
