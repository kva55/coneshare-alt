"""The single seam between /checkout and outbound integrations (the generic webhook).

``emit`` is called for every checkout event worth telling other systems about.
"""
import logging

logger = logging.getLogger(__name__)

FILE_REQUESTED = 'file_requested'      # a signed-in guest asked to check a file out
APPROVAL_REQUIRED = 'checkout_approval_required'  # ...and it waits for an admin
FILE_CHECKED_OUT = 'file_checked_out'  # the check-out was granted
CHECKOUT_DENIED = 'checkout_denied'    # the check-out was refused (limit reached, policy off)


def emit(event: str, payload: dict) -> None:
    """Hand the event to the generic webhook dispatcher (queued; never blocks or fails the request)."""
    try:
        from automations.tasks import dispatch_automation_event_task
        dispatch_automation_event_task.delay(event, payload)
    except Exception:
        logger.exception("Could not queue webhook event %s", event)
