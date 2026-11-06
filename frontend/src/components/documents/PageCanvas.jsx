import { useEffect, useRef, useState } from 'react';
import { blobToBitmap, paintBitmap } from '../../lib/pageBitmap';
import { useViewingProtection } from '../../lib/viewingProtection';
import { SpotlightLayer } from './SpotlightLayer';

// Used until the real page size is known (older documents were rendered without metadata).
const DEFAULT_WIDTH = 1000;
const DEFAULT_ASPECT = 8.5 / 11;

/**
 * Renders one document page into a <canvas>.
 *
 * `loadPage(pageNumber, { signal })` must resolve to the page image Blob. Pages are fetched
 * only when they are near the viewport and their pixel buffers are released again when they
 * are far away, so long documents do not exhaust memory. The image is never attached to the
 * DOM as a URL, and the usual "open / save / drag image" routes are disabled.
 */
export function PageCanvas({ loadPage, loadTiles, pageNumber, width, height, alt, className = '', scrollContainer, onPainted, fluid = false, children }) {
  const { suspended } = useViewingProtection();
  const wrapperRef = useRef(null);
  const canvasRef = useRef(null);
  const [near, setNear] = useState(false);
  const [status, setStatus] = useState('idle'); // 'idle' | 'loading' | 'ready' | 'error'
  const [attempt, setAttempt] = useState(0);
  const [dims, setDims] = useState(null);

  useEffect(() => {
    if (!scrollContainer || !wrapperRef.current) return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[entries.length - 1];
        setNear(Boolean(entry?.isIntersecting));
      },
      { root: scrollContainer, rootMargin: '1200px 0px' }
    );
    observer.observe(wrapperRef.current);
    return () => observer.disconnect();
  }, [scrollContainer]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!near || suspended) {
      // Release the pixel buffer of pages that are far from the viewport, or while the viewer
      // is suspended (not in focus): nothing stays drawn and nothing is requested.
      if (canvas && canvas.width > 1) {
        canvas.width = 1;
        canvas.height = 1;
      }
      setStatus('idle');
      return undefined;
    }

    let cancelled = false;
    const controller = new AbortController();
    setStatus('loading');

    (async () => {
      try {
        const blob = await loadPage(pageNumber, { signal: controller.signal });
        const bitmap = await blobToBitmap(blob);
        if (cancelled) {
          bitmap.close?.();
          return;
        }
        paintBitmap(canvas, bitmap);
        setDims({ width: bitmap.width, height: bitmap.height });
        bitmap.close?.();
        setStatus('ready');
        onPainted?.(pageNumber, canvas);
      } catch (error) {
        if (!cancelled && !controller.signal.aborted) {
          setStatus('error');
        }
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [near, suspended, loadPage, pageNumber, attempt, onPainted]);

  const intrinsicWidth = dims?.width || width || DEFAULT_WIDTH;
  const intrinsicHeight = dims?.height || height || Math.round(intrinsicWidth / DEFAULT_ASPECT);
  const ready = status === 'ready';

  return (
    <div
      ref={wrapperRef}
      className={`relative mx-auto w-full overflow-hidden ${className}`}
      style={{ maxWidth: fluid ? 'none' : intrinsicWidth, aspectRatio: `${intrinsicWidth} / ${intrinsicHeight}` }}
      data-testid={`page-canvas-${pageNumber}`}
    >
      {!ready && (
        <div
          className="absolute inset-0 flex items-center justify-center bg-gray-200 text-sm text-gray-500 dark:bg-gray-700 dark:text-gray-300"
          role={status === 'error' ? 'alert' : undefined}
        >
          {status === 'error' ? (
            <button
              type="button"
              className="rounded border border-gray-400 px-3 py-1 hover:bg-gray-300 dark:hover:bg-gray-600"
              onClick={() => setAttempt((count) => count + 1)}
            >
              Failed to load page {pageNumber}. Retry
            </button>
          ) : (
            <span className="animate-pulse">Loading page {pageNumber}…</span>
          )}
        </div>
      )}
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={alt}
        draggable={false}
        onContextMenu={(event) => event.preventDefault()}
        onDragStart={(event) => event.preventDefault()}
        className={ready ? 'block h-auto w-full select-none' : 'hidden'}
        style={{ WebkitTouchCallout: 'none', WebkitUserSelect: 'none', userSelect: 'none' }}
      />
      {/* Overlays (link hotspots, text layer, viewer effects) stay aligned with the page. */}
      {ready && loadTiles && dims && (
        <SpotlightLayer loadTiles={loadTiles} pageNumber={pageNumber} pageSize={dims} wrapperRef={wrapperRef} />
      )}
      {children}
    </div>
  );
}
