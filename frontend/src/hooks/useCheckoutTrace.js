import { useEffect, useRef } from 'react';
import { sendCheckoutTrace } from '../services/api';

const MOVE_EVERY_MS = 100;     // cursor sampling rate
const VIEW_EVERY_MS = 1000;    // which page is at the centre of the screen
const FLUSH_EVERY_MS = 10000;
const MAX_QUEUE = 600;         // matches the server's per-request limit

/**
 * Records where the cursor is (as a position within the page under it) and which page is at the centre of
 * the scroll area, for the check-out session report. Positions are fractions of the page (0..1000), never
 * pixels, and nothing is typed or read from the page. Only runs while `enabled` (the server says whether
 * tracing is on) and the tab is visible and focused.
 */
export function useCheckoutTrace(checkoutId, enabled, scrollNode) {
  const state = useRef({ moves: [], views: [], clicks: [], last: 0 });

  useEffect(() => {
    if (!enabled || !checkoutId || !scrollNode) return undefined;
    const s = state.current;
    s.moves = []; s.views = []; s.clicks = []; s.last = 0;
    // Milliseconds on the page's own clock. Each batch also sends the clock's value when it was sent, so the
    // server can place these samples on its own clock and line them up with logged events.
    const now = () => Math.round(performance.now());
    const active = () => document.visibilityState === 'visible' && document.hasFocus();

    const flush = () => {
      if (!s.moves.length && !s.views.length && !s.clicks.length) return;
      const body = { moves: s.moves.slice(0, MAX_QUEUE), views: s.views.slice(0, MAX_QUEUE), clicks: s.clicks.slice(0, MAX_QUEUE), now: now() };
      s.moves = s.moves.slice(MAX_QUEUE); s.views = s.views.slice(MAX_QUEUE); s.clicks = s.clicks.slice(MAX_QUEUE);
      sendCheckoutTrace(checkoutId, body).catch(() => {});
    };

    const pageAt = (target) => target?.closest?.('[data-page-number]') ?? null;
    const onMove = (e) => {
      // touch screens have no mouse: a finger drag counts as the pointer
      if (e.touches) {
        const t = e.touches[0];
        if (!t) return;
        e = { target: e.target, clientX: t.clientX, clientY: t.clientY };
      }
      const t = now();
      if (!active() || t - s.last < MOVE_EVERY_MS) return;
      s.last = t;
      const el = pageAt(e.target);
      if (!el) { s.moves.push([t, 0, 0, 0]); return; }
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return;
      const x = Math.round(((e.clientX - r.left) / r.width) * 1000);
      const y = Math.round(((e.clientY - r.top) / r.height) * 1000);
      s.moves.push([t, Number(el.dataset.pageNumber), Math.min(1000, Math.max(0, x)), Math.min(1000, Math.max(0, y))]);
      if (s.moves.length >= MAX_QUEUE) flush();
    };

    // Left button down and up, with where on the page: a click, or the two ends of a press-and-drag stroke.
    const onButton = (kind) => (e) => {
      if (e.button !== 0 || !active()) return;
      const el = pageAt(e.target);
      if (!el) return;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return;
      const x = Math.round(((e.clientX - r.left) / r.width) * 1000);
      const y = Math.round(((e.clientY - r.top) / r.height) * 1000);
      s.clicks.push([now(), Number(el.dataset.pageNumber), Math.min(1000, Math.max(0, x)), Math.min(1000, Math.max(0, y)), kind]);
      if (s.clicks.length >= MAX_QUEUE) flush();
    };
    const onDown = onButton(0);
    const onUp = onButton(1);

    const sampleView = () => {
      if (!active()) return;
      const box = scrollNode.getBoundingClientRect();
      const cy = box.top + box.height / 2;
      for (const el of scrollNode.querySelectorAll('[data-page-number]')) {
        const r = el.getBoundingClientRect();
        if (cy >= r.top && cy <= r.bottom && r.height) {
          s.views.push([now(), Number(el.dataset.pageNumber), Math.round(((cy - r.top) / r.height) * 1000)]);
          return;
        }
      }
      s.views.push([now(), 0, 0]);
    };

    scrollNode.addEventListener('mousemove', onMove, { passive: true });
    scrollNode.addEventListener('touchmove', onMove, { passive: true });
    scrollNode.addEventListener('mousedown', onDown, { passive: true });
    scrollNode.addEventListener('mouseup', onUp, { passive: true });
    const viewTimer = setInterval(sampleView, VIEW_EVERY_MS);
    const flushTimer = setInterval(flush, FLUSH_EVERY_MS);
    return () => {
      scrollNode.removeEventListener('mousemove', onMove);
      scrollNode.removeEventListener('touchmove', onMove);
      scrollNode.removeEventListener('mousedown', onDown);
      scrollNode.removeEventListener('mouseup', onUp);
      clearInterval(viewTimer);
      clearInterval(flushTimer);
      flush();
    };
  }, [checkoutId, enabled, scrollNode]);
}
