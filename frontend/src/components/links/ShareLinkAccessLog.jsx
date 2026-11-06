import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getShareLinkEvents } from '../../services/api';

export const EVENT_LABELS = {
  access_attempt: 'Access attempt',
  access_request: 'Access request',
  access_request_allowed: 'Request allowed',
  access_request_denied: 'Request denied (not on list)',
  access_request_throttled: 'Request throttled',
  access_granted: 'Access granted',
  access_code_invalid: 'Invalid code',
  invitation_sent: 'Invitation sent',
  access_revoked: 'Access revoked',
  print_attempt: 'Print attempt',
  copy_attempt: 'Copy attempt',
  context_menu: 'Context menu',
  screenshot_key: 'Screenshot key',
  tab_hidden: 'Tab hidden',
  devtools_open: 'Developer tools open',
  tamper_detected: 'Browser tampering',
  automation_detected: 'Automation detected',
  non_browser_request: 'Not the viewer script',
  tiles_without_cursor: 'Tiles without cursor',
};

export const FILTERS = [
  { value: '', label: 'All events', params: {} },
  { value: 'attempts', label: 'Access attempts', params: { event_type: 'access_attempt' } },
  { value: 'requests', label: 'Access requests', params: { event_type: 'access_request,access_request_throttled' } },
  { value: 'allowed', label: 'Requests (allowed)', params: { event_type: 'access_request_allowed,access_granted' } },
  { value: 'denied', label: 'Requests (denied)', params: { event_type: 'access_request_denied,access_code_invalid' } },
  { value: 'behavior', label: 'Viewer behaviour', params: { category: 'behavior' } },
];

/** The access log of one share link, newest first. */
export function ShareLinkAccessLog({ linkId }) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState('');
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = FILTERS.find((f) => f.value === filter)?.params ?? {};
      const { data } = await getShareLinkEvents(linkId, params);
      setEvents(data.results);
    } catch {
      /* handled by the global interceptor */
    } finally {
      setLoading(false);
    }
  }, [linkId, filter]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{t('accessLog.title', { defaultValue: 'Access log' })}</h3>
        <select
          aria-label={t('accessLog.filter', { defaultValue: 'Filter events' })}
          className="rounded-md border bg-transparent p-1 text-sm"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          {FILTERS.map((f) => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
      </div>
      {!loading && events.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('accessLog.empty', { defaultValue: 'No events yet.' })}</p>
      ) : (
        <div className="max-h-72 overflow-auto rounded-md border">
          <table className="min-w-full text-xs">
            <thead className="bg-gray-50 text-left uppercase text-[10px] text-gray-500 dark:bg-gray-800/50">
              <tr>
                <th className="px-2 py-1">{t('accessLog.time', { defaultValue: 'Time' })}</th>
                <th className="px-2 py-1">{t('accessLog.event', { defaultValue: 'Event' })}</th>
                <th className="px-2 py-1">{t('accessLog.email', { defaultValue: 'Email' })}</th>
                <th className="px-2 py-1">IP</th>
              </tr>
            </thead>
            <tbody className="divide-y" data-testid="access-log-rows">
              {events.map((event) => (
                <tr key={event.id}>
                  <td className="whitespace-nowrap px-2 py-1">{new Date(event.created_at).toLocaleString()}</td>
                  <td className="px-2 py-1">{EVENT_LABELS[event.event_type] ?? event.event_type}</td>
                  <td className="px-2 py-1">{event.email || '-'}</td>
                  <td className="px-2 py-1 font-mono">{event.ip_address || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
