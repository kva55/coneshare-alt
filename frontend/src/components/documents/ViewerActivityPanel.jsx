import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getDocumentViewerActivity, getLinkViewerActivity } from '../../services/api';

// The ones that matter most come first; the rest are behind a switch.
export const PRIMARY_COLUMNS = [
  { type: 'print_attempt', label: 'Print' },
  { type: 'screenshot_key', label: 'Print Screen' },
  { type: 'save_attempt', label: 'Save page' },
  { type: 'devtools_key', label: 'Inspect / source' },
  { type: 'devtools_open', label: 'Devtools open' },
  { type: 'tamper_detected', label: 'Tampering' },
];
export const SECONDARY_COLUMNS = [
  { type: 'copy_attempt', label: 'Copy' },
  { type: 'context_menu', label: 'Right-click' },
  { type: 'drag_attempt', label: 'Drag image' },
  { type: 'tab_hidden', label: 'Tab hidden' },
  { type: 'window_blur', label: 'Focus lost' },
];
export const COLUMNS = [...PRIMARY_COLUMNS, ...SECONDARY_COLUMNS];

export const formatSeconds = (total) => {
  if (!total) return '0s';
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h && `${h}h`, (h || m) && `${m}m`, `${s}s`].filter(Boolean).join(' ');
};

/**
 * Who looked, for how long, and what they tried to do to the page (print, screenshot key, save,
 * inspect, copy...). Pass `linkId` for one link or `documentId` for all of a document's links.
 * These are signals reported by the viewer's browser: they show what was reported, not proof of
 * what did not happen.
 */
export function ViewerActivityPanel({ linkId, documentId }) {
  const { t } = useTranslation();
  const [rows, setRows] = useState(null);
  const [showOther, setShowOther] = useState(false);
  const columns = showOther ? COLUMNS : PRIMARY_COLUMNS;

  useEffect(() => {
    let active = true;
    const request = linkId ? getLinkViewerActivity(linkId) : getDocumentViewerActivity(documentId);
    Promise.resolve(request)
      .then((response) => active && setRows(response?.data?.results ?? []))
      .catch(() => active && setRows([]));
    return () => { active = false; };
  }, [linkId, documentId]);

  if (rows === null) return null;

  return (
    <section className="space-y-3" aria-label="Viewer activity">
      <div>
        <h2 className="text-lg font-medium">{t('viewerActivity.title', { defaultValue: 'Viewer activity' })}</h2>
        <p className="text-sm text-muted-foreground">
          {t('viewerActivity.hint', { defaultValue: 'Time spent, and attempts to print, capture, save or inspect the page. Reported by the viewer\'s browser.' })}
        </p>
      </div>
      {rows.length > 0 && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={showOther} onChange={(e) => setShowOther(e.target.checked)} />
          {t('viewerActivity.showOther', { defaultValue: 'Show other signals (copy, right-click, drag, tab and focus changes)' })}
        </label>
      )}
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('viewerActivity.empty', { defaultValue: 'No viewers yet.' })}</p>
      ) : (
        <div className="overflow-auto rounded-md border">
          <table className="min-w-full text-sm" data-testid="viewer-activity">
            <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500 dark:bg-gray-800/50">
              <tr>
                <th className="px-3 py-2">{t('viewerActivity.viewer', { defaultValue: 'Viewer' })}</th>
                <th className="px-3 py-2">{t('viewerActivity.via', { defaultValue: 'Via' })}</th>
                <th className="px-3 py-2">{t('viewerActivity.time', { defaultValue: 'Time spent' })}</th>
                <th className="px-3 py-2">{t('viewerActivity.sessions', { defaultValue: 'Visits' })}</th>
                {columns.map((c) => <th key={c.type} className="px-3 py-2">{c.label}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((row) => (
                <tr key={`${row.source || 'link'}:${row.email || 'anonymous'}`} className={row.attempts > 0 ? 'bg-amber-50 dark:bg-amber-950/20' : ''}>
                  <td className="px-3 py-2">{row.email || t('viewerActivity.anonymous', { defaultValue: 'Anonymous' })}</td>
                  <td className="whitespace-nowrap px-3 py-2" data-testid="via">
                    {row.source === 'checkout'
                      ? t('viewerActivity.viaCheckout', { defaultValue: 'Check-out' })
                      : t('viewerActivity.viaLink', { defaultValue: 'Share link' })}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">{formatSeconds(row.seconds)}</td>
                  <td className="px-3 py-2">{row.sessions}</td>
                  {columns.map((c) => (
                    <td key={c.type} className={`px-3 py-2 ${row.counts[c.type] ? 'font-semibold' : 'text-gray-400'}`}>
                      {row.counts[c.type] || '–'}
                      {c.type === 'screenshot_key' && row.screenshot_variants && (
                        <span className="block text-xs font-normal text-muted-foreground" data-testid="screenshot-variants">
                          {Object.entries(row.screenshot_variants).map(([k, n]) => `${k} ${n}`).join(', ')}
                        </span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
