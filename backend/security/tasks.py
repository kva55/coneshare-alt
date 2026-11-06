from celery import shared_task

from .services import send_probe_digest


@shared_task
def flush_probe_digest():
    """Send the digest of probe events that were held back by the alert cool-down."""
    return send_probe_digest()
