import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { setViewerFocus } from '../services/api';

/**
 * `suspended` is true while the page must not show document pixels (not focused, or the
 * server has not yet acknowledged that the viewer is focused). PageCanvas drops what it drew
 * and fetches nothing while suspended.
 */
export const ViewingProtectionContext = createContext({ suspended: false });
export const useViewingProtection = () => useContext(ViewingProtectionContext);

const HEARTBEAT_MS = 5000;
const isInFocus = () => document.hasFocus() && !document.hidden;

/**
 * Focus lease for "blur when not focused". While enabled this keeps telling the server whether
 * the page is in focus; the server serves pages only against a fresh "focused" lease, so an
 * unfocused page receives nothing new, and `suspended` makes the viewer discard what it has.
 * (A viewer that tampers with its own JavaScript can keep claiming focus: this is a deterrent
 * against ordinary screen capture, not DRM.)
 */
export function useFocusLease(slug, enabled, reportFocus = setViewerFocus) {
  const [focused, setFocused] = useState(true);
  const [acknowledged, setAcknowledged] = useState(false);
  const latest = useRef(true);

  const report = useCallback(async () => {
    const now = isInFocus();
    latest.current = now;
    setFocused(now);
    try {
      await reportFocus(slug, now);
      if (latest.current === now) setAcknowledged(true);
    } catch {
      /* the next heartbeat retries; until then pages stay suspended */
      setAcknowledged(false);
    }
  }, [slug, reportFocus]);

  useEffect(() => {
    if (!enabled || !slug) return undefined;
    report();
    const timer = setInterval(report, HEARTBEAT_MS);
    window.addEventListener('blur', report);
    window.addEventListener('focus', report);
    document.addEventListener('visibilitychange', report);
    return () => {
      clearInterval(timer);
      window.removeEventListener('blur', report);
      window.removeEventListener('focus', report);
      document.removeEventListener('visibilitychange', report);
    };
  }, [enabled, slug, report]);

  return {
    focused: !enabled || focused,
    suspended: enabled && (!focused || !acknowledged),
    recheck: report,
  };
}
