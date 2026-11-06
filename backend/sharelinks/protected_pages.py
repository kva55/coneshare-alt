"""Server-side page protection: blur, cursor spotlight, and the focus lease.

Blurring is done here, on the server, so that for a link with the restricted viewing pane the
clear page is never sent to the browser at all: the viewer's page receives a blurred image, plus
small clear tiles around the cursor on request. The watermark is drawn after the blur, so it
stays sharp over the blurred page.

Limits (be honest about them): an authenticated viewer's browser still receives every pixel it
is shown, so a person can photograph the screen, and a script driving a valid session could
request enough tiles to rebuild a page. Tiles are rate limited and logged to make that slow
and visible, not impossible.
"""
import base64
import threading
import time
from collections import OrderedDict
from io import BytesIO

from django.conf import settings
from PIL import ImageFilter

FOCUS_SESSION_KEY = 'focus_leases'

# --------------------------------------------------------------------------- small, short-lived caches
# Cutting tiles used to fetch the page from the file service (over TLS, decrypting it), decode it and draw
# the watermark again for every request. These two caches remove that repeated work and nothing else:
#
#   * compressed page bytes, a couple of minutes, capped in total bytes (a page is ~100-300 KB);
#   * the final watermarked page image used for tiles, a few entries, per viewer (the key contains the
#     viewer's watermark text, so one viewer's image is never handed to another).
#
# Access checks always run before either cache is consulted. Both live only in the memory of the
# process (never on disk, never shared), expire quickly, and are switched off with 0.
# PAGE_SOURCE_CACHE_BYTES (default 32 MiB), PAGE_IMAGE_CACHE_ITEMS (default 3), PAGE_CACHE_SECONDS (default 120).
_SOURCE_CACHE = OrderedDict()   # key -> (expires, bytes)
_IMAGE_CACHE = OrderedDict()    # key -> (expires, image)
_CACHE_LOCK = threading.Lock()


def _ttl():
    return float(getattr(settings, 'PAGE_CACHE_SECONDS', 120))


def _purge(cache, now):
    for key in [k for k, (expires, _v) in cache.items() if expires <= now]:
        del cache[key]


def cached_source(key, fetch):
    """Page bytes for ``key``, from memory if fetched in the last PAGE_CACHE_SECONDS, else ``fetch()``."""
    budget = int(getattr(settings, 'PAGE_SOURCE_CACHE_BYTES', 32 * 1024 * 1024))
    if budget <= 0 or _ttl() <= 0:
        return fetch()
    now = time.monotonic()
    with _CACHE_LOCK:
        _purge(_SOURCE_CACHE, now)
        hit = _SOURCE_CACHE.get(key)
        if hit:
            _SOURCE_CACHE.move_to_end(key)
            return hit[1]
    data = fetch()
    if len(data) <= budget // 4:   # one huge page must not push everything else out
        with _CACHE_LOCK:
            _SOURCE_CACHE[key] = (now + _ttl(), data)
            _SOURCE_CACHE.move_to_end(key)
            while sum(len(v[1]) for v in _SOURCE_CACHE.values()) > budget:
                _SOURCE_CACHE.popitem(last=False)
    return data


def cached_image(key, build):
    """The finished (watermarked) page image for ``key``; ``build()`` runs only on a miss."""
    items = int(getattr(settings, 'PAGE_IMAGE_CACHE_ITEMS', 3))
    if items <= 0 or _ttl() <= 0:
        return build()
    now = time.monotonic()
    with _CACHE_LOCK:
        _purge(_IMAGE_CACHE, now)
        hit = _IMAGE_CACHE.get(key)
        if hit:
            _IMAGE_CACHE.move_to_end(key)
            return hit[1]
    image = build().convert('RGB')   # tiles are cut as RGB; keeping it that way is a quarter smaller
    with _CACHE_LOCK:
        _IMAGE_CACHE[key] = (now + _ttl(), image)
        _IMAGE_CACHE.move_to_end(key)
        while len(_IMAGE_CACHE) > items:
            _IMAGE_CACHE.popitem(last=False)
    return image


def clear_page_caches():
    with _CACHE_LOCK:
        _SOURCE_CACHE.clear()
        _IMAGE_CACHE.clear()


def blur_page(image, radius_ratio=None):
    """Return a heavily blurred copy of ``image`` (RGBA)."""
    ratio = settings.SPOTLIGHT_BLUR_RATIO if radius_ratio is None else radius_ratio
    radius = max(4.0, image.width * ratio)
    return image.convert('RGBA').filter(ImageFilter.GaussianBlur(radius))


# Tiles are cut on a fixed grid so a viewer that already holds a tile never has to ask for it
# again (the client keeps a small, bounded set of the most recent ones). The grid is a constant
# shared with frontend/src/components/documents/SpotlightLayer.jsx.
TILE_RATIO = 0.08          # tile edge as a share of the page width
MAX_TILES_PER_REQUEST = 16


def tile_size(width: int) -> int:
    return max(32, int(width * TILE_RATIO))


def render_tiles(image, tiles):
    """
    Cut the requested grid cells (column, row) out of ``image``. Returns a list of dicts
    ``{c, r, x, y, w, h, data}`` with ``data`` a base64 JPEG. Cells outside the page are skipped.
    """
    size = tile_size(image.width)
    rgb = image.convert('RGB')
    out = []
    for column, row in tiles:
        left, top = column * size, row * size
        if left >= rgb.width or top >= rgb.height or left < 0 or top < 0:
            continue
        box = (left, top, min(left + size, rgb.width), min(top + size, rgb.height))
        buffer = BytesIO()
        rgb.crop(box).save(buffer, format='JPEG', quality=85)
        out.append({
            'c': column, 'r': row, 'x': box[0], 'y': box[1],
            'w': box[2] - box[0], 'h': box[3] - box[1],
            'data': base64.b64encode(buffer.getvalue()).decode('ascii'),
        })
    return out


# ---- focus lease ---------------------------------------------------------------------------
# With "blur when not focused" the viewer's page must keep telling the server it is in focus.
# Pages are only served while that lease is fresh, so a page that loses focus stops receiving
# anything new (and the viewer discards what it already drew).

def set_focus_key(request, key: str, focused: bool) -> None:
    leases = dict(request.session.get(FOCUS_SESSION_KEY, {}))
    leases[key] = {'focused': bool(focused), 'at': time.time()}
    if len(leases) > 20:
        leases = dict(sorted(leases.items(), key=lambda item: item[1]['at'])[-10:])
    request.session[FOCUS_SESSION_KEY] = leases


def focus_lease_key_ok(request, key: str) -> bool:
    lease = request.session.get(FOCUS_SESSION_KEY, {}).get(key)
    if not lease or not lease.get('focused'):
        return False
    return time.time() - lease['at'] <= settings.FOCUS_LEASE_SECONDS


def set_focus(request, link, focused: bool) -> None:
    set_focus_key(request, str(link.id), focused)


def focus_lease_ok(request, link) -> bool:
    return True if not link.blur_when_unfocused else focus_lease_key_ok(request, str(link.id))
