import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getInteractions } from '../services/api';
import { Button } from '../components/ui/Button';

export const PRESETS = [
  { label: 'Attempts', filter: 'attempt' },
  { label: 'Print', filter: 'event == print_attempt' },
  { label: 'Screenshot keys', filter: 'event == screenshot_key' },
  { label: 'Save page', filter: 'event == save_attempt' },
  { label: 'Right-click', filter: 'event == context_menu' },
  { label: 'Inspect / source', filter: 'event == devtools_key' },
  { label: 'Check-outs', filter: 'source == checkout' },
  { label: 'Denied', filter: 'event in {auth_denied access_request_denied checkout_denied request_denied}' },
  { label: 'Last 24 h', filter: 'time >= -24h' },
];

const quote = (value) => `"${String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;

/** `field == "value"` (or `!=`) added to the current filter with &&, as "apply as filter" in Wireshark. */
export const addClause = (current, field, value, op = '==') => {
  const clause = `${field} ${op} ${quote(value)}`;
  const trimmed = current.trim();
  if (!trimmed) return clause;
  return /\|\|/.test(trimmed) ? `(${trimmed}) && ${clause}` : `${trimmed} && ${clause}`;
};

const ATTEMPTS = new Set(['print_attempt', 'screenshot_key', 'save_attempt', 'devtools_key', 'copy_attempt', 'context_menu', 'drag_attempt', 'devtools_open', 'tamper_detected', 'automation_detected', 'non_browser_request', 'tiles_without_cursor']);
const DENIED = /denied|throttled|invalid|failed|limit/;

function Cell({ field, value, onFilter, mono = false }) {
  if (value === '' || value == null) return <span className="text-gray-400">-</span>;
  return (
    <button
      type="button"
      title="Click: filter to this value. Alt-click: exclude it."
      className={`max-w-[16rem] truncate text-left hover:underline ${mono ? 'font-mono' : ''}`}
      onClick={(e) => onFilter(field, value, e.altKey ? '!=' : '==')}
    >
      {value}
    </button>
  );
}

/**
 * Interaction Activity: everything that happened around share links and check-outs, across every
 * datastore and file, narrowed with a display filter (field, comparison, && || !, parentheses).
 */
export function InteractionActivityPage() {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [applied, setApplied] = useState('');
  const [data, setData] = useState({ results: [], counts: {}, total: 0, has_more: false, next_before: null, fields: [] });
  const [error, setError] = useState(null);
  const [auto, setAuto] = useState(false);
  const [loading, setLoading] = useState(false);
  const request = useRef(0);

  const load = useCallback(async (expression, { append = false, before = null } = {}) => {
    const ticket = ++request.current;
    setLoading(true);
    try {
      const { data: body } = await getInteractions({ filter: expression, limit: 100, ...(before ? { before } : {}) });
      if (ticket !== request.current) return;
      setError(null);
      setData((previous) => (append ? { ...body, results: [...previous.results, ...body.results] } : body));
    } catch (err) {
      if (ticket !== request.current) return;
      const body = err.response?.data;
      setError({ message: body?.filter || t('interactions.failed', { defaultValue: 'Could not load the interactions.' }), position: body?.position ?? null });
    } finally {
      if (ticket === request.current) setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(applied); }, [applied, load]);
  useEffect(() => {
    if (!auto) return undefined;
    const timer = setInterval(() => load(applied), 10000);
    return () => clearInterval(timer);
  }, [auto, applied, load]);

  const apply = (expression = text) => { setText(expression); setApplied(expression.trim()); };
  const refine = (field, value, op) => apply(addClause(text, field, value, op));

  return (
    <div className="container mx-auto space-y-5 p-4 sm:p-6">
      <div>
        <h1 className="text-2xl font-bold">{t('interactions.title', { defaultValue: 'Interaction Activity' })}</h1>
        <p className="text-muted-foreground">
          {t('interactions.subtitle', { defaultValue: 'Everything that happened around your share links and file requests, across all datastores and files.' })}
        </p>
      </div>

      <form onSubmit={(e) => { e.preventDefault(); apply(); }} className="space-y-2" role="search">
        <div className="flex gap-2">
          <input
            aria-label="Filter"
            aria-invalid={Boolean(error)}
            spellCheck={false}
            autoComplete="off"
            className={`flex-1 rounded-md border px-3 py-2 font-mono text-sm ${error ? 'border-red-500 bg-red-50 dark:bg-red-950/20' : 'border-green-600/60 bg-green-50 dark:bg-green-950/10'}`}
            placeholder='event == "print_attempt" && email contains "guest"'
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <Button type="submit">{t('interactions.apply', { defaultValue: 'Apply' })}</Button>
          <Button type="button" variant="outline" onClick={() => apply('')}>{t('interactions.clear', { defaultValue: 'Clear' })}</Button>
        </div>
        {error && (
          <p role="alert" className="font-mono text-xs text-red-700">
            {error.message}{error.position != null ? ` (at character ${error.position + 1})` : ''}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2" aria-label="Presets">
          {PRESETS.map((p) => (
            <button key={p.label} type="button" className="rounded-full border px-3 py-1 text-xs hover:bg-gray-100 dark:hover:bg-gray-800" onClick={() => apply(p.filter)}>
              {p.label}
            </button>
          ))}
          <label className="ml-auto flex items-center gap-2 text-xs">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            {t('interactions.live', { defaultValue: 'Live (every 10 s)' })}
          </label>
        </div>
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">{t('interactions.help', { defaultValue: 'Filter syntax' })}</summary>
          <div className="mt-2 space-y-1 font-mono">
            <div>fields: {(data.fields.length ? data.fields : ['event', 'category', 'source', 'email', 'ip', 'datastore', 'file', 'link', 'time', 'attempt']).join(', ')}</div>
            <div>comparison: == != contains startswith &nbsp; {'in {a b c}'} &nbsp; time: &gt; &gt;= &lt; &lt;=</div>
            <div>logic: && (and) || (or) ! (not) ( )</div>
            <div>time: 2026-01-31 · 2026-01-31T10:00 · -30m -24h -7d</div>
            <div>click a value in the table to filter on it; Alt-click to exclude it</div>
          </div>
        </details>
      </form>

      <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="counts">
        <span className="font-medium">{t('interactions.matching', { count: data.total, defaultValue: '{{count}} matching' })}</span>
        {Object.entries(data.counts).sort((a, b) => b[1] - a[1]).map(([event, n]) => (
          <button key={event} type="button" className="rounded-full border px-2 py-0.5 hover:bg-gray-100 dark:hover:bg-gray-800"
                  onClick={() => refine('event', event)}>
            {event} <span className="font-semibold">{n}</span>
          </button>
        ))}
      </div>

      <div className="overflow-auto rounded-md border">
        <table className="min-w-full text-xs">
          <thead className="bg-gray-50 text-left uppercase text-gray-500 dark:bg-gray-800/50">
            <tr>{['Time', 'Source', 'Event', 'Email', 'IP', 'Datastore', 'File', 'Link', 'Details'].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}</tr>
          </thead>
          <tbody className="divide-y" data-testid="interaction-rows">
            {data.results.map((r) => (
              <tr key={`${r.source}-${r.id}`}
                  className={ATTEMPTS.has(r.event) ? 'bg-red-50 dark:bg-red-950/20' : DENIED.test(r.event) ? 'bg-amber-50 dark:bg-amber-950/20' : ''}>
                <td className="whitespace-nowrap px-2 py-1">{new Date(r.time).toLocaleString()}</td>
                <td className="px-2 py-1"><Cell field="source" value={r.source} onFilter={refine} /></td>
                <td className="px-2 py-1 font-medium"><Cell field="event" value={r.event} onFilter={refine} /></td>
                <td className="px-2 py-1"><Cell field="email" value={r.email} onFilter={refine} /></td>
                <td className="px-2 py-1"><Cell field="ip" value={r.ip} onFilter={refine} mono /></td>
                <td className="px-2 py-1"><Cell field="datastore" value={r.datastore} onFilter={refine} /></td>
                <td className="px-2 py-1"><Cell field="file" value={r.file} onFilter={refine} /></td>
                <td className="px-2 py-1"><Cell field="link" value={r.link} onFilter={refine} /></td>
                <td className="px-2 py-1 font-mono text-gray-500">
                  {Object.keys(r.detail || {}).length ? Object.entries(r.detail).map(([k, v]) => `${k}=${v}`).join(' ') : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && data.results.length === 0 && !error && (
          <p className="p-4 text-sm text-muted-foreground">{t('interactions.empty', { defaultValue: 'Nothing matches.' })}</p>
        )}
      </div>

      {data.has_more && (
        <Button variant="outline" onClick={() => load(applied, { append: true, before: data.next_before })} disabled={loading}>
          {t('interactions.more', { defaultValue: 'Load more' })}
        </Button>
      )}
    </div>
  );
}
