"""Request throttling with limits you can see and change (Admin > Security). Off unless switched on there.

The old global limit was 1000 requests per DAY per address for every anonymous caller. A document viewer
asks for a page, a tile, a focus report or a behaviour signal many times a minute, and everybody behind
one address (a lab, an office) shared that bucket, so ordinary scrolling got "Request was throttled".

* viewing traffic (pages, tiles, focus, signals, the check-out and share-link viewer calls) has its own
  per-minute limit, counted per browser session when there is one;
* everything else keeps a general anonymous per-minute limit;
* the message says which limit was hit, how long to wait and where the limit is set.
"""
from rest_framework.exceptions import Throttled
from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle, UserRateThrottle
from rest_framework.views import exception_handler as drf_exception_handler

from .services import snapshot


class _Switch:
    """Every throttle here does nothing until throttling is switched on in the security settings."""

    def allow_request(self, request, view):
        if not snapshot()['throttling_enabled']:
            return True
        return super().allow_request(request, view)


class ViewerThrottle(_Switch, SimpleRateThrottle):
    scope = 'viewer'

    def get_rate(self):
        return f"{snapshot()['viewer_requests_per_minute']}/min"

    def get_cache_key(self, request, view):
        session = getattr(request, 'session', None)
        ident = (session.session_key if session is not None and session.session_key else None) or self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class GeneralUserThrottle(_Switch, UserRateThrottle):
    scope = 'user'

    def get_rate(self):
        return f"{snapshot()['user_requests_per_minute']}/min"


class GeneralAnonThrottle(_Switch, AnonRateThrottle):
    scope = 'anon'

    def get_rate(self):
        return f"{snapshot()['general_requests_per_minute']}/min"


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None and isinstance(exc, Throttled):
        wait = int(exc.wait or 0)
        cfg = snapshot()
        if cfg['throttling_enabled']:
            detail = (f"Too many requests. Try again in {wait} seconds. Request throttling is switched on: viewing allows "
                      f"{cfg['viewer_requests_per_minute']} requests a minute, signed-in users {cfg['user_requests_per_minute']} "
                      f"and everything else {cfg['general_requests_per_minute']}. An administrator can change or switch this "
                      f"off under Security.")
        else:   # one of the fixed protections (password attempts, copying) rather than the optional throttling
            detail = f"Too many attempts. Try again in {wait} seconds."
        response.data = {'detail': detail, 'code': 'throttled', 'retry_after': wait}
    return response
