import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { getCheckoutSessions } from '../../services/api';
import { Input } from '../ui/Input';

/** Past and current check-outs, each with a link to its session report. */
export function CheckoutSessionsPanel() {
  const { t } = useTranslation();
  const [rows, setRows] = useState([]);
  const [email, setEmail] = useState('');

  useEffect(() => {
    let live = true;
    Promise.resolve().then(() => getCheckoutSessions(email ? { email } : {})).then((r) => live && setRows(r.data.results)).catch(() => {});
    return () => { live = false; };
  }, [email]);

  return (
    <section className="space-y-3" aria-label="Sessions" data-testid="sessions">
      <div className="flex items-end justify-between gap-3">
        <h2 className="text-lg font-medium">{t('adminCheckout.sessions', { defaultValue: 'Sessions and reports' })}</h2>
        <Input className="max-w-xs" placeholder="Filter by e-mail" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="Filter by e-mail" />
      </div>
      {rows.length === 0 ? <p className="text-sm text-muted-foreground">{t('adminCheckout.noSessions', { defaultValue: 'No file has been opened yet.' })}</p> : (
        <ul className="divide-y rounded-md border text-sm">
          {rows.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 p-2">
              <span className="min-w-0 truncate">{r.email} · {r.file} · {new Date(r.opened_at).toLocaleString()} · {r.status}</span>
              <Link className="text-indigo-600 hover:underline" to={`/check-out-center/sessions/${r.id}`}>
                {r.has_report ? t('adminCheckout.openReport', { defaultValue: 'Session report' }) : t('adminCheckout.openReportNoTrace', { defaultValue: 'Report (no movement recorded)' })}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
