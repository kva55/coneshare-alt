import { useEffect, useRef } from 'react';
import { blobToBitmap } from '../../lib/pageBitmap';

// Must match backend/sharelinks/protected_pages.py (TILE_RATIO, MAX_TILES_PER_REQUEST).
export const TILE_RATIO = 0.08;
export const MAX_TILES_PER_REQUEST = 16;
export const RADIUS_RATIO = 0.1; // clear circle, as a share of the page width
export const MAX_CACHED_TILES = 64; // most recent tiles kept; older ones are dropped

const FEATHER = 0.3; // share of the radius over which the circle fades out

export const tileSizeFor = (pageWidth) => Math.max(32, Math.floor(pageWidth * TILE_RATIO));

/** Grid cells [column, row] that a circle at (cx, cy) with radius r touches. */
export function cellsForCircle(cx, cy, r, pageWidth, pageHeight) {
  const size = tileSizeFor(pageWidth);
  const cols = Math.ceil(pageWidth / size);
  const rows = Math.ceil(pageHeight / size);
  const c0 = Math.max(0, Math.floor((cx - r) / size));
  const c1 = Math.min(cols - 1, Math.floor((cx + r) / size));
  const r0 = Math.max(0, Math.floor((cy - r) / size));
  const r1 = Math.min(rows - 1, Math.floor((cy + r) / size));
  const cells = [];
  for (let row = r0; row <= r1; row += 1) for (let col = c0; col <= c1; col += 1) cells.push([col, row]);
  return cells;
}

/**
 * Restricted viewing pane. The page underneath is already blurred by the server; this layer
 * shows a clear circle around the cursor assembled from tiles. Tiles are requested once and kept
 * (the most recent MAX_CACHED_TILES), so moving around the same area costs no requests, and a
 * move only asks for the cells it has not seen. Nothing sharp exists in the browser beyond
 * those tiles.
 *
 * `loadTiles(pageNumber, { tiles: [[c, r], ...], signal })` resolves to
 * `{ tiles: [{ c, r, x, y, blob }] }`. `pageSize` is the painted page's pixel size.
 */
export function SpotlightLayer({ loadTiles, pageNumber, pageSize, wrapperRef }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    const canvas = canvasRef.current;
    if (!wrapper || !canvas || !pageSize) return undefined;

    canvas.width = pageSize.width;
    canvas.height = pageSize.height;
    const context = canvas.getContext('2d');
    const radius = pageSize.width * RADIUS_RATIO;
    const cache = new Map(); // "c,r" -> { bitmap, x, y }
    const pending = new Set();
    const controller = new AbortController();
    let cursor = null;
    let frame = 0;
    let alive = true;

    const key = (c, r) => `${c},${r}`;

    const draw = () => {
      frame = 0;
      context.globalCompositeOperation = 'source-over';
      context.clearRect(0, 0, canvas.width, canvas.height);
      if (!cursor) return;
      for (const [c, r] of cellsForCircle(cursor.x, cursor.y, radius, pageSize.width, pageSize.height)) {
        const tile = cache.get(key(c, r));
        if (tile) context.drawImage(tile.bitmap.source || tile.bitmap, tile.x, tile.y);
      }
      // Keep only the feathered circle: everything outside it is erased again.
      const gradient = context.createRadialGradient(cursor.x, cursor.y, radius * (1 - FEATHER), cursor.x, cursor.y, radius);
      gradient.addColorStop(0, 'rgba(0,0,0,1)');
      gradient.addColorStop(1, 'rgba(0,0,0,0)');
      context.globalCompositeOperation = 'destination-in';
      context.fillStyle = gradient;
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.globalCompositeOperation = 'source-over';
    };

    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(draw);
    };

    const remember = (tile, bitmap) => {
      const k = key(tile.c, tile.r);
      cache.get(k)?.bitmap.close?.();
      cache.delete(k);
      cache.set(k, { bitmap, x: tile.x, y: tile.y });
      while (cache.size > MAX_CACHED_TILES) {
        const oldest = cache.keys().next().value;
        cache.get(oldest)?.bitmap.close?.();
        cache.delete(oldest);
      }
    };

    const fetchMissing = async () => {
      if (!cursor) return;
      const missing = cellsForCircle(cursor.x, cursor.y, radius, pageSize.width, pageSize.height)
        .filter(([c, r]) => !cache.has(key(c, r)) && !pending.has(key(c, r)));
      for (let i = 0; i < missing.length; i += MAX_TILES_PER_REQUEST) {
        const batch = missing.slice(i, i + MAX_TILES_PER_REQUEST);
        batch.forEach(([c, r]) => pending.add(key(c, r)));
        loadTiles(pageNumber, { tiles: batch, signal: controller.signal })
          .then(async ({ tiles }) => {
            for (const tile of tiles) {
              const bitmap = await blobToBitmap(tile.blob);
              if (!alive) { bitmap.close?.(); return; }
              remember(tile, bitmap);
            }
            schedule();
          })
          .catch(() => { /* refused, rate limited or aborted: the page simply stays blurred there */ })
          .finally(() => batch.forEach(([c, r]) => pending.delete(key(c, r))));
      }
    };

    const move = (clientX, clientY) => {
      const box = wrapper.getBoundingClientRect();
      const fx = (clientX - box.left) / box.width;
      const fy = (clientY - box.top) / box.height;
      if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return;
      cursor = { x: fx * pageSize.width, y: fy * pageSize.height };
      schedule();
      fetchMissing();
    };

    const leave = () => { cursor = null; schedule(); };
    const onMouse = (e) => move(e.clientX, e.clientY);
    const onTouch = (e) => e.touches[0] && move(e.touches[0].clientX, e.touches[0].clientY);
    wrapper.addEventListener('mousemove', onMouse, { passive: true });
    wrapper.addEventListener('touchmove', onTouch, { passive: true });
    wrapper.addEventListener('mouseleave', leave);
    return () => {
      alive = false;
      controller.abort();
      if (frame) cancelAnimationFrame(frame);
      wrapper.removeEventListener('mousemove', onMouse);
      wrapper.removeEventListener('touchmove', onTouch);
      wrapper.removeEventListener('mouseleave', leave);
      cache.forEach((tile) => tile.bitmap.close?.());
      cache.clear();
      context.clearRect(0, 0, canvas.width, canvas.height);
    };
  }, [loadTiles, pageNumber, pageSize, wrapperRef]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      data-testid={`spotlight-${pageNumber}`}
      className="pointer-events-none absolute inset-0 h-full w-full"
    />
  );
}
