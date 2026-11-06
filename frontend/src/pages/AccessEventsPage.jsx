import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft } from 'lucide-react';
import { getAccessEvents } from '../services/api';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { EVENT_LABELS, FILTERS } from '../components/links/ShareLinkAccessLog';

/** Organization-wide access log: access attempts, requests (allowed / denied) and viewer behaviour. */
export function AccessEventsPage() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState('');
  const [email, setEmail] = useState('');
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);

  const load = useCallback(async () => {
    const params = { ...(FILTERS.find((f) => f.value === filter)?.params ?? {}), page };
    if (email.trim()) params.email = email.trim();
    try {
      const response = await getAccessEvents(params);
      setData(response.data);
    } catch {
      /* handled by the global interceptor */
    }
  }, [filter, email, page]);

  useEffect(() => { load(); }, [load]);

  const results = data?.results ?? [];
  return (
    <div className="container mx-auto p-4 sm:p-6">
      <div className="mb-8">
        <Button asChild variant="outline">
          <Link to="/" className="flex items-center gap-2">
            <ArrowLeft className="h-4 w-4" />
            <span>{t('common.backToDashboard')}</span>
          </Link>
        </Button>
      </div>
      <h1 className="text-2xl font-bold">{t('accessEvents.title', { defaultValue: 'Access activity' })}</h1>
      <p className="text-muted-foreground">
        {t('accessEvents.subtitle', { count: data?.count ?? 0, defaultValue: '{{count}} events across your links' })}
      </p>
      <div className="mt-6 flex flex-wrap gap-2">
        <select
          aria-label={t('accessLog.filter', { defaultValue: 'Filter events' })}
          className="rounded-md border bg-transparent p-2 text-sm"
          value={filter}
          onChange={(e) => { setFilter(e.target.value); setPage(1); }}
        >
          {FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
        <Input
          className="max-w-xs"
          placeholder={t('accessEvents.emailFilter', { defaultValue: 'Filter by email' })}
          value={email}
          onChange={(e) => { setEmail(e.target.value); setPage(1); }}
        />
      </div>
      <div className="mt-4 overflow-auto rounded-md border">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500 dark:bg-gray-800/50">
            <tr>
              <th className="px-3 py-2">{t('accessLog.time', { defaultValue: 'Time' })}</th>
              <th className="px-3 py-2">{t('accessLog.event', { defaultValue: 'Event' })}</th>
              <th className="px-3 py-2">{t('accessLog.email', { defaultValue: 'Email' })}</th>
              <th className="px-3 py-2">IP</th>
            </tr>
          </thead>
          <tbody className="divide-y" data-testid="access-events-rows">
            {results.map((event) => (
              <tr key={event.id}>
                <td className="whitespace-nowrap px-3 py-2">{new Date(event.created_at).toLocaleString()}</td>
                <td className="px-3 py-2">{EVENT_LABELS[event.event_type] ?? event.event_type}</td>
                <td className="px-3 py-2">{event.email || '-'}</td>
                <td className="px-3 py-2 font-mono">{event.ip_address || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {results.length === 0 && (
          <p className="p-4 text-sm text-muted-foreground">{t('accessLog.empty', { defaultValue: 'No events yet.' })}</p>
        )}
      </div>
      <div className="mt-4 flex gap-2">
        <Button variant="outline" disabled={!data?.previous} onClick={() => setPage((p) => Math.max(1, p - 1))}>
          {t('common.previous', { defaultValue: 'Previous' })}
        </Button>
        <Button variant="outline" disabled={!data?.next} onClick={() => setPage((p) => p + 1)}>
          {t('common.next', { defaultValue: 'Next' })}
        </Button>
      </div>
    </div>
  );
}
