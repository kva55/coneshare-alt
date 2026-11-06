import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { Label } from '../components/ui/Label';
import { NdaTemplateButton } from '../components/common/NdaTemplateButton';
import { CheckoutSessionsPanel } from '../components/checkout/CheckoutSessionsPanel';
import {
  addCheckoutGuests,
  createCheckoutPolicy,
  decideCheckoutApproval,
  deleteCheckoutPolicy,
  getCheckoutApprovals,
  getCheckoutEvents,
  getCheckoutGuests,
  getCheckoutPolicies,
  getCheckoutPolicyFiles,
  revokeCheckoutGuest,
  setCheckoutPolicyFiles,
  updateCheckoutPolicy,
} from '../services/api';

export const EVENT_FILTERS = [
  { value: '', label: 'All events', types: '' },
  { value: 'auth', label: 'Sign-ins', types: 'auth_requested,login_link_sent,auth_granted,auth_denied,auth_throttled' },
  { value: 'denied', label: 'Denied', types: 'auth_denied,auth_failed,auth_throttled,checkout_denied,request_denied,download_denied' },
  { value: 'requests', label: 'Requests', types: 'file_requested,approval_required,request_approved,request_denied' },
  { value: 'out', label: 'Checked out', types: 'file_checked_out,checkout_returned,file_downloaded' },
];

const firstError = (err) => {
  const data = err.response?.data;
  if (!data) return null;
  if (typeof data === 'string') return data;
  const first = Object.values(data)[0];
  return Array.isArray(first) ? first[0] : String(first);
};

const TRI = { '': null, yes: true, no: false };
const triValue = (v) => (v === true ? 'yes' : v === false ? 'no' : '');

/** Yes / No / follow the datastore. */
function TriState({ label, value, onChange }) {
  return (
    <select aria-label={label} className="rounded-md border bg-transparent p-1 text-xs" value={triValue(value)}
            onChange={(e) => onChange(TRI[e.target.value])}>
      <option value="">Datastore default</option>
      <option value="yes">Yes</option>
      <option value="no">No</option>
    </select>
  );
}

function Approvals({ onChanged }) {
  const { t } = useTranslation();
  const [queue, setQueue] = useState({ pending: [], recent: [] });

  const load = useCallback(async () => {
    try { setQueue((await getCheckoutApprovals()).data); } catch { /* handled by the global interceptor */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  const decide = async (id, decision) => {
    try {
      await decideCheckoutApproval(id, decision);
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(firstError(err) || t('adminCheckout.failed', { defaultValue: 'That did not work.' }));
      load();
    }
  };

  return (
    <section className="space-y-3" aria-label="Approvals">
      <h2 className="text-lg font-medium">{t('adminCheckout.approvals', { defaultValue: 'Waiting for approval' })}</h2>
      {queue.pending.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('adminCheckout.noApprovals', { defaultValue: 'Nothing is waiting.' })}</p>
      ) : (
        <ul className="divide-y rounded-md border" data-testid="pending-approvals">
          {queue.pending.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
              <div>
                <div className="font-medium">{r.file_name} <span className="font-normal text-muted-foreground">· {r.datastore}</span></div>
                <div className="text-xs text-muted-foreground">{r.email} · {new Date(r.created_at).toLocaleString()}</div>
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => decide(r.id, 'approve')}>{t('adminCheckout.approve', { defaultValue: 'Approve' })}</Button>
                <Button size="sm" variant="outline" onClick={() => decide(r.id, 'deny')}>{t('adminCheckout.deny', { defaultValue: 'Deny' })}</Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {queue.recent.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">{t('adminCheckout.recent', { defaultValue: 'Recent decisions' })}</summary>
          <ul className="mt-2 divide-y rounded-md border">
            {queue.recent.map((r) => (
              <li key={r.id} className="flex justify-between gap-2 p-2">
                <span>{r.file_name} · {r.email}</span>
                <span className="text-muted-foreground">{r.status}{r.decided_by_email ? ` by ${r.decided_by_email}` : ''}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function GuestsEditor({ policyId, enabled, onChanged }) {
  const { t } = useTranslation();
  const [guests, setGuests] = useState([]);
  const [draft, setDraft] = useState('');

  const load = useCallback(async () => {
    try { setGuests((await getCheckoutGuests(policyId)).data.results); } catch { /* handled by the global interceptor */ }
  }, [policyId]);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    try {
      const { data } = await addCheckoutGuests(policyId, [draft]);
      if (data.warning) toast.warning(data.warning);
      toast.success(t('adminCheckout.guestsAdded', { count: data.added.length, defaultValue: '{{count}} guest(s) added and told by e-mail.' }));
      if (data.invalid.length) toast.error(t('adminCheckout.guestsInvalid', { list: data.invalid.join(', '), defaultValue: 'Not valid: {{list}}' }));
      setDraft('');
      await load();
      onChanged?.();
    } catch (err) {
      toast.error(firstError(err) || t('adminCheckout.failed', { defaultValue: 'That did not work.' }));
    }
  };
  const revoke = async (guest) => {
    try { await revokeCheckoutGuest(policyId, guest.id); await load(); onChanged?.(); } catch { /* handled by the global interceptor */ }
  };

  return (
    <div className="space-y-2">
      <Label htmlFor={`guests-${policyId}`}>{t('adminCheckout.guests', { defaultValue: 'Guests (can sign in by e-mail link)' })}</Label>
      <div className="flex gap-2">
        <textarea id={`guests-${policyId}`} rows={2} className="flex-1 rounded-md border bg-transparent p-2 text-sm"
                  placeholder="guest@example.com, other@example.com" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <Button type="button" onClick={add} disabled={!draft.trim()}>{t('adminCheckout.addGuests', { defaultValue: 'Add guests' })}</Button>
      </div>
      {guests.length > 0 && (
        <ul className="divide-y rounded-md border text-sm" data-testid="guests">
          {guests.map((g) => (
            <li key={g.id} className="flex items-center justify-between gap-2 px-3 py-2">
              <span className={g.is_active ? '' : 'line-through text-muted-foreground'}>{g.email}</span>
              {g.is_active && <Button type="button" variant="ghost" size="sm" onClick={() => revoke(g)}>{t('adminCheckout.revoke', { defaultValue: 'Revoke' })}</Button>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PolicyEditor({ policy, limits, onChanged }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    enabled: policy.enabled, checkout_minutes: policy.checkout_minutes, max_attempts: policy.max_attempts,
    allow_download: policy.allow_download, downloads_per_checkout: policy.downloads_per_checkout,
    require_nda: policy.require_nda, nda_text: policy.nda_text,
    notify_mode: policy.notify_mode, notify_email: policy.notify_email,
  });
  const [docs, setDocs] = useState([]);
  const set = (key) => (e) => setForm({ ...form, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });

  useEffect(() => {
    getCheckoutPolicyFiles(policy.id).then(({ data }) => setDocs(data.results)).catch(() => {});
  }, [policy.id]);

  // The on/off switch applies at once: it does not wait for Save (a datastore that looks set up but
  // is switched off silently refuses every guest).
  const setEnabled = async (enabled) => {
    try {
      await updateCheckoutPolicy(policy.id, { enabled });
      setForm((f) => ({ ...f, enabled }));
      toast.success(enabled
        ? t('adminCheckout.nowEnabled', { defaultValue: 'Enabled: guests can now sign in.' })
        : t('adminCheckout.nowDisabled', { defaultValue: 'Disabled: guests can no longer sign in.' }));
    } catch (err) {
      toast.error(firstError(err) || t('adminCheckout.failed', { defaultValue: 'That did not work.' }));
    }
  };

  const patchDoc = (id, change) => setDocs(docs.map((d) => (d.id === id ? { ...d, ...change, touched: true } : d)));

  const save = async () => {
    try {
      await updateCheckoutPolicy(policy.id, {
        enabled: form.enabled,
        checkout_minutes: Number(form.checkout_minutes),
        max_attempts: Number(form.max_attempts),
        allow_download: form.allow_download,
        downloads_per_checkout: Number(form.downloads_per_checkout),
        require_nda: form.require_nda,
        nda_text: form.nda_text,
        notify_mode: form.notify_mode,
        notify_email: form.notify_mode === 'group' ? form.notify_email : '',
      });
      await setCheckoutPolicyFiles(policy.id, docs.filter((d) => d.visible || d.configured || d.touched).map((d) => ({
        id: d.id, visible: d.visible, requires_approval: d.requires_approval, requires_nda: d.requires_nda, allow_download: d.allow_download,
      })));
      toast.success(t('adminCheckout.saved', { defaultValue: 'Saved.' }));
      onChanged();
    } catch (err) {
      toast.error(firstError(err) || t('adminCheckout.failed', { defaultValue: 'That did not work.' }));
    }
  };
  const remove = async () => {
    try { await deleteCheckoutPolicy(policy.id); onChanged(); } catch { /* handled by the global interceptor */ }
  };

  return (
    <div className="space-y-4 rounded-md border p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="font-medium">{policy.dataroom_name}</h3>
          <span data-testid="policy-status"
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${form.enabled ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
            {form.enabled
              ? t('adminCheckout.statusOn', { defaultValue: 'Enabled' })
              : t('adminCheckout.statusOff', { defaultValue: 'Disabled - guests cannot sign in' })}
          </span>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setEnabled(e.target.checked)} aria-label="Enabled" />
          {t('adminCheckout.enabled', { defaultValue: 'Enabled' })}
        </label>
      </div>

      {!form.enabled && (
        <p role="alert" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
          {t('adminCheckout.disabledWarning', { defaultValue: 'This datastore is not enabled: guests cannot sign in, and no sign-in link is e-mailed to them, even though they may already have an invitation.' })}{' '}
          <Button type="button" size="sm" onClick={() => setEnabled(true)}>{t('adminCheckout.enableNow', { defaultValue: 'Enable now' })}</Button>
        </p>
      )}
      <GuestsEditor policyId={policy.id} enabled={form.enabled} onChanged={onChanged} />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor={`minutes-${policy.id}`}>{t('adminCheckout.minutes', { max: limits.max_minutes, defaultValue: 'Viewing time (min, max {{max}})' })}</Label>
          <Input id={`minutes-${policy.id}`} type="number" min={1} max={limits.max_minutes} value={form.checkout_minutes} onChange={set('checkout_minutes')} />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`attempts-${policy.id}`}>{t('adminCheckout.attempts', { defaultValue: 'Max requests per guest (24 h)' })}</Label>
          <Input id={`attempts-${policy.id}`} type="number" min={1} max={limits.max_attempts} value={form.max_attempts} onChange={set('max_attempts')} />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`notify-${policy.id}`}>{t('adminCheckout.notify', { defaultValue: 'On check-out / approval needed' })}</Label>
          <select id={`notify-${policy.id}`} className="w-full rounded-md border bg-transparent p-2 text-sm" value={form.notify_mode} onChange={set('notify_mode')}>
            <option value="log">{t('adminCheckout.notifyLog', { defaultValue: 'Log internally only' })}</option>
            <option value="owner">{t('adminCheckout.notifyOwner', { defaultValue: 'Email the document owner' })}</option>
            <option value="group">{t('adminCheckout.notifyGroup', { defaultValue: 'Email a group address' })}</option>
          </select>
        </div>
      </div>
      {form.notify_mode === 'group' && (
        <div className="space-y-1">
          <Label htmlFor={`group-${policy.id}`}>{t('adminCheckout.groupEmail', { defaultValue: 'Group email' })}</Label>
          <Input id={`group-${policy.id}`} type="email" value={form.notify_email} onChange={set('notify_email')} />
        </div>
      )}

      <div className="space-y-2">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.require_nda} onChange={set('require_nda')} aria-label="Require NDA" />
          {t('adminCheckout.requireNda', { defaultValue: 'Guests must accept an NDA before viewing' })}
        </label>
        {form.require_nda && (
          <NdaTemplateButton value={form.nda_text} onUse={(text) => setForm({ ...form, nda_text: text })} />
        )}
        {form.require_nda && (
          <textarea aria-label="NDA text" rows={4} className="w-full rounded-md border bg-transparent p-2 text-sm"
                    value={form.nda_text} onChange={set('nda_text')} placeholder="Agreement text shown to guests" />
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.allow_download} onChange={set('allow_download')} aria-label="Allow download" />
          {t('adminCheckout.allowDownload', { defaultValue: 'Allow downloading (off: view in the browser only)' })}
        </label>
        {form.allow_download && (
          <div className="max-w-xs space-y-1">
            <Label htmlFor={`downloads-${policy.id}`}>{t('adminCheckout.downloads', { defaultValue: 'Downloads per check-out' })}</Label>
            <Input id={`downloads-${policy.id}`} type="number" min={1} max={5} value={form.downloads_per_checkout} onChange={set('downloads_per_checkout')} />
          </div>
        )}
      </div>

      <div className="space-y-2">
        <Label>{t('adminCheckout.files', { defaultValue: 'Files and their access settings' })}</Label>
        <p className="text-xs text-muted-foreground">
          {t('adminCheckout.filesHint', { defaultValue: 'These settings belong to the file in this datastore. No share link is needed: guests you add above see the files marked Visible. A new upload appears here at once and stays hidden until you make it visible.' })}
        </p>
        {docs.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('adminCheckout.noDocs', { defaultValue: 'This datastore has no files.' })}</p>
        ) : (
          <div className="max-h-80 overflow-auto rounded-md border">
            <table className="min-w-full text-sm" data-testid={`files-${policy.id}`}>
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500 dark:bg-gray-800/50">
                <tr>
                  <th className="px-2 py-1">File</th><th className="px-2 py-1">Visible</th>
                  <th className="px-2 py-1">Admin approval</th><th className="px-2 py-1">NDA</th><th className="px-2 py-1">Download</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {docs.map((d) => (
                  <tr key={d.id} className={d.visible ? '' : 'text-muted-foreground'}>
                    <td className="px-2 py-1">{d.name}{!d.configured && !d.touched && <span className="ml-2 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] text-blue-800">new</span>}</td>
                    <td className="px-2 py-1">
                      <input type="checkbox" aria-label={`Visible: ${d.name}`} checked={d.visible} onChange={(e) => patchDoc(d.id, { visible: e.target.checked })} />
                    </td>
                    <td className="px-2 py-1">
                      <input type="checkbox" aria-label={`Requires admin approval: ${d.name}`} checked={d.requires_approval}
                             onChange={(e) => patchDoc(d.id, { requires_approval: e.target.checked })} />
                    </td>
                    <td className="px-2 py-1">
                      <TriState label={`NDA: ${d.name}`} value={d.requires_nda} onChange={(v) => patchDoc(d.id, { requires_nda: v })} />
                    </td>
                    <td className="px-2 py-1">
                      <TriState label={`Download: ${d.name}`} value={d.allow_download} onChange={(v) => patchDoc(d.id, { allow_download: v })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <Button onClick={save}>{t('common.save', { defaultValue: 'Save' })}</Button>
        <Button variant="ghost" onClick={remove}>{t('adminCheckout.remove', { defaultValue: 'Remove check-out' })}</Button>
      </div>
    </div>
  );
}

function EventLog() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState('');
  const [events, setEvents] = useState([]);

  const load = useCallback(async () => {
    const types = EVENT_FILTERS.find((f) => f.value === filter)?.types;
    try {
      const { data } = await getCheckoutEvents(types ? { event_type: types } : {});
      setEvents(data.results);
    } catch { /* handled by the global interceptor */ }
  }, [filter]);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">{t('adminCheckout.log', { defaultValue: 'Check-out log' })}</h2>
        <select aria-label="Filter events" className="rounded-md border bg-transparent p-1 text-sm" value={filter} onChange={(e) => setFilter(e.target.value)}>
          {EVENT_FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
      </div>
      <div className="max-h-96 overflow-auto rounded-md border">
        <table className="min-w-full text-xs">
          <thead className="bg-gray-50 text-left text-[10px] uppercase text-gray-500 dark:bg-gray-800/50">
            <tr><th className="px-2 py-1">Time</th><th className="px-2 py-1">Event</th><th className="px-2 py-1">Email</th><th className="px-2 py-1">Datastore</th><th className="px-2 py-1">Details</th><th className="px-2 py-1">IP</th></tr>
          </thead>
          <tbody className="divide-y" data-testid="checkout-events">
            {events.map((e) => (
              <tr key={e.id}>
                <td className="whitespace-nowrap px-2 py-1">{new Date(e.created_at).toLocaleString()}</td>
                <td className="px-2 py-1">{e.event_type}</td>
                <td className="px-2 py-1">{e.email || '-'}</td>
                <td className="px-2 py-1">{e.datastore || '-'}</td>
                <td className="px-2 py-1 font-mono">{Object.keys(e.metadata || {}).length ? Object.entries(e.metadata).map(([k, v]) => `${k}=${v}`).join(' ') : '-'}</td>
                <td className="px-2 py-1 font-mono">{e.ip_address || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Check Out Center: which datastores offer files to which guests, and the access settings of each file. */
export function CheckOutCenterPage() {
  const { t } = useTranslation();
  const [data, setData] = useState({ results: [], datastores: [], limits: { max_minutes: 5, max_attempts: 10 } });
  const [newDatastore, setNewDatastore] = useState('');

  const load = useCallback(async () => {
    try { setData((await getCheckoutPolicies()).data); } catch { /* handled by the global interceptor */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  const used = new Set(data.results.map((p) => p.dataroom));
  const free = data.datastores.filter((d) => !used.has(d.id));

  const create = async () => {
    if (!newDatastore) return;
    try { await createCheckoutPolicy({ dataroom: newDatastore }); setNewDatastore(''); await load(); } catch { /* handled by the global interceptor */ }
  };

  return (
    <div className="container mx-auto space-y-6 p-4 sm:p-6">
      <div>
        <h1 className="text-2xl font-bold">{t('adminCheckout.title', { defaultValue: 'Check Out Center' })}</h1>
        <p className="text-muted-foreground">
          {t('adminCheckout.subtitle', { defaultValue: 'Guests you add get an e-mailed sign-in link, see the datastores they were given, and request files. Files are viewed in the app and are never shared as links; their access settings are set here.' })}
        </p>
      </div>

      <Approvals onChanged={load} />

      {data.results.map((policy) => (
        <PolicyEditor key={`${policy.id}-${policy.updated_at}`} policy={policy} limits={data.limits} onChanged={load} />
      ))}

      {free.length > 0 && (
        <div className="flex items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor="new-datastore">{t('adminCheckout.addDatastore', { defaultValue: 'Offer a datastore for file requests' })}</Label>
            <select id="new-datastore" className="rounded-md border bg-transparent p-2 text-sm" value={newDatastore} onChange={(e) => setNewDatastore(e.target.value)}>
              <option value="">-</option>
              {free.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </div>
          <Button onClick={create} disabled={!newDatastore}>{t('adminCheckout.add', { defaultValue: 'Add' })}</Button>
        </div>
      )}

      <CheckoutSessionsPanel />

      <EventLog />
    </div>
  );
}
