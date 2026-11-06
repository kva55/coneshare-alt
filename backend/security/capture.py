"""What the viewer does when it detects an attempt (print, screenshot key, save, inspect, ...).

Detection is only a signal from the viewer's browser; this table is the *response*, set by an
administrator (Admin > Security). The defaults only log, so nothing interrupts anyone unless you choose.

Actions
  ignore  do not even report the signal (and do not log it)
  log     record it in the access log (default)
  notify  record it and e-mail the alert address (at most one e-mail per viewer and signal every 10 minutes)
  blur    record it and cover the page until the viewer clicks to continue
  end     record it and end the viewing: a check-out is ended, a share-link viewer is locked out for a while
"""
from sharelinks.events import CLIENT_EVENT_TYPES

CAPTURE_ACTIONS = ('ignore', 'log', 'notify', 'blur', 'end')
CAPTURE_EVENTS = tuple(sorted(CLIENT_EVENT_TYPES))

# window focus changes are routine (switching tabs); everything else defaults to log
DEFAULT_CAPTURE_ACTIONS = {event: ('ignore' if event in ('tab_hidden', 'window_blur') else 'log') for event in CAPTURE_EVENTS}

LABELS = {
    'print_attempt': 'Print (Ctrl+P, print dialog)', 'screenshot_key': 'Screenshot key (PrintScreen, Cmd+Shift+3/4/5)',
    'save_attempt': 'Save page (Ctrl+S)', 'devtools_key': 'Inspect / view source (F12, Ctrl+Shift+I, Ctrl+U)',
    'copy_attempt': 'Copy or cut', 'context_menu': 'Right-click', 'drag_attempt': 'Dragging the page image',
    'tab_hidden': 'Tab hidden', 'window_blur': 'Window lost focus',
    'devtools_open': 'Developer tools open', 'tamper_detected': 'Browser functions patched (fetch, canvas...)', 'automation_detected': 'Automated browser (webdriver, headless)',
}


def effective_actions(configured: dict) -> dict:
    """The action for every signal: configured, otherwise the default. All 'ignore' while monitoring is off."""
    from .services import snapshot
    if not snapshot()['monitoring_enabled']:
        return {event: 'ignore' for event in CAPTURE_EVENTS}
    return {event: (configured or {}).get(event, DEFAULT_CAPTURE_ACTIONS[event]) for event in CAPTURE_EVENTS}


# ---------------------------------------------------------------------------- doing the response
import time

from django.core.cache import cache

LOCK_KEY = 'capture_lockouts'


def lock(request, key: str, minutes: int) -> None:
    """Lock this browser session out of ``key`` (e.g. ``link:<id>``) for ``minutes``."""
    locks = {k: v for k, v in dict(request.session.get(LOCK_KEY, {})).items() if v > time.time()}
    locks[key] = time.time() + minutes * 60
    request.session[LOCK_KEY] = locks


def locked_until(request, key: str):
    until = request.session.get(LOCK_KEY, {}).get(key)
    return until if until and until > time.time() else None


def respond(action: str, *, request, event_type: str, who: str, where: str) -> None:
    """The e-mail part of ``notify``/``end`` (rate limited per viewer and signal); the rest is done by the caller."""
    from .services import send_alert, snapshot
    cfg = snapshot()
    if action not in ('notify', 'end') or not cfg['alert_group_email']:
        return
    if not cache.add(f"security:capture_notify:{who or 'anonymous'}:{event_type}", 1, 600):
        return
    send_alert(f"Viewer alert: {LABELS.get(event_type, event_type)}",
               [f"Signal: {LABELS.get(event_type, event_type)}", f"Viewer: {who or 'anonymous'}", f"Where: {where}",
                f"Action taken: {'viewing ended' if action == 'end' else 'logged and reported'}", '',
                "Reported by the viewer's browser; it shows what the page saw, not proof of anything else."],
               [cfg['alert_group_email']])
