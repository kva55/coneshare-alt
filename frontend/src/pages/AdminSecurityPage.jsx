import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { AdminNav } from '../components/admin/AdminNav';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { Label } from '../components/ui/Label';
import { Textarea } from '../components/ui/Textarea';
import { getSecurityEvents, getSecuritySettings, saveSecuritySettings, sendSecurityTestAlert } from '../services/api';

const firstError = (err) => {
  const data = err.response?.data;
  if (!data) return null;
  if (typeof data === 'string') return data;
  const first = Object.values(data)[0];
  return Array.isArray(first) ? first[0] : String(first);
};

const ACTION_LABELS = {
  ignore: 'Ignore (do not even log)', log: 'Log only', notify: 'Log and e-mail me',
  blur: 'Log and cover the page until the viewer continues', end: 'Log and end the viewing',
};

const KIND_FILTERS = [
  { value: '', label: 'All' },
  { value: 'failed_auth_alert', label: 'Failed sign-in alerts' },
  { value: 'web_probe', label: 'Suspicious requests' },
  { value: 'settings_changed', label: 'Settings changes' },
];

function NumberField({ id, label, value, onChange, min = 1, max = 1000 }) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="number" min={min} max={max} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}

function Check({ id, label, checked, onChange }) {
  return (
    <label htmlFor={id} className="flex items-start gap-2 text-sm">
      <input id={id} type="checkbox" className="mt-1" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function EventsTable() {
  const { t } = useTranslation();
  const [kind, setKind] = useState('');
  const [rows, setRows] = useState([]);
  const load = useCallback(async () => {
    try { setRows((await getSecurityEvents(kind ? { kind } : {})).data.results); } catch { /* handled by the global interceptor */ }
  }, [kind]);
  useEffect(() => { load(); }, [load]);

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">{t('security.events', { defaultValue: 'Security log' })}</h2>
        <select aria-label="Filter security events" className="rounded-md border bg-transparent p-1 text-sm" value={kind} onChange={(e) => setKind(e.target.value)}>
          {KIND_FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
      </div>
      <div className="max-h-96 overflow-auto rounded-md border">
        <table className="min-w-full text-xs">
          <thead className="bg-gray-50 text-left text-[10px] uppercase text-gray-500 dark:bg-gray-800/50">
            <tr><th className="px-2 py-1">Time</th><th className="px-2 py-1">Kind</th><th className="px-2 py-1">Rule</th><th className="px-2 py-1">Matched</th><th className="px-2 py-1">Request</th><th className="px-2 py-1">Signed-in cookie</th><th className="px-2 py-1">IP</th></tr>
          </thead>
          <tbody className="divide-y" data-testid="security-events">
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="whitespace-nowrap px-2 py-1">{new Date(r.created_at).toLocaleString()}</td>
                <td className="px-2 py-1">{r.kind}</td>
                <td className="px-2 py-1">{r.rule || '-'}</td>
                <td className="px-2 py-1 font-mono">{r.detail?.matched || '-'}</td>
                <td className="px-2 py-1 font-mono">{r.method ? `${r.method} ${r.path}` : r.actor || '-'}</td>
                <td className="px-2 py-1">{r.kind === 'web_probe' ? (r.authenticated ? 'yes' : 'no') : '-'}</td>
                <td className="px-2 py-1 font-mono">{r.ip_address || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Global security settings: code-request limits, failed sign-in alerts, experimental probe logging. */
export function AdminSecurityPage() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState(null);
  const [groups, setGroups] = useState([]);
  const [capture, setCapture] = useState({ actions: [], events: [] });
  const [message, setMessage] = useState('');

  useEffect(() => {
    getSecuritySettings().then(({ data }) => { setSettings(data.settings); setGroups(data.groups || []); setCapture(data.capture || { actions: [], events: [] }); }).catch(() => {});
  }, []);

  if (!settings) return <div className="container mx-auto p-6"><AdminNav /></div>;

  const set = (key) => (value) => setSettings({ ...settings, [key]: value });
  const disabled = settings.probe_disabled_rules || [];
  const toggleRule = (id) => set('probe_disabled_rules')(disabled.includes(id) ? disabled.filter((r) => r !== id) : [...disabled, id]);
  const toggleGroup = (group) => {
    const ids = group.rules.map((r) => r.id);
    const allOn = ids.every((id) => !disabled.includes(id));
    set('probe_disabled_rules')(allOn ? [...new Set([...disabled, ...ids])] : disabled.filter((id) => !ids.includes(id)));
  };

  const save = async (e) => {
    e.preventDefault();
    setMessage('');
    try {
      const { data } = await saveSecuritySettings(settings);
      setSettings(data.settings);
      toast.success(t('security.saved', { defaultValue: 'Security settings saved.' }));
    } catch (err) {
      setMessage(firstError(err) || t('security.failed', { defaultValue: 'Could not save the settings.' }));
    }
  };
  const testAlert = async () => {
    setMessage('');
    try { setMessage((await sendSecurityTestAlert()).data.detail); } catch (err) { setMessage(firstError(err) || err.response?.data?.detail || 'Failed.'); }
  };

  return (
    <div className="container mx-auto max-w-4xl space-y-8 p-4 sm:p-6">
      <AdminNav />
      <div>
        <h1 className="text-2xl font-bold">{t('security.title', { defaultValue: 'Security' })}</h1>
        <p className="text-muted-foreground">{t('security.subtitle', { defaultValue: 'Global limits and alerts. Alerts are sent with the e-mail setup.' })}</p>
      </div>

      <form onSubmit={save} className="space-y-8" aria-label="Security settings">
        <section className="space-y-3 rounded-md border p-4">
          <h2 className="text-lg font-medium">{t('security.codes', { defaultValue: 'E-mailed codes and links' })}</h2>
          <p className="text-sm text-muted-foreground">{t('security.codesHint', { defaultValue: 'How many access codes or sign-in links can be requested (share links and file requests). Further requests are ignored and logged.' })}</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <NumberField id="per-email" label="Per e-mail address" value={settings.code_requests_per_email} onChange={set('code_requests_per_email')} max={100} />
            <NumberField id="per-ip" label="Per IP address" value={settings.code_requests_per_ip} onChange={set('code_requests_per_ip')} />
            <NumberField id="window" label="Within (minutes)" value={settings.code_request_window_minutes} onChange={set('code_request_window_minutes')} max={1440} />
          </div>
        </section>

        <section className="space-y-3 rounded-md border p-4" data-testid="nda-link-settings">
          <h2 className="text-lg font-medium">{t('security.ndaLink', { defaultValue: 'Link in the NDA window' })}</h2>
          <p className="text-sm text-muted-foreground">
            {t('security.ndaLinkHint', { defaultValue: 'Shown under the agreement text in every NDA window (share links and check-out), opening in a new tab. Use it for the Acceptable Use Policy page. Leave empty for no link. Must start with http:// or https://.' })}
          </p>
          <Label htmlFor="nda-link-url">{t('security.ndaLinkUrl', { defaultValue: 'Address' })}</Label>
          <Input id="nda-link-url" type="url" placeholder="https://policy.example.org/nda-aup.html" value={settings.nda_link_url || ''}
                 onChange={(e) => set('nda_link_url')(e.target.value)} />
          <Label htmlFor="nda-link-text">{t('security.ndaLinkText', { defaultValue: 'Link text' })}</Label>
          <Input id="nda-link-text" maxLength={120} value={settings.nda_link_text || ''} onChange={(e) => set('nda_link_text')(e.target.value)} />
        </section>

        <section className="space-y-3 rounded-md border p-4" data-testid="monitoring-settings">
          <h2 className="text-lg font-medium">{t('security.monitoring', { defaultValue: 'Monitoring and interaction analysis' })}</h2>
          <p className="text-sm text-muted-foreground">
            {t('security.monitoringHint', { defaultValue: 'On by default. Records viewer behaviour (print, copy, screenshot keys, focus changes), applies the responses set below, traces check-out cursor movement and feeds Interaction Activity and session reports. Turning it off does not stop access records (who asked, who was admitted or refused), failed-sign-in alerts or probe detection, but the responses to viewer signals (blur, end) stop working because the signals are no longer collected.' })}
          </p>
          <Check id="monitoring-enabled" label={t('security.monitoringEnabled', { defaultValue: 'Monitor viewer behaviour and file interactions' })}
                 checked={Boolean(settings.monitoring_enabled)} onChange={set('monitoring_enabled')} />
        </section>

        <section className="space-y-3 rounded-md border p-4" data-testid="login-notice-settings">
          <h2 className="text-lg font-medium">{t('security.loginNotice', { defaultValue: 'Notice before sign-in' })}</h2>
          <p className="text-sm text-muted-foreground">
            {t('security.loginNoticeHint', { defaultValue: 'Shown above the sign-in form, the check-out sign-in and the file request form, before anyone authenticates.' })}
          </p>
          <Check id="login-notice-enabled" label={t('security.loginNoticeEnabled', { defaultValue: 'Show the notice' })}
                 checked={Boolean(settings.login_notice_enabled)} onChange={set('login_notice_enabled')} />
          <Label htmlFor="login-notice-text">{t('security.loginNoticeText', { defaultValue: 'Notice text' })}</Label>
          <Textarea id="login-notice-text" rows={4} maxLength={2000} value={settings.login_notice_text || ''}
                    onChange={(e) => set('login_notice_text')(e.target.value)} />
        </section>

        <section className="space-y-3 rounded-md border p-4" data-testid="tracing-settings">
          <h2 className="text-lg font-medium">{t('security.tracing', { defaultValue: 'Check-out session reports' })}</h2>
          <p className="text-sm text-muted-foreground">
            {t('security.tracingHint', { defaultValue: 'While a file is checked out, record where the guest\'s cursor goes (as a position on the page, not pixels) and which page is on screen, so a report can show where they read. Nothing typed is recorded. Leave the sign-in notice on so guests are told their actions may be recorded.' })}
          </p>
          <Check id="checkout-tracing" label={t('security.tracingEnabled', { defaultValue: 'Record cursor movement during check-outs' })}
                 checked={Boolean(settings.checkout_tracing_enabled)} onChange={set('checkout_tracing_enabled')} />
        </section>

        <section className="space-y-3 rounded-md border p-4" data-testid="throttling">
          <h2 className="text-lg font-medium">{t('security.throttling', { defaultValue: 'Request throttling' })}</h2>
          <p className="text-sm text-muted-foreground">
            {t('security.throttlingHint', { defaultValue: 'Off by default. When on, a caller that makes more requests per minute than the limit is told to wait ("Too many requests"). Viewing a document makes many small requests (every page, tile and focus report), so it has its own, higher limit. Password attempts and e-mailed codes have their own limits that always apply.' })}
          </p>
          <Check id="throttling-enabled" label={t('security.throttlingEnabled', { defaultValue: 'Limit the number of requests' })}
                 checked={Boolean(settings.throttling_enabled)} onChange={set('throttling_enabled')} />
          {settings.throttling_enabled && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <NumberField id="limit-viewer" label="Viewing, per minute" value={settings.viewer_requests_per_minute} onChange={set('viewer_requests_per_minute')} min={60} max={100000} />
              <NumberField id="limit-user" label="Signed-in users, per minute" value={settings.user_requests_per_minute} onChange={set('user_requests_per_minute')} min={60} max={100000} />
              <NumberField id="limit-general" label="Everyone else, per minute" value={settings.general_requests_per_minute} onChange={set('general_requests_per_minute')} min={30} max={100000} />
            </div>
          )}
        </section>

        <section className="space-y-3 rounded-md border p-4" data-testid="capture-actions">
          <h2 className="text-lg font-medium">{t('security.capture', { defaultValue: 'Print, screenshot and save attempts' })}</h2>
          <p className="text-sm text-muted-foreground">
            {t('security.captureHint', { defaultValue: 'The viewer reports these when it sees them; you decide what happens. Logging is the default and never interrupts anyone. A page can only see keys and print events: menu commands, operating system screenshot tools and cameras are invisible to it.' })}
          </p>
          <table className="min-w-full text-sm">
            <tbody className="divide-y">
              {capture.events.map((event) => (
                <tr key={event.id}>
                  <td className="py-2 pr-3"><label htmlFor={`capture-${event.id}`}>{event.label}</label></td>
                  <td className="py-2">
                    <select id={`capture-${event.id}`} aria-label={event.label} className="rounded-md border bg-transparent p-1"
                            value={(settings.capture_actions || {})[event.id] ?? event.default}
                            onChange={(e) => set('capture_actions')({ ...(settings.capture_actions || {}), [event.id]: e.target.value })}>
                      {capture.actions.map((a) => <option key={a} value={a}>{ACTION_LABELS[a] || a}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <NumberField id="lockout" label="A viewer whose viewing is ended is locked out for (minutes)" value={settings.capture_lockout_minutes} onChange={set('capture_lockout_minutes')} max={1440} />
          <p className="text-xs text-muted-foreground">
            {t('security.captureNote', { defaultValue: '"E-mail me" sends one message per viewer and signal every 10 minutes to the alert address above. "End the viewing" ends a check-out, and locks a share-link viewer out for the time above.' })}
          </p>
        </section>

        <section className="space-y-3 rounded-md border p-4">
          <h2 className="text-lg font-medium">{t('security.alerts', { defaultValue: 'Alerts' })}</h2>
          <div className="space-y-1">
            <Label htmlFor="alert-group">{t('security.group', { defaultValue: 'Alert address (group mailbox)' })}</Label>
            <div className="flex gap-2">
              <Input id="alert-group" type="email" value={settings.alert_group_email} onChange={(e) => set('alert_group_email')(e.target.value)} placeholder="security@example.com" />
              <Button type="button" variant="outline" onClick={testAlert}>{t('security.testAlert', { defaultValue: 'Send test alert' })}</Button>
            </div>
          </div>
          <Check id="failed-enabled" label={t('security.failedEnabled', { defaultValue: 'Alert after repeated failed sign-ins (login, access codes, file request links)' })}
                 checked={settings.failed_auth_alerts_enabled} onChange={set('failed_auth_alerts_enabled')} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <NumberField id="threshold" label="Failed attempts" value={settings.failed_auth_threshold} onChange={set('failed_auth_threshold')} max={50} />
            <NumberField id="fail-window" label="Within (minutes)" value={settings.failed_auth_window_minutes} onChange={set('failed_auth_window_minutes')} max={1440} />
          </div>
          <Check id="notify-account" label={t('security.notifyAccount', { defaultValue: 'Also e-mail the account that was targeted' })}
                 checked={settings.alert_notify_account} onChange={set('alert_notify_account')} />
        </section>

        <section className="space-y-3 rounded-md border p-4">
          <h2 className="text-lg font-medium">{t('security.probes', { defaultValue: 'Suspicious requests (experimental)' })}</h2>
          <p className="text-sm text-muted-foreground">{t('security.probesHint', { defaultValue: 'Logs requests that look like probing: odd paths, traversal, injection attempts, scanners, areas that do not exist. It only logs; nothing is blocked.' })}</p>
          <Check id="probe-enabled" label={t('security.probeEnabled', { defaultValue: 'Detect and log suspicious requests' })}
                 checked={settings.probe_detection_enabled} onChange={set('probe_detection_enabled')} />
          {settings.probe_detection_enabled && (
            <div className="space-y-3 pl-6">
              <Check id="probe-auth" label={t('security.probeAuth', { defaultValue: 'Also log requests that carry a sign-in cookie (marked as such)' })}
                     checked={settings.probe_log_authenticated} onChange={set('probe_log_authenticated')} />
              <Check id="probe-unknown" label={t('security.probeUnknown', { defaultValue: 'Log requests for areas that do not exist (404)' })}
                     checked={settings.probe_log_unknown_areas} onChange={set('probe_log_unknown_areas')} />
              <Check id="probe-alert" label={t('security.probeAlert', { defaultValue: 'E-mail the alert address when a rule trips' })}
                     checked={settings.probe_alert_enabled} onChange={set('probe_alert_enabled')} />
              <NumberField id="cooldown" label="At most one alert per source every (minutes)" value={settings.probe_alert_cooldown_minutes} onChange={set('probe_alert_cooldown_minutes')} max={1440} />
              <fieldset className="space-y-3">
                <legend className="text-sm font-medium">{t('security.rules', { defaultValue: 'Rules (OWASP Core Rule Set families)' })}</legend>
                {groups.map((group) => {
                  const on = group.rules.filter((r) => !disabled.includes(r.id)).length;
                  return (
                    <div key={group.id} className="rounded-md border p-3" data-testid={`group-${group.id}`}>
                      <label htmlFor={`group-${group.id}-all`} className="flex items-start gap-2 text-sm font-medium">
                        <input id={`group-${group.id}-all`} type="checkbox" className="mt-1" checked={on === group.rules.length} onChange={() => toggleGroup(group)} />
                        <span>
                          {group.id !== 'APP' ? `${group.id} ` : ''}{group.name} <span className="font-normal text-muted-foreground">({on}/{group.rules.length})</span>
                          <span className="block text-xs font-normal text-muted-foreground">{group.description}</span>
                        </span>
                      </label>
                      <div className="mt-2 space-y-1 pl-6">
                        {group.rules.map((rule) => (
                          <Check key={rule.id} id={`rule-${rule.id}`} label={`${rule.id}: ${rule.description} [${rule.severity}]`}
                                 checked={!disabled.includes(rule.id)} onChange={() => toggleRule(rule.id)} />
                        ))}
                      </div>
                    </div>
                  );
                })}
              </fieldset>
            </div>
          )}
        </section>

        {message && <p role="status" className="text-sm">{message}</p>}
        <Button type="submit">{t('common.save', { defaultValue: 'Save' })}</Button>
      </form>

      <EventsTable />
    </div>
  );
}
