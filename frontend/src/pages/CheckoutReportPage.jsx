import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PageTraceMap } from '../components/checkout/PageTraceMap';
import { getCheckoutReport } from '../services/api';
import { Button } from '../components/ui/Button';

const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
const when = (v) => (v ? new Date(v).toLocaleString() : '-');

/** Session report for one check-out: what happened, in order, and where the guest's cursor and attention went. */
export function CheckoutReportPage() {
  const { t } = useTranslation();
  const { checkoutId } = useParams();
  const [report, setReport] = useState(null);
  const [missing, setMissing] = useState(false);
  const [show, setShow] = useState({ heat: true, path: true, diagonal: true });

  useEffect(() => {
    let live = true;
    getCheckoutReport(checkoutId).then((r) => live && setReport(r.data)).catch(() => live && setMissing(true));
    return () => { live = false; };
  }, [checkoutId]);

  if (missing) return <div className="p-8">{t('report.missing', { defaultValue: 'This report was not found.' })}</div>;
  if (!report) return <div className="p-8 text-sm text-muted-foreground">…</div>;

  const { checkout, timeline, analysis } = report;
  const { basis, movement, pages, grid } = analysis;
  const windows = analysis.capture_windows || [];
  const straight = movement.straight || { horizontal: 0, vertical: 0, diagonal: 0 };
  const toggle = (key) => (e) => setShow({ ...show, [key]: e.target.checked });

  return (
    <div className="container mx-auto space-y-6 p-4 sm:p-6" data-testid="checkout-report">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t('report.title', { defaultValue: 'Session report' })}</h1>
          <p className="text-muted-foreground">{checkout.email} · {checkout.file} · {checkout.datastore}</p>
        </div>
        <Button asChild variant="outline"><Link to="/check-out-center">{t('report.back', { defaultValue: 'Back to Check Out Center' })}</Link></Button>
      </div>

      <dl className="grid grid-cols-2 gap-3 rounded-md border p-4 text-sm sm:grid-cols-4">
        <div><dt className="text-muted-foreground">Status</dt><dd>{checkout.status}</dd></div>
        <div><dt className="text-muted-foreground">Opened</dt><dd>{when(checkout.opened_at)}</dd></div>
        <div><dt className="text-muted-foreground">Ended / expires</dt><dd>{when(checkout.expires_at)}</dd></div>
        <div><dt className="text-muted-foreground">IP address</dt><dd>{checkout.ip_address || '-'}</dd></div>
      </dl>

      {!report.tracing_enabled && (
        <p role="note" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          {t('report.tracingOff', { defaultValue: 'Cursor tracing is switched off in Security settings, so no new movement is being recorded. Earlier sessions keep what was recorded.' })}
        </p>
      )}

      <section className="space-y-2" aria-label="Basis">
        <h2 className="text-lg font-medium">{t('report.basis', { defaultValue: 'How much this rests on' })}</h2>
        <p className="text-sm" data-testid="basis">
          {basis.cursor_samples} cursor samples, {basis.view_samples} screen samples. Confidence: <strong>{basis.confidence}</strong>.
        </p>
        <p className="text-sm text-muted-foreground">{basis.note}</p>
      </section>

      <section className="space-y-3" aria-label="Movement">
        <h2 className="text-lg font-medium">{t('report.movement', { defaultValue: 'Pointer movement' })}</h2>
        <p className="text-sm" data-testid="movement">
          Horizontal {movement.horizontal}, vertical {movement.vertical}, diagonal {movement.diagonal}
          {' '}({Math.round(movement.diagonal_share * 100)}% of strokes are diagonal).
        </p>
        <p className="text-sm" data-testid="straight">
          Straight runs (the pointer held a line): horizontal {straight.horizontal}, vertical {straight.vertical}, diagonal {straight.diagonal}.
        </p>
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={show.heat} onChange={toggle('heat')} /> Time the cursor spent (blue)</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={show.path} onChange={toggle('path')} /> Averaged path (green, dashed)</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={show.diagonal} onChange={toggle('diagonal')} /> Diagonal movements (orange)</label>
        </div>
        {pages.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('report.noMovement', { defaultValue: 'No cursor movement over the pages was recorded.' })}</p>
        ) : (
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            {pages.map((page) => (
              <figure key={page.page} className="space-y-2">
                <PageTraceMap page={page} grid={grid} show={show} />
                <figcaption className="text-sm">
                  Page {page.page}: on screen {fmt(Math.round(page.seconds_in_view))}, cursor over it {fmt(Math.round(page.seconds_cursor_over))};
                  {' '}diagonal strokes {page.segments.diagonal}
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-2" aria-label="Capture windows" data-testid="capture-windows">
        <h2 className="text-lg font-medium">{t('report.captureWindows', { defaultValue: 'Around print, save and screenshot keys' })}</h2>
        <p className="text-sm text-muted-foreground">
          {t('report.captureWindowsHint', { defaultValue: 'What the pointer did in the 5 seconds before and after each key. A browser sees only the key press, and not always that: the Fn key is invisible to it, and some operating-system shortcuts never reach the page. "Window probably lost focus" means no screen samples arrived for a few seconds after the key, as happens when a snipping tool opens. A hint, not proof.' })}
        </p>
        {windows.length === 0 ? <p className="text-sm text-muted-foreground">{t('report.noCaptureKeys', { defaultValue: 'No print, save or screenshot key was recorded.' })}</p> : (
          <ul className="divide-y rounded-md border text-sm">
            {windows.map((w, i) => (
              <li key={i} className="space-y-1 p-3" data-testid="capture-window">
                <div className="font-medium">{w.type} at {fmt(w.seconds ?? 0)}, page {w.page || '-'}
                  {w.focus_left_likely && <span className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900">window probably lost focus</span>}</div>
                {['before', 'after'].map((side) => (
                  <div key={side} className="text-muted-foreground">
                    {side === 'before' ? '5 s before' : '5 s after'}: {w[side].moves} pointer samples, {w[side].clicks} clicks,
                    {' '}straight runs h{w[side].straight.horizontal}/v{w[side].straight.vertical}/d{w[side].straight.diagonal}
                    {w[side].drags.length > 0 && `, press-and-drag: ${w[side].drags.map((d) => `${d.kind} (${d.ms} ms)`).join(', ')}`}
                  </div>
                ))}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-2" aria-label="Timeline">
        <h2 className="text-lg font-medium">{t('report.timeline', { defaultValue: 'Timeline' })}</h2>
        {timeline.length === 0 ? <p className="text-sm text-muted-foreground">-</p> : (
          <ol className="divide-y rounded-md border text-sm" data-testid="timeline">
            {timeline.map((e, i) => (
              <li key={i} className="flex gap-3 p-2">
                <span className="w-14 shrink-0 font-mono text-muted-foreground">{fmt(e.seconds)}</span>
                <span className="font-medium">{e.type}</span>
                <span className="truncate text-muted-foreground">{Object.entries(e.detail).map(([k, v]) => `${k}: ${v}`).join(', ')}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
