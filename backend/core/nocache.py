"""Nothing the application sends is meant to be stored by a browser or a proxy.

``NoStoreMiddleware`` marks every dynamic response ``no-store, private``: API answers, error pages, page
images, reports. A response that already chose its own Cache-Control keeps it (a view may be stricter), and
the paths in ``NO_STORE_EXEMPT_PREFIXES`` (static files and media such as logos, which carry no document or
personal data) are left to their server. ``no-store`` also keeps a page out of the browser's back/forward
cache, so pressing Back after signing out does not show a document again.

``clear_site_cache`` adds ``Clear-Site-Data: "cache"`` to a response (used on sign-out), which asks the browser
to drop what it cached for this site. It is a best-effort extra: Safari ignores it.
"""
from django.conf import settings

NO_STORE = 'no-store, private, max-age=0'


def mark_no_store(response):
    response['Cache-Control'] = NO_STORE
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def clear_site_cache(response):
    response['Clear-Site-Data'] = '"cache"'
    return response


class NoStoreMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        exempt = getattr(settings, 'NO_STORE_EXEMPT_PREFIXES', ())
        if 'Cache-Control' in response or request.path.startswith(tuple(exempt)):
            return response
        return mark_no_store(response)
