/**
 * Viewer behaviour signals, reported to the access log (share links and check-outs alike).
 *
 * Each detector is a small independent module: `{ id, eventType, attach(emit) -> detach }`.
 * To add a trigger append one entry to DETECTORS; nothing else changes.
 *
 * Reliability notes (these were reasons events used to be missed):
 * - keys are matched on `e.code` (the physical key) as well as `e.key`, so layouts, Caps Lock and
 *   non-Latin keyboards do not hide a Ctrl+S / Ctrl+P / F12;
 * - all key listeners run in the capture phase, so nothing in the page can swallow the event;
 * - the only de-duplication is a short window that merges a key's down/up pair and a double
 *   event, so repeated attempts (two right-clicks in a row) are all kept.
 *
 * What a browser cannot see: menu-driven actions (File > Save, OS screenshot tools such as the
 * macOS and Windows snipping tools, phone screenshots, a camera). These are signals, not
 * protection, and the absence of a signal proves nothing.
 */

import { automationSignals, tamperFailures } from './integrity';

const mod = (e) => e.ctrlKey || e.metaKey;
const is = (e, key, code) => e.key?.toLowerCase() === key || e.code === code;

const isPrintShortcut = (e) => mod(e) && !e.altKey && is(e, 'p', 'KeyP');
const isPrintScreen = (e) => e.key === 'PrintScreen' || e.key === 'Snapshot' || e.code === 'PrintScreen' || e.keyCode === 44;
// Common OS screenshot chords: Win+Shift+S, macOS Cmd+Shift+3/4/5.
const isScreenshotChord = (e) =>
  (e.metaKey && e.shiftKey && (['3', '4', '5'].includes(e.key) || ['Digit3', 'Digit4', 'Digit5'].includes(e.code) || is(e, 's', 'KeyS'))) ||
  (e.shiftKey && is(e, 's', 'KeyS') && e.getModifierState?.('OS'));
const isSaveShortcut = (e) => mod(e) && !e.altKey && !e.shiftKey && is(e, 's', 'KeyS');
const isDevtoolsKey = (e) =>
  e.key === 'F12' || e.code === 'F12' ||
  (mod(e) && e.shiftKey && (is(e, 'i', 'KeyI') || is(e, 'j', 'KeyJ') || is(e, 'c', 'KeyC'))) ||
  (mod(e) && !e.shiftKey && is(e, 'u', 'KeyU'));

// Which modifiers were held. Fn is not among them: browsers never see it (the keyboard turns Fn+PrtSc into a
// plain PrintScreen), so Fn+PrintScreen is counted as PrintScreen. Alt+PrintScreen copies only the active
// window; Win/Cmd variants may save a file directly.
const modifierVariant = (e) => {
  const held = [e.altKey && 'alt', e.ctrlKey && 'ctrl', e.metaKey && 'meta', e.shiftKey && 'shift'].filter(Boolean);
  return held.length ? held.join('+') : 'plain';
};

const keys = (id, eventType, matches, metadata, types = ['keydown']) => ({
  id,
  eventType,
  attach(emit) {
    const handler = (e) => { if (!e.repeat && matches(e)) emit(metadata(e)); };   // a held key is one attempt
    types.forEach((t) => window.addEventListener(t, handler, true));
    return () => types.forEach((t) => window.removeEventListener(t, handler, true));
  },
});

const on = (id, eventType, target, name, metadata = () => ({}), capture = false) => ({
  id,
  eventType,
  attach(emit) {
    const handler = (e) => emit(metadata(e));
    target().addEventListener(name, handler, capture);
    return () => target().removeEventListener(name, handler, capture);
  },
});

export const DETECTORS = [
  on('print-event', 'print_attempt', () => window, 'beforeprint', () => ({ via: 'beforeprint' })),
  keys('print-shortcut', 'print_attempt', isPrintShortcut, () => ({ via: 'shortcut' })),
  // PrintScreen often only produces a keyup, so listen to both; the window merges the pair.
  // `phase` shows whether the press was seen (keydown) or only its release (keyup): Windows often swallows the press.
  keys('screenshot-key', 'screenshot_key', (e) => isPrintScreen(e) || isScreenshotChord(e),
    (e) => ({ key: e.key || e.code || 'PrintScreen', variant: modifierVariant(e), phase: e.type }), ['keydown', 'keyup']),
  keys('save-shortcut', 'save_attempt', isSaveShortcut, () => ({})),
  keys('devtools-keys', 'devtools_key', isDevtoolsKey, (e) => ({ key: e.key })),
  on('copy', 'copy_attempt', () => document, 'copy', () => ({ via: 'copy' }), true),
  on('cut', 'copy_attempt', () => document, 'cut', () => ({ via: 'cut' }), true),
  on('context-menu', 'context_menu', () => window, 'contextmenu', () => ({}), true),
  on('drag', 'drag_attempt', () => document, 'dragstart', () => ({}), true),
  on('window-blur', 'window_blur', () => window, 'blur'),
  // Developer tools actually open (not just the key): the window is much smaller than its frame (docked tools), or
  // the console evaluates an object the page logged. Chrome-family only for the second; an undocked window
  // or Firefox can slip past both, and a large sidebar can trip the first, so this is a signal.
  {
    id: 'devtools-open',
    eventType: 'devtools_open',
    attach(emit) {
      const isPhone = () => /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent || '');
      let open = false;
      const probe = () => {
        if (isPhone()) return;
        let via = '';
        if (window.outerWidth - window.innerWidth > 200 || window.outerHeight - window.innerHeight > 200) via = 'size';
        else {
          let hit = false;
          const bait = new Image();
          Object.defineProperty(bait, 'id', { get() { hit = true; return ''; } });
          // eslint-disable-next-line no-console
          console.debug(bait);
          if (hit) via = 'console';
        }
        if (via && !open) emit({ via });
        open = Boolean(via);
      };
      const timer = setInterval(probe, 1500);
      return () => clearInterval(timer);
    },
  },
  // Integrity: patched browser functions and automation flags. Each finding is reported once per page visit.
  {
    id: 'tamper',
    eventType: 'tamper_detected',
    attach(emit) {
      const seen = new Set();
      const run = () => {
        const fresh = tamperFailures().filter((name) => !seen.has(name));
        if (fresh.length) { fresh.forEach((n) => seen.add(n)); emit({ checks: fresh.join(',') }); }
      };
      const first = setTimeout(run, 1500);
      const again = setInterval(run, 60000);
      return () => { clearTimeout(first); clearInterval(again); };
    },
  },
  {
    id: 'automation',
    eventType: 'automation_detected',
    attach(emit) {
      const first = setTimeout(() => {
        const found = automationSignals();
        if (found.length) emit({ signals: found.join(',') });
      }, 1500);
      return () => clearTimeout(first);
    },
  },
  {
    id: 'tab-hidden',
    eventType: 'tab_hidden',
    attach(emit) {
      const handler = () => { if (document.visibilityState === 'hidden') emit({}); };
      document.addEventListener('visibilitychange', handler);
      return () => document.removeEventListener('visibilitychange', handler);
    },
  },
];

/**
 * Start all detectors. The same event type within `minIntervalMs` is merged (a key's down/up pair,
 * a doubled event); anything slower is reported, every time.
 * @returns {() => void} stop function
 */
// Used until the server has said what to do (and by older servers): only log, and skip routine focus changes.
export const FALLBACK_ACTIONS = { tab_hidden: 'ignore', window_blur: 'ignore' };

/**
 * `actions` ({signal: ignore | log | notify | blur | end}) is what an administrator configured. An ignored
 * signal is not even reported. After reporting, `onAction(action, signal)` is called for the actions the
 * page has to carry out itself (`blur`, `end`); the server records `notify` and `end` too.
 */
export function startViewerEvents(report, { detectors = DETECTORS, minIntervalMs = 250, now = () => Date.now(), actions = null, onAction } = {}) {
  const last = new Map();
  const detach = detectors.map((d) =>
    d.attach((metadata) => {
      const action = (actions || FALLBACK_ACTIONS)[d.eventType] ?? 'log';
      if (action === 'ignore') return;
      const t = now();
      if (t - (last.get(d.eventType) ?? -Infinity) < minIntervalMs) return;
      last.set(d.eventType, t);
      report(d.eventType, metadata);
      if (action === 'blur' || action === 'end') onAction?.(action, d.eventType);
    })
  );
  return () => detach.forEach((fn) => fn());
}
