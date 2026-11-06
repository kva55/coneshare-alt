import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  createWebhook,
  deleteWebhook,
  getWebhookDeliveries,
  getWebhookEvents,
  getWebhookSecret,
  getWebhooks,
  rotateWebhookSecret,
  testWebhook,
  updateWebhook,
} from '../../services/api';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Label } from '../ui/Label';

const firstError = (err) => {
  const data = err.response?.data;
  if (!data) return null;
  if (typeof data === 'string') return data;
  const first = Object.values(data)[0];
  return Array.isArray(first) ? first[0] : String(first);
};

function EventPicker({ events, value, onChange }) {
  return (
    <div className="space-y-1">
      {events.map((event) => (
        <label key={event.name} className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={value.includes(event.name)}
            onChange={(e) => onChange(e.target.checked ? [...value, event.name] : value.filter((n) => n !== event.name))}
          />
          <span><code>{event.name}</code> - {event.description}</span>
        </label>
      ))}
    </div>
  );
}

function WebhookRow({ hook, events, onChanged }) {
  const { t } = useTranslation();
  const [secret, setSecret] = useState('');
  const [deliveries, setDeliveries] = useState(null);

  const act = async (fn, done) => {
    try {
      const result = await fn();
      if (done) toast.success(done);
      return result;
    } catch (err) {
      toast.error(firstError(err) || t('webhooks.failed', { defaultValue: 'Something went wrong.' }));
      return null;
    }
  };

  return (
    <li className="space-y-3 rounded-md border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium">{hook.name}</div>
          <div className="truncate font-mono text-xs text-muted-foreground">{hook.url}</div>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            aria-label={`Active ${hook.name}`}
            checked={hook.is_active}
            onChange={async (e) => { await act(() => updateWebhook(hook.id, { is_active: e.target.checked })); onChanged(); }}
          />
          {t('webhooks.active', { defaultValue: 'Active' })}
        </label>
      </div>

      <EventPicker
        events={events}
        value={hook.events}
        onChange={async (next) => { await act(() => updateWebhook(hook.id, { events: next })); onChanged(); }}
      />

      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline"
          onClick={() => act(() => testWebhook(hook.id), t('webhooks.testQueued', { defaultValue: 'Test event queued.' }))}>
          {t('webhooks.test', { defaultValue: 'Send test' })}
        </Button>
        <Button type="button" size="sm" variant="outline"
          onClick={async () => { const r = await act(() => getWebhookSecret(hook.id)); if (r) setSecret(r.data.signing_secret); }}>
          {t('webhooks.reveal', { defaultValue: 'Show signing secret' })}
        </Button>
        <Button type="button" size="sm" variant="outline"
          onClick={async () => { const r = await act(() => rotateWebhookSecret(hook.id)); if (r) setSecret(r.data.signing_secret); }}>
          {t('webhooks.rotate', { defaultValue: 'Rotate secret' })}
        </Button>
        <Button type="button" size="sm" variant="outline"
          onClick={async () => { const r = await act(() => getWebhookDeliveries(hook.id)); if (r) setDeliveries(r.data.results); }}>
          {t('webhooks.deliveries', { defaultValue: 'Deliveries' })}
        </Button>
        <Button type="button" size="sm" variant="ghost"
          onClick={async () => { await act(() => deleteWebhook(hook.id)); onChanged(); }}>
          {t('webhooks.delete', { defaultValue: 'Delete' })}
        </Button>
      </div>

      {secret && (
        <p className="break-all rounded bg-gray-50 p-2 font-mono text-xs dark:bg-gray-800/50" data-testid="signing-secret">{secret}</p>
      )}
      {deliveries && (
        deliveries.length === 0
          ? <p className="text-sm text-muted-foreground">{t('webhooks.noDeliveries', { defaultValue: 'No deliveries yet.' })}</p>
          : (
            <table className="min-w-full text-xs" data-testid="deliveries">
              <tbody className="divide-y">
                {deliveries.map((d) => (
                  <tr key={d.id}>
                    <td className="py-1">{new Date(d.created_at).toLocaleString()}</td>
                    <td className="py-1">{d.event_type}</td>
                    <td className="py-1">{d.status}{d.response_code ? ` (${d.response_code})` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
      )}
    </li>
  );
}

/** Generic signed webhooks tied to events (file requested / checked out / ...). Admin only. */
export function WebhookManager() {
  const { t } = useTranslation();
  const [hooks, setHooks] = useState([]);
  const [events, setEvents] = useState([]);
  const [form, setForm] = useState({ name: '', url: '', events: [] });
  const [created, setCreated] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [h, e] = await Promise.all([getWebhooks(), getWebhookEvents()]);
      setHooks(h.data.results);
      setEvents(e.data.events);
    } catch {
      /* handled by the global interceptor */
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const create = async (e) => {
    e.preventDefault();
    setError('');
    try {
      const { data } = await createWebhook(form);
      setCreated(data.signing_secret);
      setForm({ name: '', url: '', events: [] });
      await load();
    } catch (err) {
      setError(firstError(err) || t('webhooks.failed', { defaultValue: 'Something went wrong.' }));
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">{t('webhooks.title', { defaultValue: 'Webhooks' })}</h2>
        <p className="text-sm text-muted-foreground">
          {t('webhooks.hint', { defaultValue: 'A signed JSON POST is sent to your URL when a subscribed event happens. Verify the X-Coneshare-Signature header (HMAC-SHA256 of the raw body with your signing secret).' })}
        </p>
      </div>

      {hooks.length > 0 && (
        <ul className="space-y-3" data-testid="webhooks">
          {hooks.map((hook) => <WebhookRow key={hook.id} hook={hook} events={events} onChanged={load} />)}
        </ul>
      )}

      <form onSubmit={create} className="space-y-3 rounded-md border p-4" aria-label="Add webhook">
        <h3 className="font-medium">{t('webhooks.add', { defaultValue: 'Add a webhook' })}</h3>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="hook-name">{t('webhooks.name', { defaultValue: 'Name' })}</Label>
            <Input id="hook-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="hook-url">URL</Label>
            <Input id="hook-url" type="url" required placeholder="https://example.com/hooks/coneshare" value={form.url}
                   onChange={(e) => setForm({ ...form, url: e.target.value })} />
          </div>
        </div>
        <EventPicker events={events} value={form.events} onChange={(next) => setForm({ ...form, events: next })} />
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        <Button type="submit">{t('webhooks.create', { defaultValue: 'Create webhook' })}</Button>
        {created && (
          <p className="text-sm">
            {t('webhooks.secretNote', { defaultValue: 'Signing secret (also available later):' })}{' '}
            <code className="break-all" data-testid="created-secret">{created}</code>
          </p>
        )}
      </form>
    </div>
  );
}
