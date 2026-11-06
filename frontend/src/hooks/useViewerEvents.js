import { useCallback, useEffect, useState } from 'react';
import {
  beaconCheckoutEvent,
  beaconViewerEvent,
  reportCheckoutEvent,
  reportViewerEvent,
} from '../services/api';
import { startViewerEvents } from '../lib/viewerEvents';

const MAX_TRIES = 4;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Delivers behaviour signals reliably: events go into a queue that is sent in order, a failed send
 * is retried a few times with a short back-off, and when the page is hidden or closed whatever is
 * still queued is handed to the browser's beacon API so it survives the page going away
 * (a Save dialog, a screenshot tool, closing the tab).
 */
export function useEventReporter({ send, beacon, enabled, actions = null, onAction }) {
  useEffect(() => {
    if (!enabled) return undefined;
    const queue = [];
    let flushing = false;
    let alive = true;

    const flush = async () => {
      if (flushing) return;
      flushing = true;
      while (alive && queue.length) {
        const item = queue[0];
        try {
          await send(item.type, item.metadata);
          queue.shift();
        } catch {
          item.tries = (item.tries || 0) + 1;
          if (item.tries >= MAX_TRIES) queue.shift();
          else await sleep(400 * item.tries);
        }
      }
      flushing = false;
    };

    const stop = startViewerEvents((type, metadata) => {
      queue.push({ type, metadata });
      flush();
    }, { actions, onAction });

    const handOver = () => {
      if (!beacon || !queue.length) return;
      queue.splice(0).forEach((item) => beacon(item.type, item.metadata));
    };
    const onVisibility = () => { if (document.visibilityState === 'hidden') handOver(); };
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', handOver);

    return () => {
      alive = false;
      stop();
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', handOver);
      handOver();
    };
  }, [send, beacon, enabled, actions, onAction]);
}

/** Share-link viewer: report signals for `slug` while `enabled` (content is on screen). */
export function useViewerEvents(slug, enabled, { actions = null, onAction } = {}) {
  const send = useCallback((type, metadata) => reportViewerEvent(slug, type, metadata), [slug]);
  const beacon = useCallback((type, metadata) => beaconViewerEvent(slug, type, metadata), [slug]);
  useEventReporter({ send, beacon, enabled: Boolean(slug) && enabled, actions, onAction });
}

/** Check-out viewer: the same signals, recorded against the check-out (and its file). */
export function useCheckoutViewerEvents(checkoutId, enabled, { actions = null, onAction } = {}) {
  const send = useCallback((type, metadata) => reportCheckoutEvent(checkoutId, type, metadata), [checkoutId]);
  const beacon = useCallback((type, metadata) => beaconCheckoutEvent(checkoutId, type, metadata), [checkoutId]);
  useEventReporter({ send, beacon, enabled: Boolean(checkoutId) && enabled, actions, onAction });
}

/**
 * What the page itself does when a signal's configured action is `blur` (cover the page until the viewer
 * clicks to continue) or `end` (stop showing the file; the server has already ended or locked it).
 */
export function useCaptureResponse() {
  const [blurred, setBlurred] = useState(false);
  const [ended, setEnded] = useState(false);
  const onAction = useCallback((action) => {
    if (action === 'blur') setBlurred(true);
    if (action === 'end') setEnded(true);
  }, []);
  const resume = useCallback(() => setBlurred(false), []);
  return { blurred, ended, resume, onAction };
}
