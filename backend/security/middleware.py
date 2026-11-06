import logging

from . import services

logger = logging.getLogger(__name__)


class ProbeDetectionMiddleware:
    """Log requests that look like probing (OWASP CRS style rules, see security/engine.py). Never blocks."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            hits, authenticated = services.collect_request(request)   # before the view: a small body can still be read
        except Exception:
            logger.exception('Probe detection failed')
            hits, authenticated = [], False
        response = self.get_response(request)
        try:
            cfg = services.snapshot()
            if cfg['probe_detection_enabled'] and cfg['probe_log_unknown_areas'] and not (
                    authenticated and not cfg['probe_log_authenticated']):
                hits = hits + services.unknown_area_hit(request.path, response.status_code)
            if hits:
                services.record_probe(hits, request=request, path=request.path,
                                      query=request.META.get('QUERY_STRING', ''), authenticated=authenticated)
        except Exception:  # detection must never break a request
            logger.exception('Probe detection failed')
        return response
