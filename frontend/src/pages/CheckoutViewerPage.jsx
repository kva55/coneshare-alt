import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Minus, Plus } from 'lucide-react';
import { downloadCheckout, fetchCheckoutPageBlob, fetchCheckoutTiles, getCheckoutDetail, returnCheckout, setCheckoutFocus } from '../services/api';
import { PageCanvas } from '../components/documents/PageCanvas';
import { useCaptureResponse, useCheckoutViewerEvents } from '../hooks/useViewerEvents';
import { useCheckoutTrace } from '../hooks/useCheckoutTrace';
import { FocusBlurOverlay } from '../components/viewer/FocusBlurOverlay';
import { ViewingProtectionContext, useFocusLease } from '../lib/viewingProtection';
import { Button } from '../components/ui/Button';

const ZOOM_MIN = 0.5;
const ZOOM_MAX = 3;
const ZOOM_STEP = 0.25;
const BASE_WIDTH = 800; // page width in px at 100%

const secondsLeft = (expiresAt) => Math.max(0, Math.floor((new Date(expiresAt) - Date.now()) / 1000));

export function Countdown({ expiresAt, onDone }) {
  const [left, setLeft] = useState(() => secondsLeft(expiresAt));
  useEffect(() => {
    const timer = setInterval(() => {
      const next = secondsLeft(expiresAt);
      setLeft(next);
      if (next === 0) onDone?.();
    }, 1000);
    return () => clearInterval(timer);
  }, [expiresAt, onDone]);
  return (
    <span data-testid="countdown" className="font-mono">
      {String(Math.floor(left / 60)).padStart(2, '0')}:{String(left % 60).padStart(2, '0')}
    </span>
  );
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * An open check-out, viewed in the app: pages are fetched by script and painted into canvases
 * (there is no link to share, and no file URL). The window is timed; "Check in" ends it early.
 */
export function CheckoutViewerPage() {
  const { t } = useTranslation();
  const { checkoutId } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState(null);
  const [state, setState] = useState('loading'); // loading | ready | ended
  const [scrollNode, setScrollNode] = useState(null);
  const [error, setError] = useState('');
  const [zoom, setZoom] = useState(1);
  // Print, save, screenshot keys, inspect, right-click... are recorded against this check-out.
  const capture = useCaptureResponse();
  useCheckoutViewerEvents(checkoutId, state === 'ready', { actions: detail?.capture_actions, onAction: capture.onAction });

  useCheckoutTrace(checkoutId, state === 'ready' && Boolean(detail?.trace), scrollNode);

  const previewState = detail?.preview?.state ?? (detail?.pages?.length ? 'ready' : 'unsupported');
  const [waitedLong, setWaitedLong] = useState(false);

  useEffect(() => {
    let active = true;
    getCheckoutDetail(checkoutId)
      .then(({ data }) => { if (active) { setDetail(data); setState('ready'); } })
      .catch(() => active && setState('ended'));
    return () => { active = false; };
  }, [checkoutId]);

  // A file whose preview is still being prepared is checked again until the pages exist.
  useEffect(() => {
    if (state !== 'ready' || previewState !== 'processing') return undefined;
    const started = Date.now();
    const timer = setInterval(() => {
      if (Date.now() - started > 60000) setWaitedLong(true);
      getCheckoutDetail(checkoutId).then(({ data }) => setDetail(data)).catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [state, previewState, checkoutId]);

  // Protections set on the file (blur when unfocused, restricted viewing pane) are enforced by the server;
  // this only keeps the viewer in step with them.
  const blurWhenUnfocused = Boolean(detail?.protections?.blur_when_unfocused);
  const restrictedPane = Boolean(detail?.protections?.restricted_viewing_pane);
  const focusLease = useFocusLease(checkoutId, state === 'ready' && blurWhenUnfocused, setCheckoutFocus);
  const protection = { suspended: focusLease.suspended || capture.blurred };
  const loadTiles = useCallback((pageNumber, options) => fetchCheckoutTiles(checkoutId, pageNumber, options), [checkoutId]);

  const zoomBy = (delta) => setZoom((z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round((z + delta) * 100) / 100)));

  const loadPage = useCallback((pageNumber, options) => fetchCheckoutPageBlob(checkoutId, pageNumber, options), [checkoutId]);
  const finish = useCallback(() => setState('ended'), []);

  const checkIn = async () => {
    try { await returnCheckout(checkoutId); } catch { /* the window ends anyway */ }
    navigate('/checkout');
  };

  const download = async () => {
    setError('');
    try {
      const { blob, filename } = await downloadCheckout(checkoutId);
      saveBlob(blob, filename);
      setDetail({ ...detail, allow_download: false });
    } catch (err) {
      setError(err.response?.data?.detail || t('checkout.downloadFailed', { defaultValue: 'Could not download this file.' }));
    }
  };

  if (state === 'loading') return <div className="p-8 text-center text-sm text-gray-500">…</div>;

  if (capture.ended) {
    return (
      <div className="mx-auto max-w-md space-y-4 p-8 text-center">
        <p role="alert">{t('checkout.endedByAction', { defaultValue: 'This viewing was ended because of an action that is not allowed here.' })}</p>
        <Button asChild><Link to="/checkout">{t('checkout.backToFiles', { defaultValue: 'Back to your files' })}</Link></Button>
      </div>
    );
  }

  if (state === 'ended') {
    return (
      <div className="mx-auto max-w-md space-y-4 p-8 text-center">
        <p>{t('checkout.windowOver', { defaultValue: 'This check-out has ended. Request the file again if you still need it.' })}</p>
        <Button asChild><Link to="/checkout">{t('checkout.backToFiles', { defaultValue: 'Back to your files' })}</Link></Button>
      </div>
    );
  }

  return (
    <ViewingProtectionContext.Provider value={protection}>
    <div className="flex h-screen flex-col bg-gray-100 dark:bg-gray-900">
      <header className="flex items-center justify-between gap-3 border-b bg-white px-4 py-2 dark:bg-gray-800">
        <div className="min-w-0 truncate font-medium">{detail.name}</div>
        <div className="flex shrink-0 items-center gap-3 text-sm">
          {detail.pages.length > 0 && (
            <div className="flex items-center gap-1" role="group" aria-label="Zoom">
              <Button size="icon" variant="outline" aria-label="Zoom out" disabled={zoom <= ZOOM_MIN} onClick={() => zoomBy(-ZOOM_STEP)}><Minus className="h-4 w-4" /></Button>
              <button type="button" className="w-14 text-center font-mono text-xs hover:underline" aria-label="Fit width" title="Reset to 100%" onClick={() => setZoom(1)} data-testid="zoom-level">
                {Math.round(zoom * 100)}%
              </button>
              <Button size="icon" variant="outline" aria-label="Zoom in" disabled={zoom >= ZOOM_MAX} onClick={() => zoomBy(ZOOM_STEP)}><Plus className="h-4 w-4" /></Button>
            </div>
          )}
          <span>{t('checkout.timeLeft', { defaultValue: 'Time left' })} <Countdown expiresAt={detail.expires_at} onDone={finish} /></span>
          {detail.allow_download && <Button size="sm" variant="outline" onClick={download}>{t('checkout.download', { defaultValue: 'Download' })}</Button>}
          <Button size="sm" onClick={checkIn}>{t('checkout.checkIn', { defaultValue: 'Check in' })}</Button>
        </div>
      </header>
      {error && <p role="alert" className="bg-red-50 px-4 py-2 text-sm text-red-700">{error}</p>}
      <div ref={setScrollNode} className="flex-1 space-y-4 overflow-auto p-4" onContextMenu={(e) => e.preventDefault()}>
        {detail.pages.length === 0 ? (
          <p className="p-8 text-center text-sm text-gray-500" data-testid="preview-state">
            {previewState === 'processing' && t('checkout.preparing', { defaultValue: 'This file is still being prepared for viewing. It will appear here in a moment.' })}
            {previewState === 'processing' && waitedLong && ` ${t('checkout.preparingLong', { defaultValue: 'This is taking longer than usual: the preview job may not be running. Ask an administrator to check the background worker.' })}`}
            {previewState === 'failed' && t('checkout.previewFailed', { defaultValue: 'The preview of this file could not be prepared. Ask the person who shared it to rebuild the preview.' })}
            {previewState !== 'processing' && previewState !== 'failed' && t('checkout.noPreview', { defaultValue: 'This file type cannot be viewed in the browser.' })}
            {previewState === 'unsupported' && detail.allow_download && ` ${t('checkout.useDownload', { defaultValue: 'Use Download to open it.' })}`}
          </p>
        ) : (
          scrollNode && (
            <div className="mx-auto space-y-4" data-testid="pages"
                 style={{ width: `${Math.round(BASE_WIDTH * zoom)}px`, maxWidth: zoom <= 1 ? '100%' : 'none' }}>
              {detail.pages.map((page) => (
                <div key={page.page_number} data-page-number={page.page_number}>
                  <PageCanvas loadPage={loadPage} loadTiles={restrictedPane ? loadTiles : undefined}
                              pageNumber={page.page_number} width={page.width} height={page.height} alt={`Page ${page.page_number}`}
                              className="rounded-md shadow-md" scrollContainer={scrollNode} fluid />
                </div>
              ))}
            </div>
          )
        )}
      </div>
      <FocusBlurOverlay visible={(blurWhenUnfocused && !focusLease.focused) || capture.blurred}
                        onResume={() => { capture.resume(); focusLease.recheck(); }} />
    </div>
    </ViewingProtectionContext.Provider>
  );
}
